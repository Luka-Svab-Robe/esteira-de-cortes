"""Acabamento profissional dos cortes: zoom "punch-in" nos picos de reação, realce da imagem e voz tratada.

- Zoom nos picos: quando o streamer grita/ri ou o jogo explode (subida brusca de volume), a imagem dá um
  zoom seco de ~14% por 1 s e volta. Até 1 pico a cada 12 s, no máximo 3 por Short, nunca no 1º segundo.
- Realce: um pouco de contraste, saturação e nitidez (a compressão da live deixa a imagem lavada).
- Voz: corta o ronco grave e comprime levemente antes da normalização de volume (-14 LUFS).
"""
from __future__ import annotations

import math
import statistics
import subprocess
from pathlib import Path

from .. import ffmpeg

PASSO = 0.05                 # janela de volume (s)
MEDIA_CURTA = 0.3            # suavização do volume (s)
OLHAR_ATRAS = 1.0            # compara o pico com o segundo anterior (s)
ACIMA_DA_BASE_DB = 7.0       # o pico precisa estar 7 dB acima do volume típico do corte...
SUBIDA_DB = 5.0              # ...e subir 5 dB em relação ao segundo anterior
DURACAO_ZOOM = 1.1           # quanto tempo o zoom fica (s)
INTERVALO_MIN = 5.0          # distância mínima entre dois zooms (s)
SEGUNDOS_POR_ZOOM = 12.0     # no máximo 1 zoom a cada 12 s de Short
FATOR_ZOOM = 1.14

REALCE = "eq=contrast=1.05:saturation=1.12,unsharp=5:5:0.45:5:5:0"
VOZ = "highpass=f=70,acompressor=threshold=-21dB:ratio=2.5:attack=10:release=180:makeup=1.5"


def volume_db(arquivo: Path, taxa: int = 8000) -> list[float]:
    """Volume (dB RMS) a cada PASSO segundos do áudio do arquivo."""
    import numpy as np

    bruto = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(arquivo), "-vn",
                            "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"], stdout=subprocess.PIPE, check=True).stdout
    amostras = np.frombuffer(bruto, dtype="<i2").astype(np.float32) / 32768.0
    tamanho = int(taxa * PASSO)
    janelas = len(amostras) // tamanho
    if not janelas:
        return []
    blocos = amostras[: janelas * tamanho].reshape(janelas, tamanho)
    rms = np.sqrt(np.mean(blocos ** 2, axis=1))
    return [20 * math.log10(max(float(v), 1e-5)) for v in rms]


def _media_movel(valores: list[float], janela: int) -> list[float]:
    meia = max(1, janela) // 2
    return [statistics.fmean(valores[max(0, i - meia):i + meia + 1]) for i in range(len(valores))]


def picos_de_reacao(db: list[float], duracao: float, maximo: int = 3) -> list[tuple[float, float]]:
    """Janelas (início, fim) em segundos para o zoom, a partir do volume do corte."""
    if len(db) < int(3 / PASSO):
        return []
    curto = _media_movel(db, int(MEDIA_CURTA / PASSO))
    base = statistics.median(curto)
    atras, vizinhos = int(OLHAR_ATRAS / PASSO), 4
    candidatos = []
    for i in range(atras, len(curto)):
        t = i * PASSO
        if t < 0.8 or t > duracao - (DURACAO_ZOOM + 0.5):
            continue
        anterior = statistics.fmean(curto[i - atras:i - 2])
        acima, subida = curto[i] - base, curto[i] - anterior
        if acima >= ACIMA_DA_BASE_DB and subida >= SUBIDA_DB and curto[i] >= max(curto[i - vizinhos:i + vizinhos + 1]):
            candidatos.append((acima + subida, t))
    limite = min(maximo, max(1, int(duracao // SEGUNDOS_POR_ZOOM)))
    escolhidos: list[float] = []
    for _, t in sorted(candidatos, reverse=True):
        if len(escolhidos) >= limite:
            break
        if all(abs(t - e) >= INTERVALO_MIN for e in escolhidos):
            escolhidos.append(t)
    return [(round(max(0.0, t - 0.08), 2), round(min(duracao - 0.3, t + DURACAO_ZOOM), 2)) for t in sorted(escolhidos)]


def cadeia_zoom(entrada: str, saida: str, w: int, h: int, janelas: list[tuple[float, float]],
                fator: float = FATOR_ZOOM, foco_y: float = 0.45) -> str:
    """Filtro: [entrada] (w x h) -> [saida] com zoom seco nas janelas (sobrepõe uma versão ampliada)."""
    if not janelas:
        return f"[{entrada}]null[{saida}];"
    cw, ch = int(w / fator) // 2 * 2, int(h / fator) // 2 * 2
    x, y = (w - cw) // 2, int((h - ch) * foco_y)
    quando = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in janelas)
    return (f"[{entrada}]split=2[{entrada}_n][{entrada}_z];"
            f"[{entrada}_z]crop={cw}:{ch}:{x}:{y},scale={w}:{h}:flags=lanczos,setsar=1[{entrada}_zz];"
            f"[{entrada}_n][{entrada}_zz]overlay=0:0:enable='{quando}'[{saida}];")


def realce(cfg: dict) -> str:
    """Trecho de filtro de realce (com vírgula na frente) ou vazio, se desligado no identidade.yaml."""
    return f",{REALCE}" if cfg.get("realce", True) else ""


def voz(cfg: dict) -> str:
    return VOZ if cfg.get("tratar_voz", True) else "anull"
