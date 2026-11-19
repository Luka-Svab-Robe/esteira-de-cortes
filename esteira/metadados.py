"""Metadados finais de publicação: título, descrição com capítulos, fontes, créditos e tags."""
from __future__ import annotations

import re

from . import util

LIMITE_DESCRICAO = 4900
LIMITE_TITULO = 100
LIMITE_TAGS = 480


def _limpar_youtube(texto: str) -> str:
    """O YouTube rejeita '<' e '>' em títulos e descrições."""
    return texto.replace("<", "‹").replace(">", "›")


def validar_capitulos(capitulos: list[dict], duracao_total: float) -> tuple[bool, str]:
    validos = [c for c in capitulos if "segundos" in c]
    if len(validos) < 3:
        return False, "São necessários pelo menos 3 capítulos para o YouTube exibi-los."
    if validos[0]["segundos"] > 0.5:
        return False, "O primeiro capítulo precisa começar em 00:00."
    limites = [c["segundos"] for c in validos] + [duracao_total]
    for i in range(len(validos)):
        if limites[i + 1] - limites[i] < 10:
            return False, f"O capítulo '{validos[i]['titulo']}' tem menos de 10 segundos."
    return True, ""


def limitar_tags(tags: list[str]) -> list[str]:
    finais, total = [], 0
    for tag in tags:
        tag = re.sub(r"[<>,]", " ", str(tag)).strip()
        if not tag or tag.lower() in (t.lower() for t in finais):
            continue
        custo = len(tag) + (2 if " " in tag else 0) + 1
        if total + custo > LIMITE_TAGS:
            break
        finais.append(tag)
        total += custo
    return finais


def compor_descricao(meta: dict, chamada_final: str = "") -> str:
    partes = [meta.get("descricao_base", "").strip()]
    if meta.get("capitulos_validos"):
        linhas = [f"{c['tempo']} {c['titulo']}" for c in meta.get("capitulos", []) if "tempo" in c]
        partes.append("⏱️ Capítulos\n" + "\n".join(linhas))
    if chamada_final:
        partes.append(chamada_final.strip())
    fontes = [f"• {util.limitar(f.get('titulo') or f['url'], 90)} — {f['url']}" for f in meta.get("fontes", [])
              if f.get("url")]
    creditos = []
    for c in meta.get("creditos", []):
        autor = c.get("autor") or "autor desconhecido"
        licenca = f", {c['licenca']}" if c.get("licenca") else ""
        creditos.append(f"• {c.get('tipo', 'Mídia')}: {autor} ({c.get('fonte', '')}{licenca}) {c.get('url', '')}".strip())
    hashtags = " ".join("#" + re.sub(r"[^\wÀ-ú]", "", h.lstrip("#")) for h in meta.get("hashtags", [])[:5] if h)

    def montar(n_fontes: int, n_creditos: int) -> str:
        blocos = list(partes)
        if fontes[:n_fontes]:
            blocos.append("📚 Fontes consultadas\n" + "\n".join(fontes[:n_fontes]))
        if creditos[:n_creditos]:
            blocos.append("🎞️ Créditos de mídia\n" + "\n".join(creditos[:n_creditos]))
        if hashtags:
            blocos.append(hashtags)
        return "\n\n".join(b for b in blocos if b)

    n_f, n_c = len(fontes), len(creditos)
    texto = montar(n_f, n_c)
    while len(texto) > LIMITE_DESCRICAO and (n_f > 3 or n_c > 0):
        if n_c > 0:
            n_c -= 1
        else:
            n_f -= 1
        texto = montar(n_f, n_c)
    return _limpar_youtube(texto[:LIMITE_DESCRICAO])


def atualizar(projeto, canal=None) -> dict:
    """Cria/atualiza metadados.json preservando edições feitas no painel."""
    seo = projeto.ler("seo.json", {}) or {}
    fontes = projeto.ler("fontes.json", []) or []
    meta = projeto.ler("metadados.json", {}) or {}
    linha = projeto.linha_tempo()

    padroes = {
        "titulo": seo.get("titulo", projeto.status.get("tema", "")),
        "descricao_base": seo.get("descricao", ""),
        "tags": seo.get("tags", []),
        "hashtags": seo.get("hashtags", []),
        "capitulos": [dict(c) for c in seo.get("capitulos", [])],
        "frases_thumbnail": list(seo.get("frases_thumbnail", [])),
        "thumbnail_escolhida": 1,
        "shorts": [],
        "categoria": (canal.editorial.get("categoria_youtube", 27) if canal else 27),
        "idioma": "pt-BR",
    }
    for chave, valor in padroes.items():
        meta.setdefault(chave, valor)

    meta["titulo"] = _limpar_youtube(util.limitar(meta["titulo"], LIMITE_TITULO, ""))
    meta["tags"] = limitar_tags(meta.get("tags", []))
    inicio_bloco = {item["bloco"]: item["inicio"] for item in linha}
    duracao_total = linha[-1]["fim"] if linha else 0.0
    capitulos = []
    for cap in meta.get("capitulos", []):
        cap = {"bloco": int(cap.get("bloco", 1)), "titulo": str(cap.get("titulo", "")).strip() or "Capítulo"}
        if cap["bloco"] in inicio_bloco:
            cap["segundos"] = round(inicio_bloco[cap["bloco"]], 2)
            cap["tempo"] = util.formatar_tempo(cap["segundos"], forcar_horas=duracao_total >= 3600)
        capitulos.append(cap)
    capitulos.sort(key=lambda c: c.get("segundos", 1e9))
    if capitulos and "segundos" in capitulos[0]:
        capitulos[0]["segundos"] = 0.0
        capitulos[0]["tempo"] = util.formatar_tempo(0, forcar_horas=duracao_total >= 3600)
    meta["capitulos"] = capitulos
    if linha:
        meta["capitulos_validos"], meta["aviso_capitulos"] = validar_capitulos(capitulos, duracao_total)
        meta["duracao_segundos"] = round(duracao_total, 2)
    citadas = [f for f in fontes if f.get("citada")]
    meta["fontes"] = [{"titulo": f.get("titulo", ""), "url": f["url"]} for f in (citadas or fontes) if f.get("url")][:15]
    meta["creditos"] = projeto.ler("midia/creditos.json", []) or []
    chamada = canal.editorial.get("chamada_final", "") if canal else ""
    meta["descricao_final"] = compor_descricao(meta, chamada)
    projeto.salvar("metadados.json", meta)
    return meta
