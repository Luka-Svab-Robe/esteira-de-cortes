"""Logs amigáveis em português, no terminal e em arquivo por projeto."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_log = logging.getLogger("esteira")
_arquivos: dict[str, logging.Handler] = {}

SIMBOLOS = {"etapa": "▶", "ok": "✔", "aviso": "⚠", "erro": "✖", "info": "•"}


def preparar_console() -> None:
    """Evita erros de acentuação/símbolos em consoles Windows antigos."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def configurar(nivel: int = logging.INFO) -> None:
    if any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in _log.handlers):
        return
    preparar_console()
    manipulador = logging.StreamHandler(sys.stdout)
    manipulador.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(manipulador)
    _log.setLevel(nivel)
    _log.propagate = False


def anexar_arquivo(caminho: Path) -> None:
    configurar()
    chave = str(caminho.resolve())
    if chave in _arquivos:
        return
    caminho.parent.mkdir(parents=True, exist_ok=True)
    manipulador = logging.FileHandler(caminho, encoding="utf-8")
    manipulador.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _log.addHandler(manipulador)
    _arquivos[chave] = manipulador


def remover_arquivo(caminho: Path) -> None:
    chave = str(caminho.resolve())
    manipulador = _arquivos.pop(chave, None)
    if manipulador:
        _log.removeHandler(manipulador)
        manipulador.close()


def etapa(msg: str) -> None:
    configurar()
    _log.info(f"\n{SIMBOLOS['etapa']} {msg}")


def ok(msg: str) -> None:
    configurar()
    _log.info(f"  {SIMBOLOS['ok']} {msg}")


def info(msg: str) -> None:
    configurar()
    _log.info(f"  {SIMBOLOS['info']} {msg}")


def aviso(msg: str) -> None:
    configurar()
    _log.warning(f"  {SIMBOLOS['aviso']} {msg}")


def erro(msg: str) -> None:
    configurar()
    _log.error(f"  {SIMBOLOS['erro']} {msg}")


def debug(msg: str) -> None:
    configurar()
    _log.debug(msg)
