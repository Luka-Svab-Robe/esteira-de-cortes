"""Módulo 4b — Thumbnails 1280x720: quadros de maior contraste + 3 frases + 3 templates HTML/CSS."""
from __future__ import annotations

from pathlib import Path

from .. import log, templates
from ..canal import Canal
from ..navegador import Navegador
from ..projeto import Projeto

TEMPLATES = ("impacto", "dividido", "moldura")
LIMITE_BYTES = 2 * 1024 * 1024  # limite do YouTube para capas


def pontuar_quadro(caminho: Path) -> dict:
    """Contraste de luminância + colorido (Hasler–Süsstrunk) + nitidez, com penalidade para quadros escuros."""
    import numpy as np
    from PIL import Image

    with Image.open(caminho) as im:
        rgb = np.asarray(im.convert("RGB").resize((320, 180)), dtype=np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    contraste = float(lum.std())
    rg, yb = r - g, 0.5 * (r + g) - b
    colorido = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    bordas = float(np.abs(4 * lum[1:-1, 1:-1] - lum[:-2, 1:-1] - lum[2:, 1:-1] - lum[1:-1, :-2] - lum[1:-1, 2:]).mean())
    brilho = float(lum.mean())
    penalidade = 0.5 if brilho < 35 or brilho > 225 else 1.0
    pontuacao = (0.5 * contraste / 64 + 0.3 * colorido / 80 + 0.2 * bordas / 20) * penalidade
    hist, _ = np.histogramdd(rgb.reshape(-1, 3), bins=(4, 4, 4), range=((0, 256),) * 3)
    hist = (hist / hist.sum()).ravel()
    return {"arquivo": str(caminho), "pontuacao": round(pontuacao, 4), "contraste": round(contraste, 1),
            "histograma": hist.tolist()}


def _distancia(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b))


def coletar_candidatos(projeto: Projeto) -> list[Path]:
    cache = projeto.ler("cenas/cache.json", {}) or {}
    candidatos = [projeto.p(v["quadro_limpo"]) for _, v in sorted(cache.items(), key=lambda kv: int(kv[0]))
                  if v.get("quadro_limpo") and projeto.p(v["quadro_limpo"]).exists()]
    return candidatos  # quadros limpos (sem legenda/logo); repetidos se forem menos de 3


def fundos_ilustrados(projeto: Projeto, canal: Canal, nav: Navegador, quantidade: int = 3) -> list[Path]:
    """Sem nenhum quadro limpo (ex.: vídeo só de cartões/tipografia): gera fundos 2D sem texto para o tema."""
    from .m3_montagem import CENARIOS, ICONES, _escolher

    roteiro = projeto.roteiro()
    texto = f"{roteiro.get('tema', '')} {' '.join(roteiro.get('palavras_chave', []))} " + \
        " ".join(b.get("busca_visual", "") for b in roteiro.get("blocos", []))
    cenario = _escolher(CENARIOS, texto, "montanhas")
    icones = sorted(ICONES, key=lambda nome: -sum(texto.lower().count(p) for p in ICONES[nome]))
    pasta = projeto.p("thumbnails", "candidatos")
    pasta.mkdir(parents=True, exist_ok=True)
    tema = templates.tema_canal(canal)
    resultado = []
    for n in range(quantidade):
        html = templates.renderizar("cenas/ilustracao_2d.html", pasta / f"fundo_{n + 1}.html", tema,
                                    {"cenario": cenario, "icone": icones[n % len(icones)], "texto": "", "semente": 11 + n})
        png = nav.capturar(html, pasta / f"fundo_{n + 1}.png", 1920, 1080, t=2.0)
        resultado.append(png)
        html.unlink(missing_ok=True)
    return resultado


def escolher_quadros(candidatos: list[Path], quantidade: int = 3) -> list[dict]:
    avaliados = sorted((pontuar_quadro(c) for c in candidatos), key=lambda q: q["pontuacao"], reverse=True)
    escolhidos: list[dict] = []
    for quadro in avaliados:
        if all(_distancia(quadro["histograma"], e["histograma"]) > 0.35 for e in escolhidos):
            escolhidos.append(quadro)
        if len(escolhidos) == quantidade:
            break
    for quadro in avaliados:
        if len(escolhidos) >= quantidade:
            break
        if quadro not in escolhidos:
            escolhidos.append(quadro)
    while escolhidos and len(escolhidos) < quantidade:
        escolhidos.append(escolhidos[len(escolhidos) % len(escolhidos)])
    return escolhidos


def _jpeg_ate_limite(png: Path, destino: Path) -> Path:
    from PIL import Image

    with Image.open(png) as im:
        im = im.convert("RGB")
        for qualidade in (94, 90, 85, 80, 72):
            im.save(destino, "JPEG", quality=qualidade, optimize=True, progressive=True)
            if destino.stat().st_size <= LIMITE_BYTES:
                break
    return destino


def executar(projeto: Projeto, canal: Canal, forcar: bool = False) -> list[dict]:
    log.etapa("Módulo 4 — Thumbnails (3 variações 1280x720)")
    meta = projeto.ler("metadados.json", {}) or {}
    frases = list(meta.get("frases_thumbnail") or (projeto.ler("seo.json", {}) or {}).get("frases_thumbnail", []))
    while len(frases) < 3:
        frases.append(["A VERDADE *OCULTA*", "NINGUÉM *EXPLICA*", "O QUE *ACONTECEU*?"][len(frases)])
    tema = templates.tema_canal(canal)
    pasta = projeto.p("thumbnails")
    resultado = []
    with Navegador() as nav:
        candidatos = coletar_candidatos(projeto)
        if not candidatos:
            log.info("Nenhum quadro limpo no vídeo; gerando fundos ilustrados sem texto para as capas.")
            candidatos = fundos_ilustrados(projeto, canal, nav)
        escolhidos = escolher_quadros(candidatos)
        for i, (template, frase) in enumerate(zip(TEMPLATES, frases[:3]), 1):
            quadro = Path(escolhidos[(i - 1) % len(escolhidos)]["arquivo"])
            html = templates.renderizar(f"thumbnails/{template}.html", pasta / f"thumb_{i}.html", tema,
                                        {"frase": frase, "imagem": quadro.resolve().as_uri(), "canal": canal.nome},
                                        1280, 720)
            png = nav.capturar(html, pasta / f"thumb_{i}.png", 1280, 720, t=0)
            jpg = _jpeg_ate_limite(png, pasta / f"thumb_{i}.jpg")
            png.unlink(missing_ok=True)
            html.unlink(missing_ok=True)
            resultado.append({"indice": i, "arquivo": f"thumbnails/{jpg.name}", "template": template, "frase": frase,
                              "quadro": str(quadro.relative_to(projeto.pasta)) if quadro.is_relative_to(projeto.pasta) else str(quadro),
                              "pontuacao_quadro": escolhidos[(i - 1) % len(escolhidos)]["pontuacao"]})
            log.info(f"Thumbnail {i}: template '{template}' — {frase.replace('*', '')}")
    projeto.salvar("thumbnails/thumbs.json", resultado)
    meta = projeto.ler("metadados.json", {}) or {}
    meta.setdefault("thumbnail_escolhida", 1)
    projeto.salvar("metadados.json", meta)
    projeto.marcar_etapa("thumbnails", True, quantidade=len(resultado))
    log.ok("3 thumbnails geradas")
    return resultado
