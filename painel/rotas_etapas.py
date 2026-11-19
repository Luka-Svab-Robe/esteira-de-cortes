"""Pipeline por etapas: opções da live, momentos editáveis à mão, executar uma etapa, copiloto e checagem."""
from __future__ import annotations

import json
import re
import threading

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from esteira.canal import Canal
from esteira.config import RAIZ
from painel import copiloto, sessao

router = APIRouter()

MODELOS = ("padrao", "direita", "centro")
LAYOUTS = ("auto", "dividido", "moldura", "vertical")
PREVIAS = MODELOS + ("dividido",)  # miniaturas que o painel mostra (modelos da moldura + tela dividida)
EDITAVEIS = {"aguardando_etapa", "pendente_revisao", "reprovado", "erro"}
ETAPAS_EXECUTAVEIS = ("analise", "edicao")
DURACAO_MIN_SEG, DURACAO_MAX_SEG = 8.0, 180.0
MAX_CORTES = 10
LIMITE_TITULO_YT, LIMITE_DESCRICAO_YT, LIMITE_TAGS_YT = 100, 5000, 500
_TRILHA = re.compile(r"[\w\-. ]{1,120}\.(mp3|wav|ogg|m4a|flac|opus)", re.I)
_CENSURA = re.compile(r"\[\s*_+\s*\]")


def _srv():
    from painel import servidor  # import tardio: o servidor inclui este router

    return servidor


def _erro(status: int, mensagem: str):
    raise HTTPException(status, {"mensagem": mensagem})


def validar_opcoes(bruto) -> dict:
    """Opções escolhidas antes de rodar uma live (sobrepõem as do canal só nesta live)."""
    if not bruto:
        return {}
    if not isinstance(bruto, dict):
        _erro(400, "Opções inválidas.")
    saida: dict = {}
    faixas = {"max_cortes_por_live": (int, 1, MAX_CORTES, "Quantidade de Shorts"),
              "duracao_min": (float, 10, 60, "Duração mínima"), "duracao_max": (float, 15, DURACAO_MAX_SEG, "Duração máxima")}
    for chave, (tipo, minimo, maximo, rotulo) in faixas.items():
        if bruto.get(chave) in (None, ""):
            continue
        try:
            valor = tipo(bruto[chave])
        except (TypeError, ValueError):
            _erro(400, f"{rotulo}: use um número.")
        if not minimo <= valor <= maximo:
            _erro(400, f"{rotulo}: escolha entre {minimo} e {maximo}.")
        saida[chave] = valor
    if saida.get("duracao_min", 0) >= saida.get("duracao_max", DURACAO_MAX_SEG + 1):
        _erro(400, "A duração mínima precisa ser menor que a máxima.")
    if bruto.get("modelo"):
        if bruto["modelo"] not in MODELOS:
            _erro(400, "Modelo visual desconhecido.")
        saida["modelo"] = bruto["modelo"]
    if "traduzir" in bruto:
        saida["traduzir"] = bool(bruto["traduzir"])
    if bruto.get("layout"):
        if bruto["layout"] not in LAYOUTS:
            _erro(400, "Layout desconhecido.")
        saida["layout"] = bruto["layout"]
    return saida


def _ocupado(projeto_id: str) -> bool:
    return any(e.get("projeto") == projeto_id and e["proc"].poll() is None for e in _srv()._envios.values())


def _fala_do_trecho(projeto, inicio: float, fim: float) -> str:
    """Texto falado entre dois instantes, juntando os candidatos da análise que se sobrepõem ao trecho."""
    partes = []
    for c in projeto.ler("analise/candidatos.json", []) or []:
        if float(c["fim"]) > inicio and float(c["inicio"]) < fim:
            partes.append(str(c.get("texto", "")))
    return _CENSURA.sub("", " ".join(partes)).strip()


def _pontuacao_do_trecho(projeto, inicio: float, fim: float) -> float:
    notas = [float(c.get("pontuacao", 0)) for c in projeto.ler("analise/candidatos.json", []) or []
             if float(c["fim"]) > inicio and float(c["inicio"]) < fim]
    return round(max(notas), 2) if notas else 0.0


def _video_da_live(projeto) -> str | None:
    from esteira.cortes import orquestrador as orq

    try:
        arquivo = orq._video_fonte(projeto)
    except FileNotFoundError:
        return None
    return _srv()._url(projeto, str(arquivo.relative_to(projeto.pasta)))


# ------------------------------------------------------------------ momentos (edição manual)
@router.get("/api/projetos/{canal}/{pasta}/momentos")
def ver_momentos(canal: str, pasta: str):
    from esteira.cortes import orquestrador as orq

    p = _srv()._projeto(canal, pasta)
    cfg = orq.cfg_cortes(Canal.carregar(canal), p)
    live = p.status.get("live", {}) or {}
    # o que outra rodada/lote desta live já escolheu (ou postou) sai das sugestões; os daqui o painel esconde
    candidatos = [{"id": c["id"], "inicio": c["inicio"], "fim": c["fim"], "pontuacao": c.get("pontuacao", 0),
                   "texto": _CENSURA.sub("", str(c.get("texto", "")))[:400].strip()}
                  for c in orq.sem_os_usados(p, p.ler("analise/candidatos.json", []) or [])]
    return {"estado": p.estado, "editavel": p.estado in EDITAVEIS and not _ocupado(p.id),
            "arquivo_inicio": orq._arquivo_inicio(p.status), "video": _video_da_live(p),
            "live": {"titulo": live.get("titulo", ""), "duracao": live.get("duracao"), "url": live.get("url", "")},
            "limites": {"duracao_min": float(cfg.get("duracao_min", 20)), "duracao_max": float(cfg.get("duracao_max", 58)),
                        "maximo": int(cfg.get("max_cortes_por_live", 5))},
            "opcoes": p.status.get("opcoes") or {}, "candidatos": candidatos,
            "cortes": p.ler("analise/cortes.json", []) or []}


def _tags(valor) -> list[str]:
    itens = valor if isinstance(valor, list) else str(valor or "").split(",")
    return [str(t).strip().lstrip("#")[:40] for t in itens if str(t).strip()][:12]


def _momento(p, item: dict, n: int, duracao_live: float | None) -> dict:
    try:
        inicio, fim = round(float(item["inicio"]), 2), round(float(item["fim"]), 2)
    except (KeyError, TypeError, ValueError):
        _erro(400, f"Corte {n}: informe o começo e o fim em segundos.")
    if inicio < 0 or fim <= inicio:
        _erro(400, f"Corte {n}: o fim precisa vir depois do começo.")
    if duracao_live and fim > duracao_live + 1:
        _erro(400, f"Corte {n}: passa do fim da live.")
    if not DURACAO_MIN_SEG <= fim - inicio <= DURACAO_MAX_SEG:
        _erro(400, f"Corte {n}: cada Short precisa ter entre {DURACAO_MIN_SEG:.0f} e {DURACAO_MAX_SEG:.0f} segundos.")
    gancho = re.sub(r"\s+", " ", str(item.get("gancho", ""))).strip()[:60]
    if not gancho:
        _erro(400, f"Corte {n}: escreva o gancho (ou peça ao ✨ copiloto).")
    momento = {"inicio": inicio, "fim": fim, "gancho": gancho,
               "titulo": re.sub(r"\s+", " ", str(item.get("titulo", ""))).strip()[:90] or gancho.replace("*", ""),
               "descricao": str(item.get("descricao", "")).strip()[:4000], "tags": _tags(item.get("tags")),
               "motivo": str(item.get("motivo", "") or "escolhido à mão").strip()[:200],
               "pontuacao": _pontuacao_do_trecho(p, inicio, fim), "texto": _fala_do_trecho(p, inicio, fim),
               "automatico": False, "manual": True}
    trilha = str(item.get("trilha", "") or "")
    if trilha == "nenhuma" or _TRILHA.fullmatch(trilha):
        momento["trilha"] = trilha
    if item.get("modelo") in MODELOS:
        momento["modelo"] = item["modelo"]
    return momento


@router.put("/api/projetos/{canal}/{pasta}/momentos")
def salvar_momentos(canal: str, pasta: str, corpo: dict = Body(...)):
    """Grava os momentos escolhidos/ajustados à mão; depois é só mandar a etapa de edição."""
    p = _srv()._projeto(canal, pasta)
    if p.estado not in EDITAVEIS:
        _erro(409, "Esta live não pode ser editada agora (já foi para o YouTube ou está sendo processada).")
    if _ocupado(p.id):
        _erro(409, "A equipe ainda está trabalhando nesta live; espere terminar.")
    lista = corpo.get("cortes")
    if not isinstance(lista, list) or not lista:
        _erro(400, "Escolha pelo menos um momento.")
    if len(lista) > MAX_CORTES:
        _erro(400, f"No máximo {MAX_CORTES} Shorts por live.")
    duracao_live = float((p.status.get("live") or {}).get("duracao") or 0) or None
    # textos automáticos que você não mexeu continuam automáticos (assim a tradução para o português ainda vale)
    automaticos = {(c.get("gancho", ""), c.get("titulo", "")) for c in (p.ler("analise/cortes.json", []) or [])
                   if c.get("automatico")}
    from esteira.cortes import analise

    # o mesmo trecho duas vezes (ex.: "＋ Usar" numa sugestão já escolhida) viraria dois Shorts iguais: fica o 1º
    momentos, repetidos = analise.sem_repetidos([_momento(p, item, n, duracao_live) for n, item in enumerate(lista, 1)
                                                 if isinstance(item, dict)])
    momentos.sort(key=lambda m: m["inicio"])
    for momento in momentos:
        if (momento["gancho"], momento["titulo"]) in automaticos:
            momento["automatico"] = True
    for n, momento in enumerate(momentos, 1):
        momento["indice"] = n
    p.salvar("analise/cortes.json", momentos)
    p.marcar_etapa("analise", True, cortes=len(momentos), origem="manual")
    p.atualizar_status(edicao_desatualizada=True)
    p.definir_estado("aguardando_etapa", "momentos editados: mande editar os Shorts")
    return {"ok": True, "cortes": momentos, "repetidos": len(repetidos)}


# ------------------------------------------------------------------ executar uma etapa
@router.post("/api/projetos/{canal}/{pasta}/etapas/{etapa}")
def rodar_etapa(canal: str, pasta: str, etapa: str, corpo: dict = Body(default={})):
    """Roda uma etapa só: 'analise' (escolher momentos, para antes de editar) ou 'edicao' (fazer os Shorts)."""
    srv = _srv()
    if etapa not in ETAPAS_EXECUTAVEIS:
        _erro(404, "Etapa desconhecida.")
    p = srv._projeto(canal, pasta)
    srv._canal_cortes(canal)
    if p.estado not in EDITAVEIS:
        _erro(409, "Esta live não pode ser refeita agora.")
    if _ocupado(p.id):
        _erro(409, "A equipe já está trabalhando nesta live.")
    if _video_da_live(p) is None:
        _erro(409, "A live ainda não foi baixada: use “Tentar de novo”.")
    if etapa == "edicao" and not p.ler("analise/cortes.json", []):
        _erro(409, "Escolha os momentos antes de editar.")
    opcoes = validar_opcoes(corpo.get("opcoes"))
    if opcoes:
        p.atualizar_status(opcoes={**(p.status.get("opcoes") or {}), **opcoes})
    args = ["cortes", "continuar", "--projeto", p.id, "--a-partir-de", etapa, "--sem-painel"]
    if etapa == "analise" and corpo.get("parar", True):
        args += ["--ate", "analise"]
    live = p.status.get("live", {}) or {}
    envio = srv._iniciar_processo(canal, args, live.get("url", ""), titulo=live.get("titulo", ""),
                                  dono=(p.status.get("fonte") or {}).get("nome", ""), projeto=p.id, etapa=etapa)
    return {"ok": True, "envio": envio["id"]}


# ------------------------------------------------------------------ copiloto
def _contexto_do_corte(canal: str, pasta: str, indice, inicio, fim) -> dict:
    p = _srv()._projeto(canal, pasta)
    c = Canal.carregar(canal)
    ctx = {"canal": c.nome, "nicho": c.editorial.get("nicho", ""),
           "titulo_live": (p.status.get("live") or {}).get("titulo", "")}
    if inicio is not None and fim is not None:
        try:
            ctx["fala"] = _fala_do_trecho(p, float(inicio), float(fim))
        except (TypeError, ValueError):
            _erro(400, "Trecho inválido.")
    if indice is not None:
        corte = next((k for k in (p.ler("analise/cortes.json", []) or []) if int(k.get("indice", 0)) == int(indice)), None)
        if corte:
            ctx.setdefault("fala", _CENSURA.sub("", corte.get("texto", "")))
            ctx.update({"gancho": corte.get("gancho", ""), "titulo": corte.get("titulo", "")})
    return ctx


@router.post("/api/copiloto")
def sugerir(corpo: dict = Body(...)):
    usuario = sessao.usuario_atual()
    sessao.registrar_tentativa(f"copiloto|{usuario['id']}", limite=30, janela=60,
                               mensagem="Muitos pedidos ao copiloto; espere um minuto.")
    campo = str(corpo.get("campo", ""))
    if campo not in copiloto.CAMPOS:
        _erro(400, "Escolha gancho, título, descrição ou tags.")
    ctx: dict = {}
    if corpo.get("canal") and corpo.get("projeto"):
        ctx = _contexto_do_corte(str(corpo["canal"]), str(corpo["projeto"]), corpo.get("indice"),
                                 corpo.get("inicio"), corpo.get("fim"))
    for chave in ("gancho", "titulo"):
        if corpo.get(chave):
            ctx[chave] = str(corpo[chave])[:200]
    ctx["atual"] = str(corpo.get("atual", "") or "")[:2000]
    if not ctx.get("fala") and corpo.get("fala"):
        ctx["fala"] = str(corpo["fala"])[:3000]
    return copiloto.sugerir(usuario["id"], campo, ctx)


# ------------------------------------------------------------------ checagem automática (revisor)
def _checar_short(s: dict, texto: dict, fonte: str, limites: dict) -> dict:
    itens = []

    def item(nivel: str, mensagem: str) -> None:
        itens.append({"nivel": nivel, "texto": mensagem})

    duracao = float(s.get("duracao") or 0)
    if not s.get("video"):
        item("erro", "O vídeo deste Short não foi gerado.")
    if duracao > 180:
        item("erro", f"Tem {duracao:.0f} s: o YouTube só aceita Shorts de até 3 minutos.")
    elif duracao < limites["duracao_min"] - 3:
        item("aviso", f"Curto demais ({duracao:.0f} s).")
    else:
        item("ok", f"Duração de {duracao:.0f} s.")
    gancho = s.get("gancho", "")
    if not gancho.strip():
        item("aviso", "Sem gancho no topo do vídeo.")
    elif len(gancho.replace("*", "")) > 50:
        item("aviso", "Gancho longo: pode ficar pequeno na tela.")
    else:
        item("ok", "Gancho no tamanho certo.")
    titulo = texto.get("titulo", "")
    if not titulo.strip():
        item("erro", "Falta o título.")
    elif len(titulo) > LIMITE_TITULO_YT:
        item("erro", f"Título com {len(titulo)} letras (o YouTube aceita {LIMITE_TITULO_YT}).")
    else:
        item("ok", "Título ok.")
    descricao = texto.get("descricao", "")
    if len(descricao) > LIMITE_DESCRICAO_YT:
        item("erro", "Descrição longa demais para o YouTube.")
    if fonte and fonte.lower() not in descricao.lower():
        item("aviso", f"A descrição não dá o crédito a {fonte}.")
    if len(",".join(texto.get("tags", []))) > LIMITE_TAGS_YT:
        item("erro", "Tags passam do limite de 500 letras do YouTube.")
    if any(_CENSURA.search(t or "") for t in (gancho, titulo, descricao)):
        item("aviso", "Há um “[ __ ]” (palavra censurada) no texto.")
    if s.get("traduzida_de"):
        from esteira.cortes.traducao import nome_idioma

        item("ok", f"Fala em {nome_idioma(s['traduzida_de'])} traduzida para o português.")
    if s.get("legenda_editada"):
        item("ok", "Legenda ajustada por você.")
    if not s.get("trilha"):
        item("aviso", "Sem música de fundo.")
    nivel = "erro" if any(i["nivel"] == "erro" for i in itens) else "aviso" if any(i["nivel"] == "aviso" for i in itens) else "ok"
    return {"indice": s["indice"], "nivel": nivel, "itens": itens}


@router.get("/api/projetos/{canal}/{pasta}/revisao")
def revisao_automatica(canal: str, pasta: str):
    from esteira.cortes import orquestrador as orq

    srv = _srv()
    p = srv._projeto(canal, pasta)
    resumo = srv._resumo_cortes(p)
    cfg = orq.cfg_cortes(Canal.carregar(canal), p)
    limites = {"duracao_min": float(cfg.get("duracao_min", 20))}
    dados_fonte = p.status.get("fonte") or {}
    fonte = dados_fonte.get("nome", "") if dados_fonte.get("tipo") == "autorizado" else ""  # canal próprio não precisa de crédito
    shorts = [_checar_short(s, s, fonte, limites) for s in resumo["shorts"]]
    return {"shorts": shorts, "desatualizado": bool(p.status.get("edicao_desatualizada")),
            "nivel": "erro" if any(s["nivel"] == "erro" for s in shorts) else
                     "aviso" if any(s["nivel"] == "aviso" for s in shorts) else "ok"}


# ------------------------------------------------------------------ modelos visuais (templates)
_TRAVA_PREVIA = threading.Lock()
def _secao_yaml(nome: str) -> re.Pattern:
    return re.compile(rf"(?ms)^{re.escape(nome)}:[^\n]*\n(.*?)(?=^[a-z_]+:|\Z)")


def _formatar_yaml(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (list, tuple)):  # lista em linha, com aspas (JSON também é YAML válido)
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in valor) + "]"
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def gravar_padroes_cortes(texto: str, valores: dict) -> str:
    """Troca valores dentro da seção `cortes:` do identidade.yaml, preservando comentários e o resto do arquivo."""
    return gravar_na_secao(texto, "cortes", valores)


def gravar_na_secao(texto: str, nome_secao: str, valores: dict) -> str:
    """Troca valores dentro de uma seção (ex.: `publicacao:`) do identidade.yaml, preservando comentários e o resto."""
    secao = _secao_yaml(nome_secao).search(texto)
    if not secao:
        raise ValueError(f"O identidade.yaml do canal não tem a seção {nome_secao}.")
    corpo = secao.group(1)
    for chave, valor in valores.items():
        linha = re.compile(rf"(?m)^(  {re.escape(chave)}:[ \t]*)([^\s#][^#\n]*?)?([ \t]*(#.*)?)$")
        novo = _formatar_yaml(valor)
        if linha.search(corpo):
            corpo = linha.sub(lambda m: m.group(1) + novo + (m.group(3) or ""), corpo, count=1)
        else:
            corpo = corpo.rstrip("\n") + f"\n  {chave}: {novo}\n"
    return texto[:secao.start(1)] + corpo + texto[secao.end(1):]


def _dependencias_previa(c: Canal) -> list:
    return [RAIZ / "templates" / "cenas" / "short_moldura.html", c.pasta / "identidade.yaml",
            c.pasta_credenciais / "canal_youtube.json",
            *(c.pasta / "assets" / nome for nome in ("avatar.jpg", "avatar.png", "logo.png", "logo.jpg"))]


@router.get("/api/modelos/{canal}/{modelo}.png")
def previa_do_modelo(canal: str, modelo: str):
    """Miniatura de como o Short fica em cada modelo, com a foto e o @ do próprio canal (gerada uma vez e guardada)."""
    from esteira.cortes import edicao

    if modelo not in PREVIAS:
        _erro(404, "Modelo desconhecido.")
    c = _srv()._canal_cortes(canal)
    destino = c.pasta / "assets" / "previas" / f"{modelo}.png"
    mais_novo = max((d.stat().st_mtime for d in _dependencias_previa(c) if d.exists()), default=0)
    with _TRAVA_PREVIA:
        if not destino.exists() or destino.stat().st_mtime < mais_novo:
            try:
                edicao.previa_modelo(c, modelo, destino)
            except Exception:
                _erro(503, "Não deu para gerar a prévia agora; tente de novo em instantes.")
    return FileResponse(destino, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.put("/api/canais/{canal}/padroes")
def salvar_padroes_do_canal(canal: str, corpo: dict = Body(...)):
    """Padrões dos próximos cortes do canal: modelo visual, quantidade e duração dos Shorts e tradução."""
    valores = validar_opcoes(corpo)
    if not valores:
        _erro(400, "Nada para salvar.")
    c = _srv()._canal_cortes(canal)
    caminho = c.pasta / "identidade.yaml"
    try:
        texto = gravar_padroes_cortes(caminho.read_text(encoding="utf-8"), valores)
    except ValueError as exc:
        _erro(500, str(exc))
    caminho.write_text(texto, encoding="utf-8")
    Canal.carregar(canal)  # confere que o arquivo continua válido
    return {"ok": True, "padroes": valores}
