"""Baixa um trecho de VOD em HLS (Twitch, Kick) lendo a lista de pedaços direto, sem passar pelo modo "ao vivo".

Por que não só o yt-dlp: com --download-sections ele entrega o HLS ao FFmpeg, que trata a lista de uma live
AINDA NO AR (sem #EXT-X-ENDLIST) como transmissão ao vivo — não consegue pular para o minuto pedido e fica
parado. Aqui a lista é lida uma vez, só os pedaços (≈10 s cada) que cobrem [início, fim] são baixados, em
paralelo, e o FFmpeg corta no quadro exato. Funciona igual em VOD terminado e em live em andamento.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from .. import ffmpeg, log

PARALELOS = 8
TENTATIVAS = 4
TEMPO_LIMITE_SEG = 30
AVISAR_A_CADA = 10  # % entre as linhas de progresso (o painel lê "[download] N%")


class ErroHLS(RuntimeError):
    pass


@dataclass(frozen=True)
class Pedaco:
    inicio: float  # segundo do VOD em que o pedaço começa
    duracao: float
    url: str


def ler_playlist(texto: str, base: str) -> tuple[str | None, list[Pedaco]]:
    """(URL do segmento de inicialização ou None, pedaços em ordem) de uma media playlist HLS."""
    init, pedacos, tempo, duracao = None, [], 0.0, None
    for linha in (x.strip() for x in texto.splitlines()):
        if linha.startswith("#EXT-X-MAP:"):
            achado = re.search(r'URI="([^"]+)"', linha)
            init = urljoin(base, achado.group(1)) if achado else init
        elif linha.startswith("#EXTINF:"):
            duracao = float(linha[len("#EXTINF:"):].split(",")[0])
        elif linha and not linha.startswith("#") and duracao is not None:
            pedacos.append(Pedaco(round(tempo, 3), duracao, urljoin(base, linha)))
            tempo += duracao
            duracao = None
    return init, pedacos


def selecionar(pedacos: list[Pedaco], inicio: float, fim: float) -> list[Pedaco]:
    return [p for p in pedacos if p.inicio + p.duracao > inicio and p.inicio < fim]


def escolher_formato(formatos: list[dict], altura: int) -> dict | None:
    """Maior resolução HLS com vídeo até `altura` (no empate, a de menos quadros por segundo: arquivo menor)."""
    candidatos = [f for f in formatos if str(f.get("protocol", "")).startswith("m3u8") and f.get("height")
                  and f.get("vcodec") != "none" and int(f["height"]) <= int(altura)]
    if not candidatos:
        return None
    return max(candidatos, key=lambda f: (int(f["height"]), -float(f.get("fps") or 30)))


def _sessao(cabecalhos: dict):
    import requests
    from requests.adapters import HTTPAdapter

    sessao = requests.Session()
    sessao.headers.update({k: v for k, v in (cabecalhos or {}).items() if isinstance(v, str)})
    sessao.mount("https://", HTTPAdapter(pool_maxsize=PARALELOS))
    return sessao


def _baixar_pedaco(sessao, url: str) -> bytes:
    ultimo = ""
    for _ in range(TENTATIVAS):
        try:
            resposta = sessao.get(url, timeout=TEMPO_LIMITE_SEG)
        except Exception as exc:  # rede instável: tenta de novo
            ultimo = str(exc)
            continue
        if resposta.status_code == 403 and "-unmuted" in url:  # Twitch: trecho com música silenciada
            url = url.replace("-unmuted", "-muted")
            continue
        if resposta.ok and resposta.content:
            return resposta.content
        ultimo = f"HTTP {resposta.status_code}"
    raise ErroHLS(f"pedaço {url.rsplit('/', 1)[-1]} não baixou ({ultimo})")


def _media_playlist(sessao, url: str) -> tuple[str, str]:
    """Texto e endereço da media playlist (se vier a master, segue a primeira variante)."""
    resposta = sessao.get(url, timeout=TEMPO_LIMITE_SEG)
    resposta.raise_for_status()
    texto = resposta.text
    if "#EXT-X-STREAM-INF" in texto:
        variante = next((x.strip() for x in texto.splitlines() if x.strip() and not x.startswith("#")), "")
        if not variante:
            raise ErroHLS("lista HLS sem variantes")
        return _media_playlist(sessao, urljoin(url, variante))
    return texto, url


def baixar(formatos: list[dict], pasta: Path, inicio: float, fim: float, altura: int, nome: str,
           preset: str = "veryfast", crf: int = 16) -> Path:
    """Baixa [inicio, fim] (segundos do VOD) em `pasta/nome.mp4`, cortado no quadro exato."""
    formato = escolher_formato(formatos, altura)
    if not formato:
        raise ErroHLS("a live não tem vídeo em HLS")
    sessao = _sessao(formato.get("http_headers") or {})
    texto, base = _media_playlist(sessao, formato["url"])
    init, pedacos = ler_playlist(texto, base)
    escolhidos = selecionar(pedacos, inicio, fim)
    if not escolhidos:
        total = pedacos[-1].inicio + pedacos[-1].duracao if pedacos else 0
        raise ErroHLS(f"o trecho {inicio:.0f}s–{fim:.0f}s está fora do vídeo ({total:.0f}s gravados)")
    pasta.mkdir(parents=True, exist_ok=True)
    juntado = pasta / f"{nome}.hls.tmp"
    saida = pasta / f"{nome}.mp4"
    log.info(f"Baixando {len(escolhidos)} pedaços da live em {formato.get('height')}p ({fim - inicio:.0f}s)…")
    try:
        with open(juntado, "wb") as arquivo, ThreadPoolExecutor(PARALELOS) as fila:
            if init:
                arquivo.write(_baixar_pedaco(sessao, init))
            proximo_aviso = AVISAR_A_CADA
            for n, conteudo in enumerate(fila.map(lambda p: _baixar_pedaco(sessao, p.url), escolhidos), 1):
                arquivo.write(conteudo)  # map devolve na ordem: o arquivo sai na sequência certa
                feito = 100 * n / len(escolhidos)
                if feito >= proximo_aviso:
                    log.info(f"[download] {feito:5.1f}% ({n}/{len(escolhidos)} pedaços)")
                    proximo_aviso += AVISAR_A_CADA
        ffmpeg.rodar(["-ss", f"{max(0.0, inicio - escolhidos[0].inicio):.3f}", "-i", juntado, "-t", f"{fim - inicio:.3f}",
                      "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", saida],
                     descricao="Corte do trecho baixado")
    finally:
        juntado.unlink(missing_ok=True)
    return saida
