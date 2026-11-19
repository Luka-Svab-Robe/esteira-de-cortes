"""Download dos modelos locais: voz Kokoro ONNX e reconhecedor Faster-Whisper."""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from . import log
from .config import PASTA_MODELOS, caminho_env, env

BASE_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
ARQUIVOS_KOKORO = {"int8": "kokoro-v1.0.int8.onnx", "completo": "kokoro-v1.0.onnx"}
VOZES_KOKORO = "voices-v1.0.bin"


def baixar_arquivo(url: str, destino: Path, rotulo: str) -> Path:
    if destino.exists() and destino.stat().st_size > 1024:
        log.ok(f"{rotulo} já está em {destino}")
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "EsteiraCanalDark/1.0"}) as resposta:
        resposta.raise_for_status()
        total = int(resposta.headers.get("Content-Length", 0))
        baixado, ultimo = 0, -1
        with open(parcial, "wb") as f:
            for pedaco in resposta.iter_content(1 << 20):
                f.write(pedaco)
                baixado += len(pedaco)
                if total:
                    pct = int(baixado * 100 / total)
                    if pct // 10 != ultimo:
                        ultimo = pct // 10
                        sys.stdout.write(f"\r  • {rotulo}: {pct}% de {total / 1e6:.0f} MB")
                        sys.stdout.flush()
    sys.stdout.write("\n")
    parcial.replace(destino)
    log.ok(f"{rotulo} salvo em {destino}")
    return destino


def baixar_kokoro(variante: str = "int8") -> None:
    arquivo = ARQUIVOS_KOKORO.get(variante, ARQUIVOS_KOKORO["int8"])
    destino_modelo = caminho_env("KOKORO_MODELO", f"modelos/kokoro/{arquivo}")
    if destino_modelo.name not in ARQUIVOS_KOKORO.values():
        destino_modelo = destino_modelo.parent / arquivo
    baixar_arquivo(BASE_KOKORO + destino_modelo.name, destino_modelo, f"Modelo de voz Kokoro ({destino_modelo.name})")
    baixar_arquivo(BASE_KOKORO + VOZES_KOKORO, caminho_env("KOKORO_VOZES", f"modelos/kokoro/{VOZES_KOKORO}"),
                   "Vozes do Kokoro")


def baixar_whisper(nome: str | None = None) -> None:
    nome = nome or env("WHISPER_MODELO", "small")
    log.info(f"Baixando/validando o modelo Faster-Whisper '{nome}' (pode levar alguns minutos)…")
    from faster_whisper import WhisperModel

    WhisperModel(nome, device="cpu", compute_type="int8", download_root=str(PASTA_MODELOS / "whisper"))
    log.ok(f"Faster-Whisper '{nome}' pronto")
