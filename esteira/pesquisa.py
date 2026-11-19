"""Pesquisa de fontes reais na internet (Wikipédia, DuckDuckGo, Brave e Tavily opcionais)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from . import log, util
from .config import env, env_int

AGENTE_HTTP = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
               "EsteiraCanalDark/1.0")
DOMINIOS_IGNORADOS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "twitter.com",
                      "x.com", "pinterest.", "linkedin.com", "reddit.com", "kwai")


def _sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": AGENTE_HTTP, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6"})
    return sessao


def _wikipedia(sessao: requests.Session, tema: str, idioma: str = "pt", n: int = 2) -> list[dict]:
    api = f"https://{idioma}.wikipedia.org/w/api.php"
    try:
        busca = sessao.get(api, params={"action": "query", "list": "search", "srsearch": tema, "srlimit": n,
                                        "format": "json", "utf8": 1}, timeout=15).json()
    except (requests.RequestException, ValueError) as exc:
        log.aviso(f"Wikipédia indisponível: {exc}")
        return []
    fontes = []
    for item in busca.get("query", {}).get("search", [])[:n]:
        try:
            dados = sessao.get(api, params={"action": "query", "prop": "extracts|info", "inprop": "url",
                                            "explaintext": 1, "exsectionformat": "plain", "titles": item["title"],
                                            "redirects": 1, "format": "json"}, timeout=20).json()
        except (requests.RequestException, ValueError):
            continue
        for pagina in dados.get("query", {}).get("pages", {}).values():
            texto = re.sub(r"\n{2,}", "\n", pagina.get("extract", "")).strip()
            if len(texto) < 300:
                continue
            fontes.append({"titulo": f"{pagina.get('title')} — Wikipédia", "url": pagina.get("fullurl", ""),
                           "trecho": util.limitar(texto, 300), "texto": texto[:9000], "origem": "wikipedia"})
    return fontes


def _duckduckgo(consulta: str, n: int = 8) -> list[dict]:
    """Busca na região Brasil e completa com a região global: com 'br-pt' o DuckDuckGo
    costuma devolver poucos (ou nenhum) resultados para frases mais longas."""
    try:
        from ddgs import DDGS  # pacote atual
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # nome antigo
        except ImportError:
            log.aviso("Pacote ddgs não instalado; busca web limitada à Wikipédia.")
            return []
    resultados: list[dict] = []
    vistos: set[str] = set()
    for regiao in ("br-pt", "wt-wt"):
        try:
            for r in DDGS().text(consulta, region=regiao, max_results=n) or []:
                if r.get("href") and r["href"] not in vistos:
                    vistos.add(r["href"])
                    resultados.append(r)
        except Exception as exc:  # "No results found" e falhas de rede caem aqui
            log.debug(f"DuckDuckGo ({regiao}) sem resultados para '{consulta}': {exc}")
        if len(resultados) >= n // 2:
            break
    if not resultados:
        log.aviso(f"Busca DuckDuckGo sem resultados para: {consulta}")
    return [{"titulo": r.get("title", ""), "url": r.get("href", ""), "trecho": r.get("body", ""), "origem": "web"}
            for r in resultados[:n]]


def _brave(sessao: requests.Session, consulta: str, n: int = 8) -> list[dict]:
    chave = env("BRAVE_API_KEY")
    if not chave:
        return []
    try:
        dados = sessao.get("https://api.search.brave.com/res/v1/web/search",
                           params={"q": consulta, "count": n, "search_lang": "pt-br", "country": "BR"},
                           headers={"X-Subscription-Token": chave, "Accept": "application/json"}, timeout=15).json()
    except (requests.RequestException, ValueError) as exc:
        log.aviso(f"Brave Search indisponível: {exc}")
        return []
    return [{"titulo": r.get("title", ""), "url": r.get("url", ""), "trecho": re.sub("<[^>]+>", "", r.get("description", "")),
             "origem": "web"} for r in dados.get("web", {}).get("results", [])]


def _tavily(sessao: requests.Session, consulta: str, n: int = 6) -> list[dict]:
    chave = env("TAVILY_API_KEY")
    if not chave:
        return []
    try:
        dados = sessao.post("https://api.tavily.com/search", json={"api_key": chave, "query": consulta,
                                                                    "max_results": n, "include_raw_content": True},
                            timeout=30).json()
    except (requests.RequestException, ValueError) as exc:
        log.aviso(f"Tavily indisponível: {exc}")
        return []
    return [{"titulo": r.get("title", ""), "url": r.get("url", ""), "trecho": r.get("content", ""),
             "texto": (r.get("raw_content") or "")[:6000], "origem": "web"} for r in dados.get("results", [])]


def extrair_texto_pagina(sessao: requests.Session, url: str, limite: int = 6000) -> str:
    try:
        resposta = sessao.get(url, timeout=12)
        tipo = resposta.headers.get("Content-Type", "")
        if resposta.status_code >= 400 or "html" not in tipo:
            return ""
        from bs4 import BeautifulSoup

        sopa = BeautifulSoup(resposta.content, "html.parser")
        for marca in sopa(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe"]):
            marca.decompose()
        principal = sopa.find("article") or sopa.find("main") or sopa.body or sopa
        paragrafos = [p.get_text(" ", strip=True) for p in principal.find_all(["p", "li", "h2", "h3"])]
        texto = "\n".join(p for p in paragrafos if len(p) > 40)
        return re.sub(r"\s{3,}", "  ", texto)[:limite]
    except Exception:
        return ""


def pesquisar(tema: str, nicho: str = "", max_paginas: int | None = None) -> dict:
    """Pesquisa o tema e devolve {"consultas": [...], "fontes": [...]} com textos extraídos."""
    max_paginas = max_paginas or env_int("PESQUISA_MAX_PAGINAS", 6)
    consultas = [tema, f"{tema} história fatos", f"{tema} explicação"]
    if env("PESQUISA_DESATIVADA") == "1":
        log.aviso("Pesquisa web desativada (PESQUISA_DESATIVADA=1).")
        return {"tema": tema, "consultas": consultas, "fontes": [], "pesquisado_em": util.agora_iso()}
    sessao = _sessao()
    log.info(f"Pesquisando fontes sobre: {tema}")
    fontes: list[dict] = _wikipedia(sessao, tema, "pt")
    if not fontes:
        fontes = _wikipedia(sessao, tema, "en", 1)
    web: list[dict] = []
    for consulta in consultas[:2]:
        web += _tavily(sessao, consulta) + _brave(sessao, consulta) + _duckduckgo(consulta)
    vistos = {f["url"] for f in fontes}
    paginas = 0
    for item in web:
        url = item.get("url", "")
        dominio = urlparse(url).netloc.lower()
        if not url or url in vistos or url.lower().endswith(".pdf") or any(d in dominio for d in DOMINIOS_IGNORADOS):
            continue
        vistos.add(url)
        if paginas >= max_paginas:
            break
        texto = item.get("texto") or extrair_texto_pagina(sessao, url)
        if len(texto) < 400:
            continue
        item["texto"] = texto
        fontes.append(item)
        paginas += 1
    for i, fonte in enumerate(fontes, 1):
        fonte["id"] = i
    if fontes:
        log.ok(f"{len(fontes)} fontes coletadas")
    else:
        log.aviso("Nenhuma fonte encontrada (sem internet?). O roteiro usará apenas o conhecimento disponível.")
    return {"tema": tema, "consultas": consultas, "fontes": fontes, "pesquisado_em": util.agora_iso()}


def contexto_markdown(pesquisa: dict, limite_total: int = 26000) -> str:
    partes, total = [], 0
    for fonte in pesquisa.get("fontes", []):
        corpo = fonte.get("texto") or fonte.get("trecho") or ""
        bloco = f"### Fonte {fonte['id']}: {fonte.get('titulo', '')}\nURL: {fonte.get('url', '')}\n{corpo}\n"
        if total + len(bloco) > limite_total:
            bloco = bloco[: max(0, limite_total - total)]
        partes.append(bloco)
        total += len(bloco)
        if total >= limite_total:
            break
    return "\n".join(partes) if partes else "(nenhuma fonte disponível)"
