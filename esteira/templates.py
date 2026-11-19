"""Renderização dos templates HTML/CSS (cenas, sobreposições e thumbnails).

As fontes são embutidas como data URI para funcionar offline e sem
problemas de permissão de arquivo em nenhum sistema operacional.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from .config import PASTA_FONTES, PASTA_TEMPLATES

# família -> [(arquivo, peso)]
FONTES_CONHECIDAS: dict[str, list[tuple[str, int]]] = {
    "Anton": [("Anton-Regular.ttf", 400)],
    "Bebas Neue": [("BebasNeue-Regular.ttf", 400)],
    "Archivo Black": [("ArchivoBlack-Regular.ttf", 400)],
    "DM Serif Display": [("DMSerifDisplay-Regular.ttf", 400)],
    "Special Elite": [("SpecialElite-Regular.ttf", 400)],
    "Poppins": [("Poppins-Regular.ttf", 400), ("Poppins-SemiBold.ttf", 600), ("Poppins-Bold.ttf", 700),
                ("Poppins-Black.ttf", 900)],
}

# Download feito pelo instalador (Google Fonts, licença OFL / Apache).
_BASE_GF = "https://raw.githubusercontent.com/google/fonts/main/"
URLS_FONTES = {
    "Anton-Regular.ttf": _BASE_GF + "ofl/anton/Anton-Regular.ttf",
    "BebasNeue-Regular.ttf": _BASE_GF + "ofl/bebasneue/BebasNeue-Regular.ttf",
    "ArchivoBlack-Regular.ttf": _BASE_GF + "ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "DMSerifDisplay-Regular.ttf": _BASE_GF + "ofl/dmserifdisplay/DMSerifDisplay-Regular.ttf",
    "SpecialElite-Regular.ttf": _BASE_GF + "apache/specialelite/SpecialElite-Regular.ttf",
    "Poppins-Regular.ttf": _BASE_GF + "ofl/poppins/Poppins-Regular.ttf",
    "Poppins-SemiBold.ttf": _BASE_GF + "ofl/poppins/Poppins-SemiBold.ttf",
    "Poppins-Bold.ttf": _BASE_GF + "ofl/poppins/Poppins-Bold.ttf",
    "Poppins-Black.ttf": _BASE_GF + "ofl/poppins/Poppins-Black.ttf",
}

RESERVA_TITULOS = "'Impact','Arial Black','Helvetica Neue',Arial,sans-serif"
RESERVA_CORPO = "'Segoe UI','Helvetica Neue',Arial,'Liberation Sans',sans-serif"


@lru_cache(maxsize=32)
def css_fontes(familias: tuple[str, ...]) -> str:
    regras = []
    for familia in familias:
        for arquivo, peso in FONTES_CONHECIDAS.get(familia, []):
            caminho = PASTA_FONTES / arquivo
            if not caminho.exists():
                continue
            dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
            regras.append(
                f"@font-face{{font-family:'{familia}';font-weight:{peso};font-style:normal;"
                f"src:url(data:font/ttf;base64,{dados}) format('truetype');}}")
    return "\n".join(regras)


@lru_cache(maxsize=1)
def grao_data_uri() -> str:
    """Textura de granulação (PNG 160x160) gerada uma vez. Bem mais leve de compor a cada quadro
    do que um filtro SVG feTurbulence com mix-blend-mode."""
    import io

    import numpy as np
    from PIL import Image

    gerador = np.random.default_rng(7)
    ruido = gerador.integers(0, 256, (160, 160), dtype=np.uint8)
    alfa = np.full((160, 160), 255, dtype=np.uint8)
    imagem = Image.fromarray(np.stack([ruido, ruido, ruido, alfa], axis=-1), "RGBA")
    buffer = io.BytesIO()
    imagem.save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def fonte_disponivel(familia: str) -> bool:
    return any((PASTA_FONTES / arq).exists() for arq, _ in FONTES_CONHECIDAS.get(familia, []))


@lru_cache(maxsize=1)
def ambiente():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    return Environment(loader=FileSystemLoader(str(PASTA_TEMPLATES)),
                       autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def tema_canal(canal) -> dict:
    """Variáveis visuais do canal usadas por todos os templates."""
    paleta = dict(canal.paleta)
    fonte_titulos = canal.tipografia.get("titulos", "Anton")
    fonte_corpo = canal.tipografia.get("corpo", "Poppins")
    return {
        "paleta": paleta,
        "estilo": canal.estilo,
        "nome_canal": canal.nome,
        "handle": canal.handle,
        "fonte_titulos": fonte_titulos,
        "fonte_corpo": fonte_corpo,
        "fonte_titulos_css": f"'{fonte_titulos}',{RESERVA_TITULOS}",
        "fonte_corpo_css": f"'{fonte_corpo}',{RESERVA_CORPO}",
        "css_fontes": css_fontes(tuple(sorted({fonte_titulos, fonte_corpo}))),
        "grao": grao_data_uri(),
        "destaque_posicao": canal.cfg["identidade_visual"].get("destaque_posicao", "inferior"),
    }


PESADOS = ("css_fontes", "grao")  # fora do JS embutido e do hash de cache


def renderizar(nome_template: str, destino: Path, tema: dict, dados: dict | None = None,
               largura: int = 1920, altura: int = 1080, transparente: bool = False) -> Path:
    """Gera o HTML final (com dados embutidos) em `destino` e devolve o caminho."""
    modelo = ambiente().get_template(nome_template)
    html = modelo.render(tema=tema, dados=dados or {}, largura=largura, altura=altura,
                         transparente=transparente,
                         tema_js={k: v for k, v in tema.items() if k not in PESADOS})
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    return destino
