#!/usr/bin/env python3
"""Instalador automático da Esteira Canal Dark (Windows, macOS e Linux).

Uso:  python instalar.py [--sem-modelos] [--whisper small] [--kokoro int8|completo]

Passos: verifica o Python -> cria .venv -> instala dependências -> instala o
Chromium do Playwright -> verifica/instala o FFmpeg -> baixa fontes -> baixa
modelos de voz/auditoria -> cria o .env -> roda o diagnóstico.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / ".venv"
SISTEMA = platform.system()
PY_VENV = VENV / ("Scripts/python.exe" if SISTEMA == "Windows" else "bin/python")


def passo(msg: str) -> None:
    print(f"\n▶ {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  ✔ {msg}", flush=True)


def aviso(msg: str) -> None:
    print(f"  ⚠ {msg}", flush=True)


def rodar(comando: list, **kw) -> int:
    print("  $ " + " ".join(str(c) for c in comando), flush=True)
    return subprocess.call([str(c) for c in comando], cwd=str(RAIZ), **kw)


def garantir_python_compativel() -> None:
    versao = sys.version_info[:2]
    if (3, 10) <= versao <= (3, 13):
        ok(f"Python {platform.python_version()}")
        return
    aviso(f"Python {platform.python_version()} não é o ideal (use 3.10 a 3.13). Procurando outra versão…")
    candidatos = (["py", "-3.12"], ["py", "-3.11"], ["py", "-3.13"]) if SISTEMA == "Windows" else \
        (["python3.12"], ["python3.11"], ["python3.13"], ["python3.10"])
    for candidato in candidatos:
        if shutil.which(candidato[0]):
            teste = subprocess.run([*candidato, "-c", "import sys; print(sys.version_info[:2] >= (3, 10))"],
                                   capture_output=True, text=True)
            if teste.returncode == 0 and "True" in teste.stdout:
                print(f"  → reiniciando o instalador com {' '.join(candidato)}")
                sys.exit(subprocess.call([*candidato, str(RAIZ / "instalar.py"), *sys.argv[1:]]))
    if versao < (3, 10):
        sys.exit("✖ Instale o Python 3.12 em https://www.python.org/downloads/ e rode novamente.")
    aviso("Seguindo com a versão atual; alguns pacotes (ex.: onnxruntime) podem não ter suporte ainda.")


def _venv_funciona() -> bool:
    return PY_VENV.exists() and subprocess.run([str(PY_VENV), "-m", "pip", "--version"], capture_output=True).returncode == 0


def criar_venv() -> None:
    """Cria o .venv tentando, nesta ordem: Python atual, outras versões instaladas, uv e virtualenv."""
    passo("Ambiente virtual (.venv)")
    if _venv_funciona():
        ok("já existe")
        return
    tentativas = [[sys.executable, "-m", "venv", VENV]]
    outros = (["py", "-3.12"], ["py", "-3.11"], ["py", "-3.13"]) if SISTEMA == "Windows" else \
        (["python3.12"], ["python3.11"], ["python3.13"], ["python3.10"])
    tentativas += [[*c, "-m", "venv", VENV] for c in outros if shutil.which(c[0])]
    uv = shutil.which("uv") or (str(Path.home() / ".local" / "bin" / "uv") if (Path.home() / ".local" / "bin" / "uv").exists() else None)
    if uv:
        tentativas += [[uv, "venv", "--seed", "--python", v, VENV] for v in ("3.12", "3.11")]
    if shutil.which("virtualenv"):
        tentativas.append(["virtualenv", VENV])
    for comando in tentativas:
        shutil.rmtree(VENV, ignore_errors=True)
        if rodar(comando, stderr=subprocess.DEVNULL) == 0 and _venv_funciona():
            ok(f"criado com {Path(str(comando[0])).name}")
            return
        aviso("tentativa falhou; tentando a próxima opção…")
    shutil.rmtree(VENV, ignore_errors=True)
    dica = "sudo apt install python3-venv" if SISTEMA == "Linux" else "reinstale o Python marcando 'pip/venv'"
    sys.exit(f"✖ Não foi possível criar o .venv. Dica: {dica} (ou instale o uv: https://docs.astral.sh/uv/)")


def instalar_dependencias() -> None:
    passo("Dependências Python (pode levar alguns minutos)")
    rodar([PY_VENV, "-m", "pip", "install", "--upgrade", "pip", "wheel"])
    if rodar([PY_VENV, "-m", "pip", "install", "-r", RAIZ / "requirements.txt"]) != 0:
        sys.exit("✖ Falha ao instalar as dependências. Verifique a conexão e rode novamente.")
    ok("pacotes instalados")
    passo("Navegador headless (Chromium do Playwright)")
    if rodar([PY_VENV, "-m", "playwright", "install", "chromium"]) != 0:
        aviso("Falha ao baixar o Chromium. Rode depois: .venv/bin/python -m playwright install chromium")
    elif SISTEMA == "Linux":
        teste = subprocess.run([str(PY_VENV), "-c", "from playwright.sync_api import sync_playwright as s\n"
                                "p=s().start(); b=p.chromium.launch(); b.close(); p.stop()"], capture_output=True)
        if teste.returncode != 0:
            aviso("O Chromium precisa de bibliotecas do sistema. Rode: "
                  f"sudo {PY_VENV} -m playwright install-deps chromium")
        else:
            ok("Chromium funcionando")


def garantir_ffmpeg() -> None:
    passo("FFmpeg")
    if shutil.which("ffmpeg"):
        ok(shutil.which("ffmpeg"))
        return
    if SISTEMA == "Windows" and shutil.which("winget"):
        rodar(["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"])
        aviso("Feche e reabra o terminal para o FFmpeg entrar no PATH.")
    elif SISTEMA == "Darwin" and shutil.which("brew"):
        rodar(["brew", "install", "ffmpeg"])
    else:
        comandos = {"apt-get": "sudo apt install -y ffmpeg", "dnf": "sudo dnf install -y ffmpeg",
                    "pacman": "sudo pacman -S ffmpeg", "zypper": "sudo zypper install ffmpeg"}
        sugestao = next((c for g, c in comandos.items() if shutil.which(g)), "instale o FFmpeg pelo gerenciador do sistema")
        aviso(f"FFmpeg não encontrado. Rode: {sugestao}")
        return
    ok("FFmpeg instalado" if shutil.which("ffmpeg") else "verifique o FFmpeg depois de reabrir o terminal")


def baixar_fontes() -> None:
    passo("Fontes tipográficas (Google Fonts, licenças OFL/Apache)")
    sys.path.insert(0, str(RAIZ))
    from esteira.templates import URLS_FONTES  # só usa a biblioteca padrão

    pasta = RAIZ / "assets" / "fontes"
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, url in URLS_FONTES.items():
        destino = pasta / nome
        if destino.exists() and destino.stat().st_size > 10_000:
            continue
        for tentativa in range(1, 4):
            try:
                pedido = urllib.request.Request(url, headers={"User-Agent": "EsteiraCanalDark/1.0"})
                with urllib.request.urlopen(pedido, timeout=60) as r:
                    destino.write_bytes(r.read())
                ok(nome)
                break
            except Exception as exc:
                destino.unlink(missing_ok=True)
                if tentativa == 3:
                    aviso(f"{nome}: {exc} (a esteira usará fontes do sistema)")
    ok(f"{len(list(pasta.glob('*.ttf')))} fontes disponíveis")


def baixar_modelos(args) -> None:
    if args.sem_modelos:
        aviso("Download dos modelos pulado (--sem-modelos). Depois rode: python esteira.py modelos baixar")
        return
    passo("Modelos locais: voz Kokoro (~115 MB) e auditor Faster-Whisper")
    comando = [PY_VENV, RAIZ / "esteira.py", "modelos", "baixar", "--kokoro", args.kokoro]
    if args.whisper:
        comando += ["--whisper", args.whisper]
    if rodar(comando, env={**os.environ, "ESTEIRA_SEM_VENV": "1"}) != 0:
        aviso("Falha ao baixar os modelos; a narração usará o Edge-TTS (online). Tente: python esteira.py modelos baixar")


def criar_env() -> None:
    passo("Arquivo de configuração .env")
    destino, modelo = RAIZ / ".env", RAIZ / ".env.example"
    if destino.exists():
        ok(".env já existe (não foi alterado)")
    else:
        shutil.copyfile(modelo, destino)
        ok(".env criado a partir de .env.example — preencha suas chaves quando quiser")
    for pasta in ("projetos", "logs", "modelos", "automacao", "credenciais"):
        (RAIZ / pasta).mkdir(exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Instalador da Esteira Canal Dark")
    parser.add_argument("--sem-modelos", action="store_true", help="não baixa Kokoro/Whisper agora")
    parser.add_argument("--whisper", help="tamanho do modelo Whisper (tiny, base, small, medium)")
    parser.add_argument("--kokoro", default="int8", choices=["int8", "completo"])
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    print("=" * 64 + "\n  Esteira Canal Dark — instalação automática\n" + "=" * 64)
    passo("Python")
    garantir_python_compativel()
    criar_venv()
    instalar_dependencias()
    garantir_ffmpeg()
    baixar_fontes()
    criar_env()
    baixar_modelos(args)
    passo("Diagnóstico final")
    # F13: só anuncia conclusão se o diagnóstico não achou problema crítico (ele sai com código 1 nesse caso)
    if rodar([PY_VENV, RAIZ / "esteira.py", "diagnostico"], env={**os.environ, "ESTEIRA_SEM_VENV": "1"}) != 0:
        sys.exit("\n✖ Instalação INCOMPLETA: há problema(s) crítico(s) listados acima. Corrija-o(s) e rode "
                 "python instalar.py de novo.")
    print("\nPronto! Abra esta pasta no Claude Code (ou outro agente) e peça, por exemplo:\n"
          "  “Crie um canal novo para mim” e depois “Crie um vídeo sobre o mistério do navio Mary Celeste”.")


if __name__ == "__main__":
    main()
