#!/usr/bin/env python3
"""Gera o instalador do Windows: dist/EsteiraDeCortes-Instalador.exe.

Uso:  python instalador/construir_exe.py
- Copia só o programa para instalador/build/pacote (sem .env, contas, canais, vídeos, credenciais nem modelos).
- Recusa gerar se encontrar algo que pareça chave, token ou e-mail pessoal no pacote.
- Usa o makensis do sistema ou baixa os pacotes oficiais do NSIS (apt-get download) para instalador/ferramentas.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PASTA = RAIZ / "instalador"
FERRAMENTAS = PASTA / "ferramentas"
PACOTE = PASTA / "build" / "pacote"
SAIDA = RAIZ / "dist" / "EsteiraDeCortes-Instalador.exe"
VERSAO = "1.0.0"

INCLUIR = ["esteira", "painel", "templates", "docs", "assets/fontes", ".claude/agents", "esteira.py", "instalar.py",
           "instalar.bat", "requirements.txt", "README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", ".env.example",
           "credenciais/LEIA-ME.md"]
IGNORAR = ["__pycache__", "*.pyc", "*.pyo", "classico", "previas", ".DS_Store", "*.log"]
# o que nunca pode ir para um instalador (contas, chaves, vídeos e dados do dono)
PROIBIDO = [".env", "dados", "canais", "projetos", "credenciais/*.json", "modelos", "logs", "automacao", "site"]
SEGREDOS = {
    "chave da Anthropic": re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    "chave da OpenAI": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"),
    "chave do Google": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "segredo OAuth do Google": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "token OAuth": re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}|\b1//0[A-Za-z0-9_-]{20,}"),
    "chave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "token do GitHub": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
}


def passo(texto: str) -> None:
    print(f"\n▶ {texto}", flush=True)


def _ignorado(nome: str) -> bool:
    return any(fnmatch.fnmatch(nome, padrao) for padrao in IGNORAR)


def montar_pacote() -> Path:
    passo("Montando o pacote (só o programa)")
    if PACOTE.exists():
        shutil.rmtree(PACOTE)
    PACOTE.mkdir(parents=True)
    for item in INCLUIR:
        origem = RAIZ / item
        if not origem.exists():
            sys.exit(f"✖ Falta no projeto: {item}")
        destino = PACOTE / item
        destino.parent.mkdir(parents=True, exist_ok=True)
        if origem.is_dir():
            shutil.copytree(origem, destino, ignore=lambda _pasta, nomes: [n for n in nomes if _ignorado(n)])
        else:
            shutil.copy2(origem, destino)
    (PACOTE / "instalador").mkdir(exist_ok=True)  # o .nsi chama $INSTDIR\instalador\preparar_windows.ps1
    shutil.copy2(PASTA / "windows" / "preparar_windows.ps1", PACOTE / "instalador" / "preparar_windows.ps1")
    gerar_icone(PACOTE / "instalador" / "esteira.ico")
    return PACOTE


def gerar_icone(destino: Path) -> Path:
    from PIL import Image

    png = RAIZ / "painel" / "static" / "icones" / "app-512.png"
    if not png.exists():
        sys.path.insert(0, str(RAIZ))
        from esteira.app_desktop import gerar_icones

        gerar_icones()
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.open(png).save(destino, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return destino


def conferir_pacote(pacote: Path) -> None:
    """Para tudo se algo pessoal ou secreto entrou no pacote."""
    passo("Conferindo que não vai nenhum segredo")
    problemas = []
    for padrao in PROIBIDO:
        if list(pacote.glob(padrao)):
            problemas.append(f"item proibido no pacote: {padrao}")
    pessoais = [e for e in (os.environ.get("ESTEIRA_EMAIL_DONO", ""),) if e]
    for arquivo in pacote.rglob("*"):
        if not arquivo.is_file() or arquivo.suffix.lower() in (".ttf", ".png", ".ico", ".jpg", ".otf", ".woff2"):
            continue
        texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        for nome, regex in SEGREDOS.items():
            if regex.search(texto):
                problemas.append(f"{nome} em {arquivo.relative_to(pacote)}")
        for email in pessoais:
            if email.lower() in texto.lower():
                problemas.append(f"e-mail pessoal em {arquivo.relative_to(pacote)}")
    exemplo = (pacote / ".env.example").read_text(encoding="utf-8")
    for linha in exemplo.splitlines():
        chave, _, valor = linha.partition("=")
        if re.search(r"(KEY|TOKEN|SECRET|SENHA)$", chave.strip()) and valor.strip():
            problemas.append(f".env.example traz valor em {chave.strip()}")
    if problemas:
        sys.exit("✖ Pacote recusado:\n  - " + "\n  - ".join(problemas))
    total = sum(f.stat().st_size for f in pacote.rglob("*") if f.is_file())
    print(f"  ✔ nenhum segredo encontrado ({sum(1 for f in pacote.rglob('*') if f.is_file())} arquivos, {total / 1e6:.1f} MB)")


def achar_makensis() -> tuple[str, dict]:
    sistema = shutil.which("makensis")
    if sistema:
        return sistema, dict(os.environ)
    local = FERRAMENTAS / "nsis" / "usr" / "bin" / "makensis"
    if not local.exists():
        passo("Baixando o NSIS (pacotes oficiais do sistema, sem instalar nada)")
        if not shutil.which("apt-get"):
            sys.exit("✖ Instale o NSIS (makensis) e rode de novo.")
        debs = FERRAMENTAS / "debs"
        debs.mkdir(parents=True, exist_ok=True)
        subprocess.run(["apt-get", "download", "nsis", "nsis-common"], cwd=debs, check=True)
        for deb in debs.glob("*.deb"):
            subprocess.run(["dpkg", "-x", str(deb), str(FERRAMENTAS / "nsis")], check=True)
    return str(local), {**os.environ, "NSISDIR": str(FERRAMENTAS / "nsis" / "usr" / "share" / "nsis")}


def gerar_exe(pacote: Path) -> Path:
    passo("Gerando o instalador .exe")
    makensis, ambiente = achar_makensis()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    comando = [makensis, "-V2", "-INPUTCHARSET", "UTF8", f"-DPACOTE={pacote}", f"-DSAIDA={SAIDA}",
               f"-DICONE={pacote / 'instalador' / 'esteira.ico'}", f"-DVERSAO={VERSAO}",
               str(PASTA / "windows" / "esteira.nsi")]
    resultado = subprocess.run(comando, env=ambiente, capture_output=True, text=True)
    if resultado.returncode != 0:
        sys.exit("✖ O NSIS falhou:\n" + (resultado.stdout + resultado.stderr)[-3000:])
    print(f"  ✔ {SAIDA} ({SAIDA.stat().st_size / 1e6:.1f} MB)")
    return SAIDA


def main() -> int:
    pacote = montar_pacote()
    conferir_pacote(pacote)
    gerar_exe(pacote)
    print("\nPronto! Leve o arquivo para o PC com Windows e dê dois cliques.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
