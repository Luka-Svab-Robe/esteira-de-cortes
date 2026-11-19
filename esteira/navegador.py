"""Navegador headless (Playwright/Chromium) para renderizar templates HTML.

Os templates expõem `window.__setTime(t)`, que congela todas as animações CSS
no instante `t`. Assim cada quadro é capturado de forma determinística, sem
depender da velocidade da máquina — funciona igual em CPU fraca ou com GPU.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from . import log
from .ffmpeg import ErroFFmpeg, args_video, ffmpeg_bin


class ErroNavegador(RuntimeError):
    pass


class Navegador:
    def __init__(self) -> None:
        self._pw = None
        self.browser = None

    def __enter__(self) -> "Navegador":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ErroNavegador("Playwright não está instalado. Rode: python instalar.py") from exc
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.launch(
                args=["--allow-file-access-from-files", "--hide-scrollbars", "--font-render-hinting=none",
                      "--disable-dev-shm-usage"])
        except Exception as exc:
            self._pw.stop()
            raise ErroNavegador(
                "O Chromium do Playwright não iniciou. Rode: python -m playwright install chromium "
                f"(no Linux talvez também: python -m playwright install-deps chromium). Detalhe: {exc}") from exc
        return self

    def __exit__(self, *exc) -> None:
        for alvo in (self.browser, self._pw):
            try:
                if alvo is self._pw and alvo:
                    alvo.stop()
                elif alvo:
                    alvo.close()
            except Exception:
                pass

    def _abrir(self, html: Path, largura: int, altura: int, escala: float = 1.0):
        contexto = self.browser.new_context(viewport={"width": largura, "height": altura},
                                            device_scale_factor=escala)
        pagina = contexto.new_page()
        erros: list[str] = []
        pagina.on("pageerror", lambda e: erros.append(str(e)))
        pagina.goto(Path(html).resolve().as_uri(), wait_until="load")
        try:
            pagina.wait_for_function("window.__pronto === true", timeout=60_000)
        except Exception as exc:
            contexto.close()
            raise ErroNavegador(f"Template {Path(html).name} não ficou pronto: {exc}; erros JS: {erros[:3]}") from exc
        if erros:
            log.debug(f"Avisos JS em {Path(html).name}: {erros[:3]}")
        return contexto, pagina

    @staticmethod
    def _capturador(contexto, pagina):
        """Captura JPEG via CDP (Page.captureScreenshot com optimizeForSpeed), ~20-40% mais rápida
        que page.screenshot; volta ao método padrão do Playwright se o CDP não estiver disponível."""
        try:
            cdp = contexto.new_cdp_session(pagina)
            parametros = {"format": "jpeg", "quality": 90, "optimizeForSpeed": True, "captureBeyondViewport": False}
            base64.b64decode(cdp.send("Page.captureScreenshot", parametros)["data"])
            return lambda: base64.b64decode(cdp.send("Page.captureScreenshot", parametros)["data"])
        except Exception:
            return lambda: pagina.screenshot(type="jpeg", quality=90)

    def capturar(self, html: Path, saida: Path, largura: int, altura: int, t: float = 0.0,
                 transparente: bool = False, escala: float = 1.0, qualidade: int = 92) -> Path:
        contexto, pagina = self._abrir(html, largura, altura, escala)
        try:
            pagina.evaluate("t => window.__setTime(t)", float(t))
            saida = Path(saida)
            saida.parent.mkdir(parents=True, exist_ok=True)
            if saida.suffix.lower() in (".jpg", ".jpeg"):
                pagina.screenshot(path=str(saida), type="jpeg", quality=qualidade)
            else:
                pagina.screenshot(path=str(saida), type="png", omit_background=transparente)
        finally:
            contexto.close()
        return saida

    def renderizar_clipe(self, html: Path, saida: Path, duracao: float, fps: int = 30,
                         anim_seg: float | None = None, largura: int = 1920, altura: int = 1080) -> Path:
        """Captura quadro a quadro e codifica em MP4.

        anim_seg: renderiza só os primeiros N segundos (animação de entrada) e
        congela o último quadro até completar a duração — muito mais rápido.
        """
        animado = duracao if anim_seg is None else max(0.1, min(duracao, anim_seg))
        quadros = max(1, int(round(animado * fps)))
        espera = max(0.0, duracao - quadros / fps)
        filtros = []
        if espera > 0.001:
            filtros.append(f"tpad=stop_mode=clone:stop_duration={espera + 0.1:.3f}")
        filtros += ["fps=30", "format=yuv420p"]
        comando = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe",
                   "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-", "-vf", ",".join(filtros),
                   "-t", f"{duracao:.3f}", *args_video("cena"), "-an", str(saida)]
        contexto, pagina = self._abrir(html, largura, altura)
        capturar = self._capturador(contexto, pagina)
        proc = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for i in range(quadros):
                pagina.evaluate("t => window.__setTime(t)", i / fps)
                proc.stdin.write(capturar())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        finally:
            contexto.close()
        erro = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise ErroFFmpeg(f"Falha ao codificar {Path(saida).name}: {erro[-800:]}")
        return Path(saida)
