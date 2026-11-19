"""Módulo 1 — Pesquisa, anti-duplicidade e roteirização estruturada."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .. import log, metadados, util
from ..canal import Canal
from ..historico import Historico, ResultadoDuplicidade
from ..llm import ErroLLM, obter_cliente
from ..pesquisa import contexto_markdown, pesquisar
from ..projeto import Projeto

MODOS = ("cartao_animado", "video_banco", "imagem_ia", "ilustracao_2d")
TIPOS_CARTAO = ("titulo", "contador", "lista", "grafico", "citacao")

DESCRICAO_ESTILO = {
    "explicativo": "cartões animados, contadores, listas e gráficos dinâmicos intercalados com clipes",
    "documentario": "clipes reais de bancos gratuitos com movimento suave de câmera, poucos cartões",
    "animacao_2d": "cenas ilustradas 2D em camadas com parallax, intercaladas com cartões",
    "tipografia": "tipografia minimalista: a narração aparece como texto cinético na tela",
}
PREFERENCIA_ESTILO = {
    "explicativo": "Neste canal use cartao_animado em ~45% dos blocos e video_banco/imagem_ia no restante.",
    "documentario": "Neste canal use video_banco em ~60% dos blocos, imagem_ia em ~25% e cartao_animado só para dados.",
    "animacao_2d": "Neste canal use ilustracao_2d em ~60% dos blocos e cartao_animado para dados.",
    "tipografia": "Neste canal quase tudo vira tipografia; use cartao_animado com tipo titulo, e contador/lista/grafico só para dados.",
}

_CARTAO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"}, "subtitulo": {"type": "string"}, "numero": {"type": "string"},
        "unidade": {"type": "string"}, "itens": {"type": "array", "items": {"type": "string"}},
        "barras": {"type": "array", "items": {
            "type": "object", "properties": {"rotulo": {"type": "string"}, "valor": {"type": "number"}},
            "required": ["rotulo", "valor"], "additionalProperties": False}},
        "autor": {"type": "string"},
    },
    "required": ["titulo", "subtitulo", "numero", "unidade", "itens", "barras", "autor"],
    "additionalProperties": False,
}
_BLOCO = {
    "type": "object",
    "properties": {
        "narracao": {"type": "string"},
        "modo_visual": {"type": "string", "enum": list(MODOS)},
        "tipo_cartao": {"type": "string", "enum": list(TIPOS_CARTAO) + ["nenhum"]},
        "dados_cartao": _CARTAO,
        "busca_visual": {"type": "string"},
        "destaque": {"type": "string"},
        "potencial_short": {"type": "boolean"},
        "capitulo": {"type": "string"},
    },
    "required": ["narracao", "modo_visual", "tipo_cartao", "dados_cartao", "busca_visual", "destaque",
                 "potencial_short", "capitulo"],
    "additionalProperties": False,
}
ESQUEMA_ROTEIRO = {
    "type": "object",
    "properties": {
        "titulo_trabalho": {"type": "string"},
        "resumo": {"type": "string"},
        "palavras_chave": {"type": "array", "items": {"type": "string"}},
        "blocos": {"type": "array", "items": _BLOCO},
        "seo": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string"}, "descricao": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "hashtags": {"type": "array", "items": {"type": "string"}},
                "frases_thumbnail": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["titulo", "descricao", "tags", "hashtags", "frases_thumbnail"],
            "additionalProperties": False,
        },
        "fontes_citadas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titulo_trabalho", "resumo", "palavras_chave", "blocos", "seo", "fontes_citadas"],
    "additionalProperties": False,
}


class TemaDuplicado(RuntimeError):
    def __init__(self, resultado: ResultadoDuplicidade):
        self.resultado = resultado
        parecidos = "; ".join(f"{r['tema']} ({p:.0%})" for r, p in resultado.similares[:3])
        super().__init__(f"Tema bloqueado por duplicidade: {resultado.motivo} Parecidos: {parecidos}")


class ErroRoteiro(RuntimeError):
    pass


# --------------------------------------------------------------------- prompts
def _limites(canal: Canal) -> tuple[int, int]:
    wps = canal.palavras_por_segundo
    return int(math.ceil(canal.ritmo["bloco_min_seg"] * wps)), int(canal.ritmo["bloco_max_seg"] * wps)


def instrucoes_sistema(canal: Canal, roteiro_bruto: bool = False, palavras_texto: int = 0) -> str:
    ed, ritmo = canal.editorial, canal.ritmo
    min_w, max_w = _limites(canal)
    media_seg = (ritmo["bloco_min_seg"] + ritmo["bloco_max_seg"]) / 2
    if roteiro_bruto:
        duracao = max(1.0, palavras_texto / canal.palavras_por_segundo / 60)
        regra_texto = ("O usuário enviou um ROTEIRO BRUTO: use-o como narração preservando o texto quase literalmente "
                       "(apenas correções mínimas de pontuação e números por extenso). Não acrescente nem remova "
                       "informações; apenas segmente nos blocos e preencha os campos visuais e de SEO.")
    else:
        duracao = float(ed.get("duracao_alvo_min", 8))
        regra_texto = (f"Abra com um gancho forte nos 2 primeiros blocos e feche com a chamada: "
                       f"\"{ed.get('chamada_final', '')}\".")
    n_blocos = max(4, int(round(duracao * 60 / media_seg)))
    evitar = ed.get("evitar") or []
    linha_evitar = f"\nNunca aborde: {', '.join(evitar)}." if evitar else ""
    return f"""Você é roteirista-chefe e editor de vídeo do canal "{canal.nome}" no YouTube.
Nicho: {canal.nicho}. Público: {canal.cfg['canal'].get('publico', '')}.
Tom de narração: {ed.get('tom_narracao', '')}.
Estilo visual predominante: {canal.estilo} ({DESCRICAO_ESTILO[canal.estilo]}).
Escreva SEMPRE em português do Brasil, com frases curtas e naturais para narração em voz alta.

Regras obrigatórias:
1. Divida a narração em blocos de {ritmo['bloco_min_seg']} a {ritmo['bloco_max_seg']} segundos (≈{min_w} a {max_w} palavras por bloco, a {canal.palavras_por_segundo:.1f} palavras por segundo).
2. Duração total alvo ≈ {duracao:.1f} minutos (≈{n_blocos} blocos). {regra_texto}
3. Use apenas fatos sustentados pelas fontes fornecidas; quando algo for incerto, deixe isso claro na narração. Nunca invente números, datas ou citações.
4. modo_visual de cada bloco: "cartao_animado" (dados, números, listas, comparações, citações), "video_banco" (cenas reais filmáveis), "imagem_ia" (cenas históricas ou impossíveis de filmar), "ilustracao_2d" (conceitos abstratos ou narrativa ilustrada). {PREFERENCIA_ESTILO[canal.estilo]}
5. Para cartao_animado escolha tipo_cartao: titulo | contador (preencha numero e unidade) | lista (3 a 5 itens curtos) | grafico (2 a 6 barras com rotulo e valor numérico) | citacao (frase em titulo e autor). Para os outros modos use tipo_cartao "nenhum" e dados_cartao com campos vazios.
6. busca_visual em INGLÊS: para video_banco, 2 a 5 palavras concretas e filmáveis (ex.: "foggy forest drone"); para imagem_ia, uma descrição visual detalhada da cena, sem texto na imagem; para ilustracao_2d, cenário e objeto principal.
7. destaque: frase de impacto com no máximo 6 palavras, exibida na tela.
8. potencial_short: true nos trechos com gancho, revelação ou dado surpreendente. Marque sequências de 4 a 7 blocos consecutivos que formem uma mini-história completa de 30 a 58 segundos (até 3 sequências).
9. capitulo: título curto (2 a 5 palavras) apenas no bloco que inicia um capítulo; "" nos demais. O primeiro bloco SEMPRE inicia um capítulo. Crie de 4 a 8 capítulos.
10. Siglas pouco conhecidas devem ser explicadas; evite parênteses e símbolos na narração.

SEO: título com até 70 caracteres, curioso e honesto (sem clickbait enganoso); descrição com 2 a 3 parágrafos; 12 a 20 tags; 3 hashtags; frases_thumbnail: exatamente 3 opções de 2 a 4 palavras em MAIÚSCULAS, cada uma com UMA palavra entre *asteriscos* para destaque.
fontes_citadas: URLs das fontes efetivamente usadas.{linha_evitar}"""


def mensagem_usuario(tema: str, roteiro_bruto: str | None, pesquisa: dict) -> str:
    partes = [f"TEMA: {tema}"]
    if roteiro_bruto:
        partes.append(f"ROTEIRO BRUTO DO USUÁRIO (preserve o texto):\n\"\"\"\n{roteiro_bruto.strip()}\n\"\"\"")
    partes.append("FONTES PESQUISADAS (use para checar fatos e citar):\n" + contexto_markdown(pesquisa))
    return "\n\n".join(partes)


# ------------------------------------------------------------- normalização
def _dividir_balanceado(partes: list[str]) -> tuple[str, str]:
    total = sum(util.contar_palavras(p) for p in partes)
    melhor, indice, acumulado = None, 1, 0
    for i in range(1, len(partes)):
        acumulado += util.contar_palavras(partes[i - 1])
        diferenca = abs(acumulado - total / 2)
        if melhor is None or diferenca < melhor:
            melhor, indice = diferenca, i
    return " ".join(partes[:indice]), " ".join(partes[indice:])


def _dividir_texto(texto: str, max_w: int, min_w: int = 0) -> list[str]:
    total = util.contar_palavras(texto)
    if total <= max_w:
        return [texto]
    frases = util.dividir_frases(texto)
    if len(frases) > 1:
        a, b = _dividir_balanceado(frases)
    else:
        trechos = re.split(r"(?<=[,;:—])\s+", texto)
        if len(trechos) > 1:
            a, b = _dividir_balanceado(trechos)
        else:
            palavras = texto.split()
            meio = len(palavras) // 2
            a, b = " ".join(palavras[:meio]), " ".join(palavras[meio:])
    # um pouco acima do máximo, mas a divisão geraria um trecho curto demais: mantém inteiro
    if total <= max_w * 1.2 and min(util.contar_palavras(a), util.contar_palavras(b)) < min_w:
        return [texto]
    return _dividir_texto(a, max_w, min_w) + _dividir_texto(b, max_w, min_w)


def _numero_no_texto(texto: str) -> tuple[str, str]:
    """Primeiro número "contável" do texto (anos como 1900 não viram contador)."""
    for m in re.finditer(r"(\d[\d.,]*)\s*(%|mil\b|milh\w+|bilh\w+|anos|metros|km|quil\w+|pessoas|vezes|dias|horas)?", texto):
        numero = m.group(1).rstrip(".,")
        if re.fullmatch(r"1\d{3}|20\d{2}", numero) and not m.group(2):
            continue
        return numero, (m.group(2) or "")
    return "", ""


def _bloco_padrao(bruto: dict) -> dict:
    cartao = dict(bruto.get("dados_cartao") or {})
    for chave, vazio in (("titulo", ""), ("subtitulo", ""), ("numero", ""), ("unidade", ""), ("itens", []),
                         ("barras", []), ("autor", "")):
        cartao.setdefault(chave, vazio)
    return {
        "narracao": re.sub(r"\s+", " ", str(bruto.get("narracao", ""))).strip(),
        "texto_fonetico": bruto.get("texto_fonetico") or None,
        "modo_visual": bruto.get("modo_visual", ""),
        "tipo_cartao": bruto.get("tipo_cartao", "nenhum"),
        "dados_cartao": cartao,
        "busca_visual": str(bruto.get("busca_visual", "")).strip(),
        "destaque": str(bruto.get("destaque", "")).strip(),
        "potencial_short": bool(bruto.get("potencial_short", False)),
        "capitulo": str(bruto.get("capitulo", "") or "").strip(),
        "midia_variante": int(bruto.get("midia_variante", 0) or 0),
        "midia_arquivo": bruto.get("midia_arquivo") or None,
    }


def _modo_padrao(estilo: str, indice: int) -> str:
    ciclos = {
        "explicativo": ["cartao_animado", "video_banco", "cartao_animado", "imagem_ia"],
        "documentario": ["video_banco", "video_banco", "imagem_ia", "video_banco", "cartao_animado"],
        "animacao_2d": ["ilustracao_2d", "ilustracao_2d", "cartao_animado", "ilustracao_2d"],
        "tipografia": ["cartao_animado"],
    }[estilo]
    return ciclos[indice % len(ciclos)]


def normalizar_blocos(brutos: list[dict], canal: Canal) -> list[dict]:
    min_w, max_w = _limites(canal)
    blocos: list[dict] = []
    for bruto in brutos:
        base = _bloco_padrao(bruto)
        if not base["narracao"]:
            continue
        pedacos = _dividir_texto(base["narracao"], max_w, min_w)
        for i, pedaco in enumerate(pedacos):
            novo = json.loads(json.dumps(base))
            novo["narracao"] = pedaco
            if i > 0:
                novo["capitulo"] = ""
                novo["destaque"] = ""
                novo["texto_fonetico"] = None
            blocos.append(novo)
    # junta blocos curtos demais
    i = 0
    while i < len(blocos):
        atual = blocos[i]
        palavras = util.contar_palavras(atual["narracao"])
        if palavras < min_w and len(blocos) > 1:
            vizinho = i + 1 if i + 1 < len(blocos) else i - 1
            alvo = blocos[vizinho]
            if palavras + util.contar_palavras(alvo["narracao"]) <= max_w * 1.15:
                primeiro, segundo = (atual, alvo) if vizinho > i else (alvo, atual)
                principal = primeiro if util.contar_palavras(primeiro["narracao"]) >= util.contar_palavras(segundo["narracao"]) else segundo
                fundido = json.loads(json.dumps(principal))
                fundido["narracao"] = f"{primeiro['narracao']} {segundo['narracao']}"
                fundido["texto_fonetico"] = None
                fundido["potencial_short"] = primeiro["potencial_short"] or segundo["potencial_short"]
                fundido["capitulo"] = primeiro["capitulo"] or segundo["capitulo"]
                fundido["destaque"] = primeiro["destaque"] or segundo["destaque"]
                indice = min(i, vizinho)
                blocos[indice:indice + 2] = [fundido]
                i = max(0, indice - 1) if vizinho < i else indice
                continue
        i += 1
    # campos visuais
    for indice, bloco in enumerate(blocos):
        bloco["id"] = indice + 1
        if bloco["modo_visual"] not in MODOS:
            bloco["modo_visual"] = _modo_padrao(canal.estilo, indice)
        cartao = bloco["dados_cartao"]
        if bloco["modo_visual"] == "cartao_animado":
            tipo = bloco["tipo_cartao"] if bloco["tipo_cartao"] in TIPOS_CARTAO else "titulo"
            if tipo == "contador" and not re.search(r"\d", str(cartao.get("numero", ""))):
                numero, unidade = _numero_no_texto(bloco["narracao"])
                cartao["numero"], cartao["unidade"] = numero, cartao.get("unidade") or unidade
                tipo = "contador" if numero else "titulo"
            if tipo == "lista" and len([x for x in cartao.get("itens", []) if str(x).strip()]) < 2:
                tipo = "titulo"
            if tipo == "grafico":
                barras = [b for b in cartao.get("barras", []) if isinstance(b, dict) and "valor" in b]
                cartao["barras"] = barras[:6]
                if len(barras) < 2:
                    tipo = "titulo"
            bloco["tipo_cartao"] = tipo
            if not cartao.get("titulo"):
                cartao["titulo"] = bloco["destaque"] or util.limitar(bloco["narracao"], 60)
        else:
            bloco["tipo_cartao"] = "nenhum"
        if not bloco["busca_visual"]:
            bloco["busca_visual"] = " ".join(util.palavras_chave(bloco["narracao"], 4))
        destaque = bloco["destaque"] or " ".join(p[:1].upper() + p[1:] for p in util.palavras_chave_originais(bloco["narracao"], 3))
        palavras = destaque.split()
        bloco["destaque"] = " ".join(palavras[:7]).strip(" .")
    if blocos and not blocos[0]["capitulo"]:
        blocos[0]["capitulo"] = "Introdução"
    if sum(1 for b in blocos if b["capitulo"]) < 3 and len(blocos) >= 9:
        for fracao in (1 / 3, 2 / 3):
            bloco = blocos[int(len(blocos) * fracao)]
            if not bloco["capitulo"]:
                bloco["capitulo"] = util.limitar(bloco["destaque"] or "Parte seguinte", 40, "")
    if not any(b["potencial_short"] for b in blocos):
        for bloco in blocos:
            texto = bloco["narracao"]
            if re.search(r"\d|\?|nunca|ningu[eé]m|segredo|maior|incr[ií]vel|verdade", texto, re.I):
                bloco["potencial_short"] = True
    return blocos


def _normalizar_seo(seo: dict, blocos: list[dict], tema: str, palavras: list[str]) -> dict:
    titulo = str(seo.get("titulo") or tema).strip()
    frases = [str(f).strip().upper().replace("**", "*") for f in seo.get("frases_thumbnail", []) if str(f).strip()]
    reservas = [b["destaque"] for b in blocos if b["potencial_short"]] + [b["destaque"] for b in blocos]
    for reserva in reservas:
        if len(frases) >= 3:
            break
        palavras_reserva = reserva.upper().split()[:4]
        if len(palavras_reserva) >= 2:
            palavras_reserva[-1] = f"*{palavras_reserva[-1]}*"
            candidata = " ".join(palavras_reserva)
            if candidata not in frases:
                frases.append(candidata)
    while len(frases) < 3:
        frases.append(["A VERDADE *OCULTA*", "NINGUÉM *EXPLICA*", "O QUE *ACONTECEU*?"][len(frases)])
    frases = [f if "*" in f else " ".join(f.split()[:-1] + [f"*{f.split()[-1]}*"]) for f in frases[:3]]
    tags = [str(t).strip().lstrip("#") for t in seo.get("tags", []) if str(t).strip()] or palavras
    hashtags = [str(h).strip().lstrip("#").replace(" ", "") for h in seo.get("hashtags", []) if str(h).strip()]
    capitulos = [{"bloco": b["id"], "titulo": b["capitulo"]} for b in blocos if b["capitulo"]]
    return {"titulo": util.limitar(titulo, 100, ""), "descricao": str(seo.get("descricao", "")).strip(),
            "tags": tags[:30],
            "hashtags": hashtags[:5] or ([util.slugify(palavras[0], 30).replace("-", "")] if palavras else []),
            "frases_thumbnail": frases, "capitulos": capitulos}


# ------------------------------------------------------ geração do roteiro
def gerar_com_llm(llm, canal: Canal, tema: str, roteiro_bruto: str | None, pesquisa: dict) -> dict:
    log.info(f"Gerando roteiro com {llm.descricao}…")
    sistema = instrucoes_sistema(canal, bool(roteiro_bruto), util.contar_palavras(roteiro_bruto or ""))
    return llm.gerar_json(sistema, mensagem_usuario(tema, roteiro_bruto, pesquisa), ESQUEMA_ROTEIRO, max_tokens=64000)


def _texto_das_fontes(pesquisa: dict, palavras_alvo: int) -> str:
    frases: list[str] = []
    for fonte in pesquisa.get("fontes", []):
        texto = fonte.get("texto") or ""
        for linha in texto.splitlines():
            linha = linha.strip()
            if len(linha) < 60 or linha.startswith(("=", "|", "Ver também", "Referências")):
                continue
            for frase in util.dividir_frases(linha):
                frase = re.sub(r"\s*\([^)]{0,80}\)", "", frase).strip()
                frase = re.sub(r"\[\d+\]", "", frase)
                if 40 <= len(frase) <= 260 and frase not in frases:
                    frases.append(frase)
            if sum(util.contar_palavras(f) for f in frases) >= palavras_alvo:
                return " ".join(frases)
    return " ".join(frases)


def gerar_heuristico(canal: Canal, tema: str, roteiro_bruto: str | None, pesquisa: dict) -> dict:
    """Roteiro sem LLM: segmenta o texto (do usuário ou extraído das fontes) e anota as cenas."""
    log.aviso("Nenhum modelo de linguagem disponível: usando o gerador heurístico (qualidade básica).")
    if roteiro_bruto:
        texto = roteiro_bruto
    else:
        alvo = int(canal.editorial.get("duracao_alvo_min", 8) * 60 * canal.palavras_por_segundo)
        corpo = _texto_das_fontes(pesquisa, alvo)
        if not corpo:
            raise ErroRoteiro("Sem modelo de linguagem e sem fontes encontradas para escrever o roteiro. "
                              "Configure uma chave de LLM no .env, envie um roteiro pronto ou use --modo-agente.")
        # "O mistério do voo MH370" vira "o mistério do voo MH370" no meio da frase
        tema_frase = tema[:1].lower() + tema[1:] if re.match(r"(O|A|Os|As|Um|Uma)\s", tema) else tema
        texto = (f"Você já ouviu falar sobre {tema_frase}? Hoje vamos descobrir o que se sabe de verdade sobre essa "
                 f"história. {corpo} {canal.editorial.get('chamada_final', '')}")
    min_w, max_w = _limites(canal)
    alvo_bloco = (min_w + max_w) // 2
    blocos, atual = [], []
    for frase in util.dividir_frases(texto):
        atual.append(frase)
        if util.contar_palavras(" ".join(atual)) >= alvo_bloco:
            blocos.append(" ".join(atual))
            atual = []
    if atual:
        blocos.append(" ".join(atual))
    brutos = []
    for i, narracao in enumerate(blocos):
        modo = _modo_padrao(canal.estilo, i)
        numero, unidade = _numero_no_texto(narracao)
        tipo = "nenhum"
        if modo == "cartao_animado":
            tipo = "contador" if numero else "titulo"
        chave = util.palavras_chave(narracao, 4)
        destaque = " ".join(w[:1].upper() + w[1:] for w in util.palavras_chave_originais(narracao, 3))
        brutos.append({"narracao": narracao, "modo_visual": modo, "tipo_cartao": tipo,
                       "dados_cartao": {"titulo": destaque, "numero": numero, "unidade": unidade},
                       "busca_visual": " ".join(chave), "destaque": destaque,
                       "potencial_short": bool(re.search(r"\d|\?|nunca|segredo|maior|verdade", narracao, re.I)),
                       "capitulo": ""})
    palavras = util.palavras_chave(f"{tema} {texto}", 15)
    frases = util.dividir_frases(texto)
    descricao = " ".join(frases[1:4]) if len(frases) > 3 else texto[:400]
    principal = (palavras[0] if palavras else "verdade").upper()
    return {
        "titulo_trabalho": tema, "resumo": util.limitar(" ".join(frases[:3]), 400), "palavras_chave": palavras,
        "blocos": brutos,
        "seo": {"titulo": util.limitar(f"{tema}: o que pouca gente sabe", 70, ""), "descricao": descricao,
                "tags": [tema] + palavras, "hashtags": palavras[:3],
                "frases_thumbnail": [f"O CASO *{principal}*", "A VERDADE *OCULTA*", "NINGUÉM *EXPLICA*"]},
        "fontes_citadas": [f["url"] for f in pesquisa.get("fontes", [])[:5]],
    }


def escrever_pedido_agente(projeto: Projeto, canal: Canal, tema: str, roteiro_bruto: str | None, pesquisa: dict) -> Path:
    sistema = instrucoes_sistema(canal, bool(roteiro_bruto), util.contar_palavras(roteiro_bruto or ""))
    texto = f"""# Pedido de roteiro para o agente de IA

O projeto `{projeto.id}` precisa de um roteiro estruturado. Não há modelo de linguagem configurado no `.env`,
então **você (agente) deve escrever o roteiro** seguindo as instruções e o esquema abaixo.

1. Leia as instruções e as fontes deste arquivo.
2. Escreva um JSON válido que siga o esquema em `{projeto.p('pesquisa', 'roteiro_agente.json')}`.
3. Rode: `python esteira.py importar-roteiro --projeto {projeto.id} --arquivo {projeto.p('pesquisa', 'roteiro_agente.json')}`
   (o comando valida, salva e continua automaticamente a produção).

## Instruções do roteirista
{sistema}

## Esquema JSON obrigatório
```json
{json.dumps(ESQUEMA_ROTEIRO, ensure_ascii=False, indent=2)}
```

## Material de entrada
{mensagem_usuario(tema, roteiro_bruto, pesquisa)}
"""
    caminho = projeto.p("pesquisa", "pedido_roteiro.md")
    util.salvar_texto(caminho, texto)
    return caminho


# ------------------------------------------------------------- finalização
def finalizar_roteiro(projeto: Projeto, canal: Canal, dados: dict, pesquisa: dict, origem: str) -> dict:
    tema = projeto.status.get("tema", dados.get("titulo_trabalho", ""))
    blocos = normalizar_blocos(dados.get("blocos", []), canal)
    if len(blocos) < 2:
        raise ErroRoteiro("O roteiro precisa de pelo menos 2 blocos de narração.")
    palavras = [str(p) for p in dados.get("palavras_chave", [])] or util.palavras_chave(
        " ".join(b["narracao"] for b in blocos), 12)
    seo = _normalizar_seo(dados.get("seo", {}), blocos, tema, palavras)
    roteiro = {
        "versao": 1, "tema": tema, "canal": canal.id, "estilo": canal.estilo, "origem": origem,
        "titulo_trabalho": dados.get("titulo_trabalho") or tema, "resumo": dados.get("resumo", ""),
        "palavras_chave": palavras, "blocos": blocos, "gerado_em": util.agora_iso(),
    }
    citadas = set(dados.get("fontes_citadas", []))
    fontes = [{"id": f.get("id"), "titulo": f.get("titulo", ""), "url": f.get("url", ""), "origem": f.get("origem", ""),
               "citada": f.get("url") in citadas} for f in pesquisa.get("fontes", [])]
    projeto.salvar_roteiro(roteiro)
    projeto.salvar("seo.json", seo)
    projeto.salvar("fontes.json", fontes)
    metadados.atualizar(projeto, canal)
    Historico(canal.caminho_db).registrar(tema, projeto.id, seo["titulo"], roteiro["resumo"], palavras, "em_producao")
    duracao = sum(util.contar_palavras(b["narracao"]) for b in blocos) / canal.palavras_por_segundo
    projeto.marcar_etapa("roteiro", True, blocos=len(blocos), origem=origem, duracao_estimada_seg=round(duracao))
    projeto.definir_estado("em_producao")
    log.ok(f"Roteiro com {len(blocos)} blocos (~{util.formatar_tempo(duracao)}) — origem: {origem}")
    return roteiro


def derivar_tema(roteiro_bruto: str) -> str:
    primeira = util.dividir_frases(roteiro_bruto)[0] if roteiro_bruto.strip() else "Roteiro sem título"
    return util.limitar(" ".join(primeira.split()[:12]), 90, "")


def executar(canal: Canal, tema: str | None = None, roteiro_bruto: str | None = None, forcar: bool = False,
             modo_agente: bool = False, sem_llm: bool = False, projeto: Projeto | None = None) -> Projeto:
    if not tema and not roteiro_bruto:
        raise ErroRoteiro("Informe um tema ou um roteiro.")
    tema = (tema or derivar_tema(roteiro_bruto or "")).strip()
    log.etapa(f"Módulo 1 — Pesquisa e roteiro: {tema}")
    llm = None if (sem_llm or modo_agente) else obter_cliente()
    if not forcar and projeto is None:
        resultado = Historico(canal.caminho_db).verificar_duplicidade(tema, (roteiro_bruto or "")[:800], llm)
        if resultado.bloqueado:
            raise TemaDuplicado(resultado)
        if resultado.em_duvida:
            log.aviso(resultado.motivo)
        else:
            log.ok(resultado.motivo)
    projeto = projeto or Projeto.criar(canal.id, tema)
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    projeto.atualizar_status(entrada={"tipo": "roteiro" if roteiro_bruto else "tema", "tema": tema})
    if roteiro_bruto:
        util.salvar_texto(projeto.p("pesquisa", "roteiro_bruto.txt"), roteiro_bruto)
    pesquisa = pesquisar(tema, canal.nicho)
    projeto.salvar("pesquisa/resultado.json", pesquisa)
    util.salvar_texto(projeto.p("pesquisa", "contexto.md"), contexto_markdown(pesquisa, 60000))

    if llm is not None:
        try:
            dados = gerar_com_llm(llm, canal, tema, roteiro_bruto, pesquisa)
            finalizar_roteiro(projeto, canal, dados, pesquisa, f"llm:{llm.provedor}")
            return projeto
        except ErroLLM as exc:
            log.aviso(f"Falha no modelo de linguagem: {exc}")
            projeto.alertar("roteiro", f"LLM indisponível ({exc}); roteiro gerado por alternativa.")
    if modo_agente or (llm is None and not sem_llm and not roteiro_bruto and _sem_texto(pesquisa)):
        caminho = escrever_pedido_agente(projeto, canal, tema, roteiro_bruto, pesquisa)
        projeto.definir_estado("aguardando_roteiro", "o agente de IA deve escrever o roteiro")
        log.aviso(f"AGUARDANDO_ROTEIRO_DO_AGENTE: leia {caminho} e importe o JSON com 'importar-roteiro'.")
        return projeto
    dados = gerar_heuristico(canal, tema, roteiro_bruto, pesquisa)
    finalizar_roteiro(projeto, canal, dados, pesquisa, "heuristico")
    projeto.alertar("roteiro", "Roteiro gerado sem modelo de linguagem (heurístico): revise o texto com atenção.")
    return projeto


def _sem_texto(pesquisa: dict) -> bool:
    return not any(len(f.get("texto") or "") > 400 for f in pesquisa.get("fontes", []))


def importar_roteiro(projeto: Projeto, canal: Canal, arquivo: Path) -> dict:
    """Importa um roteiro escrito pelo agente (ou editado à mão) e o valida."""
    log.etapa(f"Importando roteiro de {arquivo}")
    try:
        dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErroRoteiro(f"JSON inválido em {arquivo}: {exc}") from exc
    if "blocos" not in dados:
        raise ErroRoteiro("O JSON precisa ter a lista 'blocos'.")
    pesquisa = projeto.ler("pesquisa/resultado.json", {"fontes": []}) or {"fontes": []}
    roteiro = finalizar_roteiro(projeto, canal, dados, pesquisa, "agente")
    projeto.limpar_alertas("roteiro")
    return roteiro
