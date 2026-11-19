"""Camada fina sobre o FFmpeg: execução, durações, encoder (CPU/GPU) e loudness."""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

from . import log
from .config import env, env_int


class ErroFFmpeg(RuntimeError):
    pass


def _portatil(nome: str) -> str | None:
    """FFmpeg portátil que o instalador do Windows deixa em ferramentas/ffmpeg/bin (fora do PATH)."""
    from .config import RAIZ

    alvo = RAIZ / "ferramentas" / "ffmpeg" / "bin" / (nome + (".exe" if platform.system() == "Windows" else ""))
    return str(alvo) if alvo.exists() else None


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    caminho = env("FFMPEG_BIN") or shutil.which("ffmpeg") or _portatil("ffmpeg")
    if not caminho:
        try:
            import imageio_ffmpeg  # type: ignore

            caminho = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            caminho = None
    if not caminho:
        raise ErroFFmpeg(
            "FFmpeg não encontrado. Instale com: Windows 'winget install Gyan.FFmpeg', "
            "macOS 'brew install ffmpeg', Linux 'sudo apt install ffmpeg' (ou defina FFMPEG_BIN no .env)."
        )
    return caminho


@lru_cache(maxsize=1)
def ffprobe_bin() -> str | None:
    caminho = env("FFPROBE_BIN") or shutil.which("ffprobe") or _portatil("ffprobe")
    if caminho:
        return caminho
    vizinho = Path(ffmpeg_bin()).with_name("ffprobe" + (".exe" if platform.system() == "Windows" else ""))
    return str(vizinho) if vizinho.exists() else None


def _cauda(texto: str, linhas: int = 12) -> str:
    return "\n".join(texto.strip().splitlines()[-linhas:])


def rodar(args: list, cwd: Path | None = None, descricao: str = "FFmpeg") -> str:
    """Executa o FFmpeg e devolve o stderr (onde ficam as medições)."""
    comando = [ffmpeg_bin(), "-hide_banner", "-y", *[str(a) for a in args]]
    log.debug("ffmpeg " + " ".join(comando[1:]))
    proc = subprocess.run(comando, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    saida = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise ErroFFmpeg(f"{descricao} falhou:\n{_cauda(saida)}")
    return saida


def duracao(caminho: Path) -> float:
    caminho = Path(caminho)
    if caminho.suffix.lower() == ".wav":
        try:
            with wave.open(str(caminho), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass
    probe = ffprobe_bin()
    if probe:
        proc = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(caminho)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            return float(proc.stdout.decode().strip())
        except ValueError:
            pass
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(caminho)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr.decode("utf-8", "replace"))
    if not m:
        raise ErroFFmpeg(f"Não foi possível medir a duração de {caminho}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def info_midia(caminho: Path) -> dict:
    probe = ffprobe_bin()
    if not probe:
        return {"duracao": duracao(caminho)}
    proc = subprocess.run([probe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                           str(caminho)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        dados = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        return {"duracao": duracao(caminho)}
    info = {"duracao": float(dados.get("format", {}).get("duration", 0) or 0), "tem_audio": False}
    for s in dados.get("streams", []):
        if s.get("codec_type") == "video" and "largura" not in info:
            info["largura"], info["altura"] = int(s.get("width", 0)), int(s.get("height", 0))
        if s.get("codec_type") == "audio":
            info["tem_audio"] = True
    return info


@lru_cache(maxsize=1)
def filtros_disponiveis() -> frozenset:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    nomes = set()
    for linha in proc.stdout.decode("utf-8", "replace").splitlines():
        partes = linha.split()
        if len(partes) >= 3 and re.fullmatch(r"[A-Z.|]{2,3}", partes[0]):
            nomes.add(partes[1])
    return frozenset(nomes)


def tem_filtro(nome: str) -> bool:
    return nome in filtros_disponiveis()


def _testar_encoder(codec: str) -> bool:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                           "color=c=black:s=320x240:d=0.3:r=30", "-c:v", codec, "-f", "null", "-"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode == 0


@lru_cache(maxsize=1)
def encoder_video() -> str:
    """Escolhe o encoder H.264: usa GPU quando existe e funciona, senão libx264 (CPU)."""
    preferido = env("VIDEO_ENCODER", "auto").lower()
    if preferido != "auto":
        return preferido
    sistema = platform.system()
    candidatos = ["h264_videotoolbox"] if sistema == "Darwin" else ["h264_nvenc", "h264_qsv", "h264_amf"]
    for codec in candidatos:
        if _testar_encoder(codec):
            log.info(f"Aceleração de vídeo por hardware detectada: {codec}")
            return codec
    return "libx264"


def args_video(perfil: str = "final") -> list[str]:
    """Argumentos de codificação. perfil: final | short | cena | intermediario.

    short = Shorts de cortes: qualidade mais alta (o YouTube recomprime; quanto melhor o envio, melhor fica)."""
    codec = encoder_video()
    crf = {"final": env_int("VIDEO_CRF", 20), "short": env_int("VIDEO_CRF_SHORT", 18), "cena": 18,
           "intermediario": 15}.get(perfil, 20)
    if codec == "libx264":
        preset = env("VIDEO_PRESET_SHORT", "medium") if perfil == "short" else env("VIDEO_PRESET", "veryfast")
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "0",
                "-pix_fmt", "yuv420p"]
    if codec == "h264_videotoolbox":
        taxa = {"final": "14M", "cena": "20M", "intermediario": "30M"}.get(perfil, "14M")
        return ["-c:v", "h264_videotoolbox", "-b:v", taxa, "-pix_fmt", "yuv420p"]
    if codec == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf), "-pix_fmt", "nv12"]
    if codec == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf + 2), "-pix_fmt", "yuv420p"]
    return ["-c:v", codec, "-pix_fmt", "yuv420p"]


# ---------------------------------------------------------------------------
# Loudness (EBU R128) — padrão YouTube: -14 LUFS integrado, pico real -1.5 dBTP
# ---------------------------------------------------------------------------
def _extrair_json_loudnorm(stderr: str) -> dict:
    blocos = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.S)
    if not blocos:
        raise ErroFFmpeg("Não foi possível ler a medição de loudness do FFmpeg.")
    return json.loads(blocos[-1])


def medir_loudness(entrada: Path, alvo_i: float = -14.0, alvo_tp: float = -1.5, lra: float = 11.0) -> dict:
    stderr = rodar(["-i", entrada, "-vn", "-af",
                    f"loudnorm=I={alvo_i}:TP={alvo_tp}:LRA={lra}:print_format=json", "-f", "null", "-"],
                   descricao="Medição de loudness")
    dados = _extrair_json_loudnorm(stderr)
    return {k: float(v) if re.fullmatch(r"-?\d+(\.\d+)?|-?inf", str(v)) else v for k, v in dados.items()}


def filtro_loudnorm(medicao: dict, alvo_i: float = -14.0, alvo_tp: float = -1.5, lra: float = 11.0) -> str:
    def valor(chave: str, padrao: float) -> float:
        v = medicao.get(chave, padrao)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return padrao
        return padrao if v in (float("inf"), float("-inf")) else v

    return (f"loudnorm=I={alvo_i}:TP={alvo_tp}:LRA={lra}"
            f":measured_I={valor('input_i', -24):.2f}:measured_TP={valor('input_tp', -2):.2f}"
            f":measured_LRA={valor('input_lra', 7):.2f}:measured_thresh={valor('input_thresh', -34):.2f}"
            f":offset={valor('target_offset', 0):.2f}:linear=true:print_format=summary")


def normalizar_audio(entrada: Path, saida: Path, alvo_i: float = -14.0, alvo_tp: float = -1.5,
                     lra: float = 11.0, codec: list[str] | None = None) -> dict:
    """Normalização em duas passagens. Devolve a medição final do arquivo gerado."""
    medicao = medir_loudness(entrada, alvo_i, alvo_tp, lra)
    codec = codec or ["-c:a", "pcm_s16le"]
    rodar(["-i", entrada, "-vn", "-af", filtro_loudnorm(medicao, alvo_i, alvo_tp, lra) + ",aresample=48000",
           "-ar", "48000", *codec, saida], descricao="Normalização de loudness")
    return medir_loudness(saida, alvo_i, alvo_tp, lra)


def ajustar_pico(entrada_video: Path, saida_video: Path, ganho_db: float, bitrate: str = "320k") -> None:
    """Reduz o ganho do áudio (copiando o vídeo) quando a codificação AAC estoura o pico."""
    rodar(["-i", entrada_video, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af",
           f"volume={ganho_db:.2f}dB", "-c:a", "aac", "-b:a", bitrate, "-ar", "48000",
           "-movflags", "+faststart", saida_video], descricao="Ajuste de pico de áudio")
