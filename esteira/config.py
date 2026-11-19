"""Configuração global: caminhos do projeto e leitura do arquivo .env."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_CANAIS = RAIZ / "canais"
PASTA_PROJETOS = RAIZ / "projetos"
PASTA_TEMPLATES = RAIZ / "templates"
PASTA_MODELOS = RAIZ / "modelos"
PASTA_ASSETS = RAIZ / "assets"
PASTA_FONTES = PASTA_ASSETS / "fontes"
PASTA_LOGS = RAIZ / "logs"
PASTA_CREDENCIAIS = RAIZ / "credenciais"
PASTA_AUTOMACAO = RAIZ / "automacao"
PASTA_PAINEL = RAIZ / "painel"

_env_carregado = False


def _limpar_valor(valor: str) -> str:
    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        return valor[1:-1]
    # comentário no fim da linha (apenas quando o valor não está entre aspas)
    if " #" in valor:
        valor = valor.split(" #", 1)[0].rstrip()
    return valor


def carregar_env(forcar: bool = False) -> None:
    """Lê RAIZ/.env sem sobrescrever variáveis já definidas no sistema.

    Valores vazios são ignorados de propósito: assim "ANTHROPIC_API_KEY="
    não vira uma chave vazia que confunde os SDKs.
    """
    global _env_carregado
    if _env_carregado and not forcar:
        return
    _env_carregado = True
    arquivo = RAIZ / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        if chave.startswith("export "):
            chave = chave[7:].strip()
        valor = _limpar_valor(valor)
        if valor and chave not in os.environ:
            os.environ[chave] = valor


def env(nome: str, padrao: str = "") -> str:
    carregar_env()
    valor = os.environ.get(nome)
    if valor is None or not valor.strip():
        return padrao
    return valor.strip()


def env_bool(nome: str, padrao: bool = False) -> bool:
    valor = env(nome, "")
    if not valor:
        return padrao
    return valor.lower() in ("1", "true", "sim", "s", "yes", "y", "on")


def env_int(nome: str, padrao: int) -> int:
    try:
        return int(env(nome, str(padrao)))
    except ValueError:
        return padrao


def env_float(nome: str, padrao: float) -> float:
    try:
        return float(env(nome, str(padrao)).replace(",", "."))
    except ValueError:
        return padrao


def caminho_env(nome: str, padrao: str) -> Path:
    """Caminho vindo do .env; relativo à raiz do projeto quando não for absoluto."""
    caminho = Path(env(nome, padrao)).expanduser()
    return caminho if caminho.is_absolute() else (RAIZ / caminho)
