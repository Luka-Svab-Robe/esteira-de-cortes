"""Diagnóstico do ambiente: mostra o que está pronto e o que falta, com a solução em português."""
from __future__ import annotations

import importlib
import platform
import sys

from . import ffmpeg, templates
from .canal import Canal
from .config import PASTA_FONTES, caminho_env, env


def _modulo(nome: str) -> bool:
    try:
        importlib.import_module(nome)
        return True
    except Exception:
        return False


def verificar(testar_navegador: bool = True) -> list[dict]:
    itens: list[dict] = []

    def add(grupo: str, nome: str, ok: bool, detalhe: str = "", solucao: str = "", critico: bool = False):
        itens.append({"grupo": grupo, "nome": nome, "ok": ok, "detalhe": detalhe, "solucao": solucao,
                      "critico": critico})

    versao = sys.version_info
    add("Sistema", "Python", (3, 10) <= versao[:2] <= (3, 13), f"{platform.python_version()} em {platform.system()}",
        "Use Python 3.10 a 3.13 (3.12 recomendado).", critico=versao[:2] < (3, 10))
    pacotes = {"yaml": "PyYAML", "requests": "requests", "jinja2": "Jinja2", "num2words": "num2words",
               "numpy": "numpy", "PIL": "Pillow", "bs4": "beautifulsoup4", "fastapi": "fastapi", "uvicorn": "uvicorn"}
    faltando = [p for m, p in pacotes.items() if not _modulo(m)]
    add("Sistema", "Pacotes principais", not faltando, "ok" if not faltando else "faltando: " + ", ".join(faltando),
        "Rode: python instalar.py", critico=bool(faltando))
    try:
        bin_ff = ffmpeg.ffmpeg_bin()
        filtros = {"loudnorm", "sidechaincompress", "xfade", "zoompan", "subtitles"}
        ausentes = sorted(f for f in filtros if not ffmpeg.tem_filtro(f))
        add("Vídeo", "FFmpeg", not ausentes, bin_ff if not ausentes else f"{bin_ff} (sem: {', '.join(ausentes)})",
            "Instale um FFmpeg completo (com libass), ex.: Gyan.FFmpeg no Windows, brew no macOS.",
            critico="loudnorm" in ausentes or "xfade" in ausentes)
        encoder = ffmpeg.encoder_video()
        add("Vídeo", "Codificação", True, f"{encoder} ({'GPU' if encoder != 'libx264' else 'CPU'})")
    except ffmpeg.ErroFFmpeg as exc:
        add("Vídeo", "FFmpeg", False, str(exc), "Instale o FFmpeg e reabra o terminal.", critico=True)
    if _modulo("playwright"):
        detalhe, ok = "instalado", True
        if testar_navegador:
            try:
                from .navegador import Navegador

                with Navegador():
                    pass
                detalhe = "Chromium headless funcionando"
            except Exception as exc:
                ok, detalhe = False, str(exc)[:200]
        add("Vídeo", "Playwright/Chromium", ok, detalhe, "Rode: python -m playwright install chromium", critico=not ok)
    else:
        add("Vídeo", "Playwright/Chromium", False, "não instalado", "Rode: python instalar.py", critico=True)
    fontes = [f.name for f in PASTA_FONTES.glob("*.ttf")]
    add("Vídeo", "Fontes tipográficas", len(fontes) >= 5, f"{len(fontes)} arquivo(s) em assets/fontes",
        "Rode: python instalar.py (baixa as fontes do Google Fonts)")

    kokoro_ok = caminho_env("KOKORO_MODELO", "modelos/kokoro/kokoro-v1.0.int8.onnx").exists() and _modulo("kokoro_onnx")
    add("Voz", "Kokoro (voz local)", kokoro_ok, "modelo encontrado" if kokoro_ok else "modelo ausente",
        "Rode: python esteira.py modelos baixar")
    add("Voz", "Edge-TTS (alternativa online)", _modulo("edge_tts"), "", "pip install edge-tts",
        critico=not kokoro_ok and not _modulo("edge_tts"))
    add("Voz", "Faster-Whisper (auditoria)", _modulo("faster_whisper"), f"modelo '{env('WHISPER_MODELO', 'small')}'",
        "pip install faster-whisper")

    from .llm import modelos_ollama, obter_cliente

    cliente = obter_cliente()
    add("Roteiro", "Modelo de linguagem", cliente is not None,
        cliente.descricao if cliente else "nenhum — use --modo-agente (o agente escreve o roteiro) ou o modo heurístico",
        "Configure ANTHROPIC_API_KEY (ou outro provedor) no .env para roteiros automáticos.")
    instalados = modelos_ollama()
    modelo_ollama = env("OLLAMA_MODELO", "qwen2.5:7b")
    if instalados is not None and (cliente is None or cliente.provedor != "ollama"):
        add("Roteiro", "Ollama local", False,
            f"rodando, mas o modelo '{modelo_ollama}' não está instalado (instalados: {', '.join(instalados) or 'nenhum'})",
            f"Para usar: 'ollama pull {modelo_ollama}' ou defina OLLAMA_MODELO no .env "
            "(modelos com menos de 7B costumam gerar roteiros fracos).")
    add("Roteiro", "Pesquisa web", _modulo("ddgs") or _modulo("duckduckgo_search"),
        "Wikipédia + DuckDuckGo" + (" + Brave" if env("BRAVE_API_KEY") else "") + (" + Tavily" if env("TAVILY_API_KEY") else ""),
        "pip install ddgs")
    bancos = [n for n, v in (("Pexels", "PEXELS_API_KEY"), ("Pixabay", "PIXABAY_API_KEY")) if env(v)]
    add("Mídia", "Bancos de vídeo", bool(bancos), ", ".join(bancos) or "sem chaves — usará imagens Openverse e cenas 2D",
        "Crie chaves grátis em pexels.com/api e pixabay.com/api/docs e coloque no .env")
    add("Mídia", "Imagens por IA", env("IMAGEM_IA_PROVEDOR", "pollinations") != "nenhum",
        env("IMAGEM_IA_PROVEDOR", "pollinations"))

    # Esteira de cortes de lives
    if _modulo("yt_dlp"):
        import yt_dlp.version

        from .cortes.ingestao import _runtime_js

        js = _runtime_js()
        add("Cortes", "yt-dlp (download de lives)", True, yt_dlp.version.__version__)
        add("Cortes", "Runtime JavaScript (YouTube)", bool(js), js[1] if js else "nenhum (deno ou node)",
            "Instale o Deno (deno.com) ou o Node.js; o YouTube exige um para liberar os downloads.")
    else:
        add("Cortes", "yt-dlp (download de lives)", False, "não instalado", "Rode: python instalar.py")
    try:
        import cv2

        rosto = not cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml").empty()
        detalhe = f"OpenCV {cv2.__version__}"
    except Exception as exc:
        rosto, detalhe = False, f"indisponível ({str(exc)[:80]})"
    try:
        from .cortes import traducao

        tradutor = traducao.disponivel()
    except Exception:
        tradutor = None
    add("Cortes", "Tradução das falas para português", bool(tradutor), tradutor or "nenhum tradutor",
        "Configure uma IA no .env ou instale um modelo no Ollama (ex.: ollama pull qwen3.5:4b)")
    add("Cortes", "Enquadramento no rosto", rosto, detalhe,
        "Rode: python instalar.py (OpenCV 4.x); sem ele os cortes usam fundo desfocado")

    from .modulos.m6_publicacao import caminho_segredo

    segredo = caminho_segredo()
    add("YouTube", "Credencial OAuth (client_secret.json)", segredo.exists(), str(segredo),
        "Veja credenciais/LEIA-ME.md para criar no Google Cloud Console.")
    for canal_id in Canal.listar():
        canal = Canal.carregar(canal_id)
        add("YouTube", f"Canal '{canal_id}' conectado", canal.caminho_token_youtube.exists(), canal.nome,
            f"python esteira.py youtube autenticar --canal {canal_id}")
    add("Fontes", "Templates", (templates.PASTA_TEMPLATES / "cenas" / "_base.html").exists(), str(templates.PASTA_TEMPLATES))
    return itens


def imprimir(itens: list[dict]) -> int:
    grupo_atual = None
    criticos = 0
    for item in itens:
        if item["grupo"] != grupo_atual:
            grupo_atual = item["grupo"]
            print(f"\n[{grupo_atual}]")
        simbolo = "✔" if item["ok"] else ("✖" if item["critico"] else "⚠")
        print(f"  {simbolo} {item['nome']}: {item['detalhe']}")
        if not item["ok"] and item["solucao"]:
            print(f"      → {item['solucao']}")
        criticos += int(item["critico"] and not item["ok"])
    print("\n" + ("Tudo pronto para produzir!" if criticos == 0 else f"{criticos} problema(s) crítico(s) a resolver."))
    return criticos
