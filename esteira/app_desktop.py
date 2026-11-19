"""Esteira de Cortes como app de PC: ícones do app, janela própria (Chrome/Edge em modo app) e atalho no menu.

`python esteira.py app` liga o painel se ele estiver desligado e abre a janela do app.
`python esteira.py app instalar` cria o atalho "Esteira de Cortes" no menu do sistema (Linux) e gera os ícones.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import log
from .config import PASTA_PAINEL, RAIZ, env_int

NOME_APP = "Esteira de Cortes"
ID_APP = "esteira-de-cortes"
PASTA_ICONES = PASTA_PAINEL / "static" / "icones"
TAMANHOS_ICONE = (192, 512)
NAVEGADORES_APP = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                   "microsoft-edge-stable", "brave-browser")  # todos abrem uma janela de app com --app=URL
CAMINHOS_WINDOWS = (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
                    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")  # o Edge vem em todo Windows 10/11

# sol listrado de pôr do sol + coqueiro (mesmo desenho do painel; nada de marcas de terceiros)
HTML_ICONE = """<!doctype html><html><body style="margin:0;background:transparent">
<div id="i" style="width:{t}px;height:{t}px;border-radius:{r}px;overflow:hidden;position:relative;
  background:radial-gradient(120% 90% at 50% 0%,#5a1158 0%,#2a0846 45%,#0b0418 100%)">
  <div style="position:absolute;left:50%;top:{sol_top}px;width:{sol}px;height:{sol}px;margin-left:-{meio}px;border-radius:50%;
    background:linear-gradient(#ffd23f,#ff8a3d 50%,#ff2d95);
    -webkit-mask:linear-gradient(#000 0 0) top/100% 53% no-repeat,repeating-linear-gradient(#000 0 {l}px,transparent {l}px {e}px) bottom/100% 48% no-repeat;
    filter:drop-shadow(0 0 {brilho}px rgba(255,45,149,.7))"></div>
  <div style="position:absolute;left:0;right:0;bottom:0;height:{chao}px;background:
    linear-gradient(transparent 0 88%,rgba(255,45,149,.75) 88%) 0 0/100% {grade}px,
    linear-gradient(90deg,transparent 0 94%,rgba(25,227,214,.6) 94%) 0 0/{coluna}px 100%,linear-gradient(#170530,#0b0418)"></div>
  <svg viewBox="0 0 120 200" style="position:absolute;left:{palma_x}px;bottom:{palma_y}px;width:{palma_w}px;height:{palma_h}px;fill:#07020f">
    <path d="M58 200 C57 160 55 118 63 72 L69 72 C62 118 64 160 66 200 Z"/>
    <path d="M66 72 C44 50 22 52 2 70 C26 58 46 62 66 80 Z"/><path d="M66 72 C90 48 110 50 120 66 C98 58 82 62 66 80 Z"/>
    <path d="M66 74 C52 42 36 28 16 26 C38 36 50 52 62 80 Z"/><path d="M66 74 C80 40 96 26 114 24 C94 34 82 52 70 80 Z"/>
    <path d="M66 74 C66 46 62 26 52 12 C68 26 72 48 70 80 Z"/></svg>
</div></body></html>"""


def _html_icone(tamanho: int, mascaravel: bool) -> str:
    t = tamanho
    escala = 0.8 if mascaravel else 1.0  # ícone "maskable" deixa margem segura para o sistema recortar
    sol = int(t * 0.56 * escala)
    return HTML_ICONE.format(t=t, r=0 if mascaravel else int(t * 0.22), sol=sol, meio=sol // 2,
                             sol_top=int(t * (0.2 if mascaravel else 0.14)), l=max(2, int(t * 0.035)),
                             e=max(4, int(t * 0.058)), brilho=int(t * 0.06), chao=int(t * 0.3),
                             grade=max(4, int(t * 0.07)), coluna=max(6, int(t * 0.14)),
                             palma_x=int(t * (0.1 if mascaravel else 0.04)), palma_y=int(t * (0.12 if mascaravel else 0.06)),
                             palma_w=int(t * 0.36 * escala), palma_h=int(t * 0.6 * escala))


def gerar_icones(forcar: bool = False) -> list[Path]:
    """PNG do ícone do app (192, 512 e 512 "maskable"), desenhados com o navegador interno."""
    from playwright.sync_api import sync_playwright

    alvos = [(t, False, PASTA_ICONES / f"app-{t}.png") for t in TAMANHOS_ICONE]
    alvos.append((512, True, PASTA_ICONES / "app-maskable-512.png"))
    if not forcar and all(caminho.exists() for _, _, caminho in alvos):
        return [caminho for _, _, caminho in alvos]
    PASTA_ICONES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        navegador = pw.chromium.launch()
        try:
            for tamanho, mascaravel, caminho in alvos:
                pagina = navegador.new_page(viewport={"width": tamanho, "height": tamanho})
                pagina.set_content(_html_icone(tamanho, mascaravel))
                pagina.locator("#i").screenshot(path=str(caminho), omit_background=True)
                pagina.close()
        finally:
            navegador.close()
    return [caminho for _, _, caminho in alvos]


def url_do_app(porta: int | None = None) -> str:
    return f"http://127.0.0.1:{porta or env_int('PAINEL_PORTA', 8765)}/#/painel"


def achar_navegador() -> str | None:
    """Chrome, Chromium, Edge ou Brave instalados (abrem a janela do app sem barra de endereço)."""
    for nome in NAVEGADORES_APP:
        caminho = shutil.which(nome)
        if caminho:
            return caminho
    if os.name == "nt":
        return next((c for c in map(os.path.expandvars, CAMINHOS_WINDOWS) if Path(c).exists()), None)
    return None


def abrir_app() -> str:
    """Liga o painel se preciso e abre a janela do app."""
    from .orquestrador import _painel_ativo, iniciar_painel_em_segundo_plano

    porta = env_int("PAINEL_PORTA", 8765)
    if not _painel_ativo(porta):
        log.info("Ligando o painel…")
        iniciar_painel_em_segundo_plano(porta)
    url = url_do_app(porta)
    navegador = achar_navegador()
    if navegador:
        subprocess.Popen([navegador, f"--app={url}", f"--class={ID_APP}"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=os.name != "nt")
    else:
        webbrowser.open(url)
    log.ok(f"{NOME_APP} aberto: {url}")
    return url


def _pasta_aplicativos() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def caminho_atalho() -> Path:
    return _pasta_aplicativos() / f"{ID_APP}.desktop"


def instalar_atalho() -> Path:
    """Cria o atalho no menu do sistema (Linux) que liga o painel e abre o app."""
    if sys.platform != "linux":
        raise RuntimeError("Neste sistema, instale pelo navegador: abra o painel no Chrome/Edge e use “Instalar app”.")
    icones = gerar_icones()
    destino = caminho_atalho()
    destino.parent.mkdir(parents=True, exist_ok=True)
    python = RAIZ / ".venv" / "bin" / "python"
    executavel = python if python.exists() else Path(sys.executable)
    destino.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NOME_APP}\n"
        "Comment=Cortes de lives para Shorts: painel da equipe\n"
        f"Exec=\"{executavel}\" \"{RAIZ / 'esteira.py'}\" app\n"
        f"Path={RAIZ}\n"
        f"Icon={icones[-2]}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;\n"
        f"StartupWMClass={ID_APP}\n"
        "StartupNotify=true\n", encoding="utf-8")
    destino.chmod(0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(destino.parent)], check=False, capture_output=True)
    return destino


def remover_atalho() -> bool:
    destino = caminho_atalho()
    if destino.exists():
        destino.unlink()
        return True
    return False
