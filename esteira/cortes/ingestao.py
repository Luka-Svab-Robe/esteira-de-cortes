"""Olheiro: baixa lives/VODs da Twitch, da Kick e do YouTube com yt-dlp (vídeo, chat, legendas) e acha lives novas."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import log
from ..config import env


class ErroIngestao(RuntimeError):
    pass


def _runtime_js() -> list[str]:
    """O YouTube exige um runtime JavaScript para o yt-dlp (Deno ou Node)."""
    caminho = env("YTDLP_JS_RUNTIME")
    if caminho:
        nome = "deno" if "deno" in Path(caminho).name else "node"
        return ["--js-runtimes", f"{nome}:{caminho}"]
    for nome in ("deno", "node"):
        achado = shutil.which(nome)
        if achado:
            return ["--js-runtimes", f"{nome}:{achado}"]
    from ..config import RAIZ

    portatil = RAIZ / "ferramentas" / "deno" / ("deno.exe" if os.name == "nt" else "deno")
    if portatil.exists():  # deixado pelo instalador do Windows
        return ["--js-runtimes", f"deno:{portatil}"]
    nvm = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))
    if nvm:  # agendadores (cron/systemd) não carregam o PATH do nvm
        return ["--js-runtimes", f"node:{nvm[-1]}"]
    return []


def _ytdlp(args: list, descricao: str, capturar: bool = True, timeout: int | None = None) -> str:
    comando = [sys.executable, "-m", "yt_dlp", "--no-warnings", *_runtime_js(), *[str(a) for a in args]]
    log.debug("yt-dlp " + " ".join(comando[3:]))
    try:
        proc = subprocess.run(comando, stdout=subprocess.PIPE if capturar else None, stderr=subprocess.PIPE,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ErroIngestao(f"{descricao} passou do limite de {timeout / 3600:.1f} h e foi interrompido.") from exc
    if proc.returncode != 0:
        erro = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ErroIngestao(f"{descricao} falhou: {' | '.join(erro)}")
    return proc.stdout.decode("utf-8", "replace") if capturar else ""


def info(url: str) -> dict:
    return json.loads(_ytdlp(["-J", "--no-playlist", url], "Leitura da live", timeout=180))


def info_canal(url: str) -> dict:
    """Dados do canal (id, nome) a partir do link do canal ou de um vídeo dele."""
    try:
        return json.loads(_ytdlp(["-J", "--flat-playlist", "--playlist-end", "1", url], "Leitura do canal", timeout=180))
    except Exception:
        return info(url)


def formato_direto(altura: int = 1080) -> str:
    """Prefere até 30 fps (60 fps dobra o tamanho e o Short sai em 30 fps de qualquer jeito)."""
    h = int(altura)
    return (f"bv*[height<={h}][fps<=30][ext=mp4]+ba[ext=m4a]/bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={h}][fps<=30]+ba/bv*[height<={h}]+ba/b[height<={h}]/b")


def formato_hls(altura: int = 1080) -> str:
    h = int(altura)
    return (f"bv*[height<={h}][fps<=30][protocol^=m3u8]+ba[protocol^=m3u8]/"
            f"bv*[height<={h}][protocol^=m3u8]+ba[protocol^=m3u8]/b[height<={h}][protocol^=m3u8]")
_RECUSAS = ("403", "forbidden", "requested format is not available", "fragment", "unable to download video data")


def baixar_video(url: str, pasta: Path, ao_vivo: bool = False, limite_seg: int | None = None,
                 altura: int = 1080, secao: tuple[float, float] | None = None, nome: str = "fonte") -> Path:
    """Baixa a live (até `altura`p, MP4, áudio original) pelo downloader nativo, em pedaços.

    No YouTube, baixar só um trecho (--download-sections) passa pelo FFmpeg numa única conexão, que o YouTube
    limita à velocidade de reprodução (1 h de trecho = 1 h de download): lá a live inteira é baixada e o trecho
    pedido limita só a análise. Na Twitch e na Kick (HLS sem limite) `secao` baixa só [início, fim] em segundos,
    com o corte exato no quadro (--force-keyframes-at-cuts). Se o YouTube recusar o download direto (403), tenta
    de novo pelo HLS (fragmentos .m3u8), que costuma ser liberado. ao_vivo grava a live desde o início.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    comuns = ["--merge-output-format", "mp4", "-o", str(pasta / f"{nome}.%(ext)s"), "--no-playlist",
              "--http-chunk-size", "10M", "--concurrent-fragments", "8", "--retries", "10",
              "--fragment-retries", "10", "--newline", "--progress-delta", "15"]
    if ao_vivo:
        comuns += ["--live-from-start", "--wait-for-video", "60"]
    if secao:
        # o corte exato recodifica o trecho; sem isto o FFmpeg usa o x264 "medium" (lento). Cópia de análise: rápida;
        # trecho em HD (vira Short): qualidade alta, porque ainda passa por mais duas codificações
        preset, crf = ("ultrafast", 24) if altura <= 540 else ("veryfast", 16)
        comuns += ["--download-sections", f"*{max(0.0, secao[0]):.2f}-{secao[1]:.2f}", "--force-keyframes-at-cuts",
                   "--downloader-args", f"ffmpeg_o:-c:v libx264 -preset {preset} -crf {crf} -c:a aac -b:a 192k"]
    log.info(f"Baixando o vídeo da live em até {altura}p" + (" (gravando desde o início)" if ao_vivo else "")
             + (f" — só o trecho {secao[0]:.0f}s–{secao[1]:.0f}s" if secao else "") + "…")
    tentativas = [("direto", formato_direto(altura)), ("HLS", formato_hls(altura))]
    for n, (rotulo, formato) in enumerate(tentativas):
        try:
            _ytdlp(["-f", formato, *comuns, url], "Download do vídeo", capturar=False, timeout=limite_seg)
            break
        except ErroIngestao as exc:
            recusado = any(r in str(exc).lower() for r in _RECUSAS)
            if not recusado or n == len(tentativas) - 1:
                raise
            log.aviso(f"A plataforma recusou o download {rotulo}; tentando pelo formato {tentativas[n + 1][0]}…")
            for resto in pasta.glob(f"{nome}.*"):
                resto.unlink(missing_ok=True)
    for resto in pasta.glob(f"{nome}.*.part"):
        resto.unlink(missing_ok=True)
    arquivos = sorted((a for a in pasta.glob(f"{nome}.*") if a.suffix not in (".part", ".ytdl")),
                      key=lambda a: a.stat().st_size, reverse=True)
    if not arquivos:
        raise ErroIngestao("O download terminou sem gerar o arquivo de vídeo.")
    return arquivos[0]


def baixar_trecho(url: str, pasta: Path, inicio: float, fim: float, altura: int, nome: str) -> Path:
    """Twitch/Kick: só [inicio, fim] (segundos do VOD), lendo os pedaços HLS direto — funciona até em live ainda
    no ar. Se isso falhar, tenta o --download-sections do yt-dlp (só serve para VOD terminado)."""
    from . import hls

    preset, crf = ("ultrafast", 24) if altura <= 540 else ("veryfast", 16)  # análise rápida | Short em alta
    try:
        return hls.baixar(info(url).get("formats") or [], pasta, inicio, fim, altura, nome, preset, crf)
    except Exception as exc:
        log.aviso(f"Download direto dos pedaços falhou ({str(exc)[-160:]}); tentando pelo yt-dlp…")
        for resto in pasta.glob(f"{nome}.*"):
            resto.unlink(missing_ok=True)
        return baixar_video(url, pasta, altura=altura, secao=(inicio, fim), nome=nome)


def baixar_chat(url: str, pasta: Path) -> Path | None:
    """Replay do chat da live (só YouTube, quando o canal mantém o chat)."""
    try:
        _ytdlp(["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(pasta / "chat.%(ext)s"),
                "--no-playlist", url], "Download do chat", timeout=1800)
    except ErroIngestao as exc:
        log.debug(f"Chat indisponível: {exc}")
    achados = list(pasta.glob("chat*.json"))
    return achados[0] if achados else None


def baixar_legendas(url: str, pasta: Path, idioma: str) -> Path | None:
    idiomas = f"{idioma}.*,{idioma},{idioma}-orig" if idioma else "pt.*,pt,en.*,en"
    try:
        _ytdlp(["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", idiomas, "--sub-format", "vtt",
                "-o", str(pasta / "legenda.%(ext)s"), "--no-playlist", url], "Download das legendas", timeout=300)
    except ErroIngestao as exc:
        log.debug(f"Legendas indisponíveis: {exc}")
    achados = sorted(pasta.glob("legenda*.vtt"), key=lambda p: ("orig" not in p.name, len(p.name)))
    return achados[0] if achados else None


def lives_recentes(url_canal: str, plataforma: str, limite: int = 5) -> list[dict]:
    """Últimas lives do canal (em andamento ou encerradas), da mais nova para a mais antiga."""
    base = url_canal.rstrip("/")
    lista = {"youtube": f"{base}/streams", "kick": f"{base}/videos"}.get(plataforma,
                                                                      f"{base}/videos?filter=archives&sort=time")
    saida = _ytdlp(["--flat-playlist", "--playlist-end", str(limite), "-J", lista], "Lista de lives", timeout=180)
    entradas = []
    for e in json.loads(saida).get("entries", []) or []:
        if e.get("live_status") in ("is_upcoming", "not_live"):
            continue
        miniatura = (f"https://i.ytimg.com/vi/{e.get('id')}/mqdefault.jpg" if plataforma == "youtube"
                     else next((t.get("url") for t in reversed(e.get("thumbnails") or []) if t.get("url")), "")
                     or e.get("thumbnail") or "")  # a Twitch manda uma só, em "thumbnail"
        link = (e.get("url") or e.get("webpage_url") or "").split("#__youtubedl_smuggle")[0]  # Kick anexa dados internos
        entradas.append({"id": e.get("id"), "url": link, "titulo": e.get("title", ""),
                         "status": e.get("live_status") or "was_live", "duracao": e.get("duration"),
                         "visualizacoes": e.get("view_count"), "miniatura": miniatura})
    if plataforma in ("twitch", "kick"):  # live em andamento na Twitch/Kick fica na URL do canal
        try:
            atual = info(base)
            if atual.get("is_live"):
                entradas.insert(0, {"id": atual.get("id"), "url": base, "titulo": atual.get("title", ""),
                                    "status": "is_live"})
        except ErroIngestao:
            pass
    return entradas
