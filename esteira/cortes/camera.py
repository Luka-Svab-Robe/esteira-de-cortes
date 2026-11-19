"""Câmera do streamer: acha a webcam no vídeo e decide o layout profissional de cada Short.

  webcam pequena sobre o jogo (o normal na Twitch/Kick) -> "dividido": câmera em cima, jogo embaixo
  rosto grande ocupando a tela (bate-papo, IRL)          -> "vertical": recorte 9:16 que segue o rosto
  sem rosto fixo (só o jogo)                             -> "moldura": modelo do canal, vídeo inteiro no centro

A webcam é o rosto que fica PARADO no mesmo lugar na maior parte do corte; rostos de personagens do jogo
(jogos de mundo aberto têm muitos) mudam de lugar e não formam um grupo estável, então não enganam a detecção.
"""
from __future__ import annotations

import os
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path

from .. import ffmpeg, log

LAYOUTS = ("auto", "dividido", "moldura", "vertical")
LARGURA, ALTURA = 1080, 1920
CAM_ALTURA = 680                      # faixa da câmera no topo do Short dividido (1080 x 680)
JOGO_ALTURA = ALTURA - CAM_ALTURA     # faixa do jogo embaixo (1080 x 1240)
ROSTO_GRANDE = 0.13                   # rosto com mais de 13% da largura do quadro = câmera cheia, não webcam
PRESENCA_MIN = 0.4                    # o rosto precisa aparecer em 40% das amostras no mesmo lugar
ROSTOS_POR_CAIXA = 3.2                # largura do recorte da câmera em "larguras de rosto" (rosto + ombros)
ALTURA_DO_ROSTO_NA_CAIXA = 0.45       # centro do rosto a 45% do topo do recorte (sobra espaço para o queixo)


@dataclass(frozen=True)
class Facecam:
    cx: float        # centro do rosto (0 a 1 da largura)
    cy: float        # centro do rosto (0 a 1 da altura)
    rosto: float     # largura do rosto (0 a 1 da largura do quadro)
    presenca: float  # fração das amostras em que o rosto estava ali


def agrupar(amostras: list[list[tuple[float, float, float]]], largura: int, altura: int) -> Facecam | None:
    """Acha o rosto fixo. amostras: por quadro, lista de rostos (cx, cy, w) em pixels do quadro amostrado."""
    if not amostras:
        return None
    grupos: list[dict] = []
    for indice, rostos in enumerate(amostras):
        for cx, cy, w in rostos:
            for g in grupos:
                mx, my, mw = (statistics.median(g[k]) for k in ("cx", "cy", "w"))
                perto = abs(cx - mx) <= max(0.6 * mw, 0.03 * largura) and abs(cy - my) <= max(0.8 * mw, 0.04 * altura)
                if perto and 0.6 <= w / mw <= 1.6:
                    g["cx"].append(cx), g["cy"].append(cy), g["w"].append(w), g["amostras"].add(indice)
                    break
            else:
                grupos.append({"cx": [cx], "cy": [cy], "w": [w], "amostras": {indice}})
    if not grupos:
        return None
    melhor = max(grupos, key=lambda g: (len(g["amostras"]), statistics.median(g["w"])))
    presenca = len(melhor["amostras"]) / len(amostras)
    if presenca < PRESENCA_MIN:
        return None
    return Facecam(cx=statistics.median(melhor["cx"]) / largura, cy=statistics.median(melhor["cy"]) / altura,
                   rosto=statistics.median(melhor["w"]) / largura, presenca=round(presenca, 2))


def escolher_layout(facecam: Facecam | None) -> str:
    if facecam is None:
        return "moldura"
    return "vertical" if facecam.rosto >= ROSTO_GRANDE else "dividido"


def detectar(video: Path, pasta_tmp: Path, fps: float = 1.0, largura_amostra: int = 960) -> Facecam | None:
    """Amostra o corte (1 quadro/s, 960 px: webcam pequena ainda tem rosto detectável) e procura o rosto fixo."""
    try:
        import cv2

        frontal = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        if frontal.empty():
            raise RuntimeError("modelo Haar não carregou")
    except Exception as exc:  # OpenCV ausente ou 5.x (sem Haar)
        log.aviso(f"Detecção da câmera indisponível ({exc}); usando a moldura do canal. Rode: python instalar.py")
        return None
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg.rodar(["-i", video, "-vf", f"fps={fps},scale={largura_amostra}:-2", "-q:v", "3", pasta_tmp / "c_%04d.jpg"],
                     descricao="Amostras para achar a câmera")
        amostras, tamanho = [], (largura_amostra, int(largura_amostra * 9 / 16))
        for arquivo in sorted(pasta_tmp.glob("c_*.jpg")):
            imagem = cv2.imread(str(arquivo))
            if imagem is None:
                continue
            tamanho = (imagem.shape[1], imagem.shape[0])
            cinza = cv2.equalizeHist(cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY))
            rostos = frontal.detectMultiScale(cinza, scaleFactor=1.08, minNeighbors=6, minSize=(26, 26))
            amostras.append([(x + w / 2, y + h / 2, float(w)) for x, y, w, h in rostos])
    finally:
        shutil.rmtree(pasta_tmp, ignore_errors=True)
    return agrupar(amostras, *tamanho)


def _par(valor: float) -> int:
    return max(2, int(valor) // 2 * 2)


def caixa_camera(fc: Facecam, largura: int, altura: int) -> tuple[int, int, int, int]:
    """Recorte (x, y, w, h) da webcam no quadro original, no formato da faixa de cima do Short."""
    aspecto = LARGURA / CAM_ALTURA
    w = min(max(fc.rosto * largura * ROSTOS_POR_CAIXA, 0.16 * largura), 0.6 * largura)
    h = w / aspecto
    if h > altura:
        h, w = altura, altura * aspecto
    x = min(max(fc.cx * largura - w / 2, 0), largura - w)
    y = min(max(fc.cy * altura - ALTURA_DO_ROSTO_NA_CAIXA * h, 0), altura - h)
    return _par(x), _par(y), _par(w), _par(h)


def caixa_jogo(largura: int, altura: int, cam: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int]:
    """Recorte do jogo (altura inteira, centro da tela). Se a webcam cair dentro dele, desliza para o outro lado."""
    w = min(largura, _par(altura * LARGURA / JOGO_ALTURA))
    x = (largura - w) // 2
    if cam:
        cx, _, cw, _ = cam
        if cx < x + w and cx + cw > x:  # a webcam está dentro do recorte central
            x = min(largura - w, cx + cw) if cx + cw / 2 < largura / 2 else max(0, cx - w)
    return _par(x), 0, w, _par(altura)
