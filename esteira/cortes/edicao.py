"""Editor: transforma cada momento escolhido em um Short vertical 1080x1920 pronto para postar."""
from __future__ import annotations

import math
import os
import shutil
import statistics
from pathlib import Path

from .. import ffmpeg, log, templates, util
from . import camera, efeitos, fontes, ingestao, traducao
from ..canal import Canal
from ..config import PASTA_FONTES, PASTA_MODELOS, env
from ..navegador import Navegador
from ..projeto import Projeto
from ..modulos.m4_shorts import _cor_ass, gerar_ass

LARGURA, ALTURA = 1080, 1920
_modelo_whisper = None


def palavras_do_corte(video: Path, idioma: str | None = None) -> tuple[list[dict], str | None]:
    """Tempos exatos de cada palavra (para a legenda dinâmica) e o idioma falado, detectado pelo Whisper.

    idioma=None detecta sozinho: a etiqueta de idioma da live costuma estar errada (ex.: live brasileira
    marcada como "inglês", ou jogo em inglês com narração em português).
    """
    global _modelo_whisper
    from faster_whisper import WhisperModel

    if _modelo_whisper is None:
        _modelo_whisper = WhisperModel(env("WHISPER_MODELO", "small"), device="cpu", compute_type="int8",
                                       download_root=str(PASTA_MODELOS / "whisper"))
    segmentos, info = _modelo_whisper.transcribe(str(video), language=idioma or None, word_timestamps=True,
                                                 vad_filter=True, condition_on_previous_text=False)
    palavras = []
    for s in segmentos:
        for p in s.words or []:
            if p.word.strip():
                palavras.append({"palavra": p.word.strip(), "inicio": float(p.start), "fim": float(p.end)})
    return palavras, getattr(info, "language", None) or idioma


def trilha_do_rosto(video: Path, pasta_tmp: Path, fps: int = 2) -> list[tuple[float, float]] | None:
    """Posição horizontal (0 a 1) do rosto principal ao longo do tempo, já suavizada como uma câmera.

    Devolve pontos (segundo, centro). None quando não há rosto em pelo menos 40% das amostras.
    """
    try:
        import cv2

        frontal = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        perfil = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml"))
        if frontal.empty():
            raise RuntimeError("modelo Haar não carregou")
    except Exception as exc:  # OpenCV ausente ou sem o detector Haar (o OpenCV 5 o removeu)
        log.aviso(f"Detecção de rosto indisponível ({exc}); usando fundo desfocado. Rode: python instalar.py")
        return None
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    ffmpeg.rodar(["-i", video, "-vf", f"fps={fps},scale=480:-2", "-q:v", "4", pasta_tmp / "q_%04d.jpg"],
                 descricao="Amostras para detectar rosto")
    quadros = sorted(pasta_tmp.glob("q_*.jpg"))

    def maior_rosto(cinza) -> float | None:
        """Centro (0 a 1) do maior rosto: de frente; senão de perfil, olhando para os dois lados."""
        largura = cinza.shape[1]
        rostos = [(x, w) for x, _, w, _ in frontal.detectMultiScale(cinza, scaleFactor=1.1, minNeighbors=6,
                                                                     minSize=(36, 36))]
        if not rostos and not perfil.empty():
            rostos = [(x, w) for x, _, w, _ in perfil.detectMultiScale(cinza, 1.1, 6, minSize=(36, 36))]
            rostos += [(largura - x - w, w) for x, _, w, _ in perfil.detectMultiScale(cv2.flip(cinza, 1), 1.1, 6,
                                                                                    minSize=(36, 36))]
        if not rostos:
            return None
        x, w = max(rostos, key=lambda r: r[1])
        return (x + w / 2) / largura

    brutos: list[float | None] = []
    for arquivo in quadros:
        imagem = cv2.imread(str(arquivo))
        brutos.append(maior_rosto(cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)) if imagem is not None else None)
    shutil.rmtree(pasta_tmp, ignore_errors=True)
    achados = [c for c in brutos if c is not None]
    if not quadros or len(achados) < 0.4 * len(quadros):
        return None
    # Preenche as amostras sem rosto com a última posição conhecida (ou a primeira, no começo).
    ultimo = achados[0]
    preenchidos = []
    for c in brutos:
        ultimo = c if c is not None else ultimo
        preenchidos.append(ultimo)
    # Câmera virtual: zona morta (não treme com movimentos pequenos) + aproximação amortecida + média móvel.
    # Troca de cena (o rosto pula longe e fica lá por 1 s) vira corte seco, sem a câmera "deslizar".
    camera = statistics.median(preenchidos[:2 * fps * 3] or preenchidos)
    zona, passo, salto = 0.04, 0.5, 0.22
    posicoes, cenas = [], [0]
    for i, c in enumerate(preenchidos):
        seguintes = preenchidos[i:i + fps + 1]
        if abs(c - camera) > salto and len(seguintes) > fps and all(abs(x - c) < 0.08 for x in seguintes):
            camera = c
            cenas.append(i)
        elif abs(c - camera) > zona:
            camera += (c - camera - math.copysign(zona, c - camera)) * passo
        posicoes.append(camera)
    cenas.append(len(posicoes))
    meia, suaves = fps, []
    for a, b in zip(cenas, cenas[1:]):
        trecho = posicoes[a:b]
        suaves += [statistics.fmean(trecho[max(0, i - meia):i + meia + 1]) for i in range(len(trecho))]
    pontos = []
    for i, c in enumerate(suaves):
        if i in cenas[1:-1]:  # corte seco: repete o ponto anterior um instante antes da troca
            pontos.append((round(i / fps - 0.04, 3), pontos[-1][1]))
        pontos.append((i / fps, round(c, 4)))
    return pontos


def _simplificar(pontos: list[tuple[float, float]], tolerancia: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker: mantém só os pontos onde a trajetória muda de direção."""
    if len(pontos) < 3:
        return pontos
    (t0, x0), (t1, x1) = pontos[0], pontos[-1]
    pior, indice = 0.0, 0
    for i in range(1, len(pontos) - 1):
        t, x = pontos[i]
        esperado = x0 + (x1 - x0) * (t - t0) / ((t1 - t0) or 1)
        if abs(x - esperado) > pior:
            pior, indice = abs(x - esperado), i
    if pior <= tolerancia:
        return [pontos[0], pontos[-1]]
    return _simplificar(pontos[:indice + 1], tolerancia)[:-1] + _simplificar(pontos[indice:], tolerancia)


def _expressao_x(trilha: list[tuple[float, float]], largura: int, w: int) -> str:
    """Expressão do FFmpeg (avaliada a cada quadro) para o x do recorte seguir a trilha do rosto."""
    pontos = [(t, max(0.0, min(largura - w, c * largura - w / 2))) for t, c in trilha]
    pontos = _simplificar(pontos, 4.0)
    if max(x for _, x in pontos) - min(x for _, x in pontos) < 0.02 * largura:
        return str(int(statistics.fmean(x for _, x in pontos)))
    expressao = f"{pontos[0][1]:.1f}"
    for (ta, xa), (tb, xb) in zip(pontos, pontos[1:]):
        if tb > ta and abs(xb - xa) >= 0.5:
            inclinacao = (xb - xa) / (tb - ta)
            sinal = "-" if inclinacao < 0 else "+"
            expressao += f"{sinal}{abs(inclinacao):.3f}*clip(t-{ta:.2f}\\,0\\,{tb - ta:.2f})"
    return expressao


def _cadeia_quadro(modo: str, trilha: list[tuple[float, float]] | None, largura: int, altura: int,
                   zooms: list[tuple[float, float]] | None = None, realce: str = "") -> tuple[str, float]:
    """Filtro de enquadramento vertical e a altura relativa ideal para a legenda."""
    zoom = efeitos.cadeia_zoom("q0", "c0", LARGURA, ALTURA, zooms or [], foco_y=0.35)
    if modo in ("rosto", "centro") or (modo == "auto" and trilha):
        w = min(largura, int(altura * 9 / 16)) // 2 * 2
        x = _expressao_x(trilha, largura, w) if trilha else str((largura - w) // 2)
        return (f"[0:v]crop=w={w}:h={altura}:x='{x}':y=0,scale={LARGURA}:{ALTURA}:flags=lanczos{realce},setsar=1[q0];"
                + zoom), 0.70
    return (f"[0:v]split=2[a][b];[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
            f"scale={LARGURA}:{ALTURA},eq=brightness=-0.12:saturation=0.85,setsar=1[bg];"
            f"[b]scale={LARGURA}:-2:flags=lanczos{realce},setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-120[q0];"
            + zoom), 0.68


# Janela do vídeo na moldura do canal (vídeo horizontal inteiro no centro, cantos arredondados)
JANELA = {"x": 40, "y": 716, "w": 1000, "h": 562}
LEGENDA_MOLDURA_Y = 1312  # topo da legenda, logo abaixo do vídeo (cresce para baixo se quebrar a linha)
LEGENDA_DIVIDIDO_Y = 1250  # centro da legenda no layout dividido (meio do jogo, longe do gancho e da interface)
MARGEM_HD = 1.0  # folga (s) de cada lado ao baixar o trecho de um corte em HD


def perfil_do_canal(canal: Canal) -> dict:
    """@, nome e foto do canal (a foto vem do YouTube: python esteira.py youtube perfil --canal C)."""
    import base64

    info = util.ler_json(canal.pasta_credenciais / "canal_youtube.json", {}) or {}
    handle = info.get("handle") or canal.handle or ""
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    foto = ""
    for nome in ("avatar.jpg", "avatar.png", "logo.png", "logo.jpg"):
        arquivo = canal.pasta / "assets" / nome
        if arquivo.exists():
            tipo = "image/png" if arquivo.suffix == ".png" else "image/jpeg"
            foto = f"data:{tipo};base64," + base64.b64encode(arquivo.read_bytes()).decode()
            break
    return {"handle": handle or info.get("titulo") or canal.nome, "nome": info.get("titulo") or canal.nome, "foto": foto}


MODELOS = ("padrao", "direita", "centro")  # posição da foto + @: esquerda (atual), direita, centralizada no topo
GANCHO_EXEMPLO = "Olha *isso* aqui"


def legenda_editada(projeto: Projeto, corte: dict) -> dict | None:
    """Legenda que o dono corrigiu no painel, se ainda vale para este trecho (se o corte mudou, volta à automática)."""
    if not corte.get("legenda_editada"):
        return None
    dados = projeto.ler(f"cortes/corte_{corte['indice']}_legenda_editada.json")
    if not dados or not dados.get("palavras"):
        return None
    if abs(float(dados.get("inicio", -1)) - float(corte["inicio"])) > 0.05 or abs(float(dados.get("fim", -1)) - float(corte["fim"])) > 0.05:
        log.aviso(f"Corte {corte['indice']}: o trecho mudou depois que você ajustou a legenda; voltando à legenda automática.")
        return None
    return dados


def modelo_do_corte(cfg: dict, corte: dict | None = None) -> str:
    modelo = (corte or {}).get("modelo") or cfg.get("modelo") or "padrao"
    return modelo if modelo in MODELOS else "padrao"


def html_moldura(canal: Canal, cfg: dict, destino: Path, gancho: str, credito: str, modelo: str,
                 previa: bool = False) -> Path:
    """HTML da moldura do canal (foto + @, crédito e gancho) no modelo escolhido."""
    perfil = perfil_do_canal(canal)
    return templates.renderizar("cenas/short_moldura.html", destino, templates.tema_canal(canal),
                                {"titulo": gancho, "handle": perfil["handle"], "foto": perfil["foto"],
                                 "credito": credito, "janela": JANELA, "cor_fundo": cfg.get("cor_fundo", "#000000"),
                                 "brilho": bool(cfg.get("brilho_fundo", False)), "modelo": modelo,
                                 "foco_foto": cfg.get("foco_foto", "45% 30%"), "previa": previa,
                                 "legenda_y": LEGENDA_MOLDURA_Y},
                                LARGURA, ALTURA, True)


def html_dividido(canal: Canal, cfg: dict, destino: Path, gancho: str, credito: str, previa: bool = False) -> Path:
    """HTML do layout dividido (selo do canal, faixa na emenda e gancho sobre a emenda)."""
    perfil = perfil_do_canal(canal)
    return templates.renderizar("cenas/short_dividido.html", destino, templates.tema_canal(canal),
                                {"titulo": gancho, "handle": perfil["handle"], "foto": perfil["foto"],
                                 "credito": credito, "juncao": camera.CAM_ALTURA, "legenda_y": LEGENDA_DIVIDIDO_Y,
                                 "foco_foto": cfg.get("foco_foto", "45% 30%"), "previa": previa},
                                LARGURA, ALTURA, True)


def previa_modelo(canal: Canal, modelo: str, destino: Path, escala: float = 0.4) -> Path:
    """PNG pequeno de como fica o Short neste modelo (para escolher no painel). modelo "dividido" = câmera em cima."""
    cfg = canal.cfg.get("cortes", {})
    destino.parent.mkdir(parents=True, exist_ok=True)
    if modelo == "dividido":
        html = html_dividido(canal, cfg, destino.with_suffix(".html"), GANCHO_EXEMPLO, "Live de hoje · twitch.tv/canal",
                             previa=True)
    else:
        html = html_moldura(canal, cfg, destino.with_suffix(".html"), GANCHO_EXEMPLO, "Corte da live de hoje", modelo,
                            previa=True)
    with Navegador() as nav:
        nav.capturar(html, destino, LARGURA, ALTURA, t=5, escala=escala)
    html.unlink(missing_ok=True)
    return destino


def _cadeia_moldura(cor_fundo: str, zooms: list[tuple[float, float]] | None = None, realce: str = "") -> str:
    """Vídeo inteiro (sem cortar as laterais) dentro da janela; o resto é preenchido pela moldura PNG."""
    j = JANELA
    cor = cor_fundo.lstrip("#") or "000000"
    return (f"[0:v]scale={j['w']}:{j['h']}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={j['w']}:{j['h']}:(ow-iw)/2:(oh-ih)/2:color=black{realce},setsar=1[m0];"
            + efeitos.cadeia_zoom("m0", "m1", j["w"], j["h"], zooms or [], foco_y=0.5)
            + f"[m1]pad={LARGURA}:{ALTURA}:{j['x']}:{j['y']}:color=0x{cor},setsar=1[c0];")


def _cadeia_dividido(cam: tuple[int, int, int, int], jogo: tuple[int, int, int, int],
                     zooms: list[tuple[float, float]] | None = None, realce: str = "") -> str:
    """Câmera do streamer (recorte da webcam) em cima e o jogo (recorte central) embaixo; zoom só na câmera."""
    cx, cy, cw, ch = cam
    jx, jy, jw, jh = jogo
    return ("[0:v]split=2[cam_s][jogo_s];"
            f"[cam_s]crop={cw}:{ch}:{cx}:{cy},scale={LARGURA}:{camera.CAM_ALTURA}:flags=lanczos{realce},setsar=1[cam0];"
            + efeitos.cadeia_zoom("cam0", "cam1", LARGURA, camera.CAM_ALTURA, zooms or [], foco_y=0.4)
            + f"[jogo_s]crop={jw}:{jh}:{jx}:{jy},scale={LARGURA}:{camera.JOGO_ALTURA}:flags=lanczos{realce},setsar=1[jogo0];"
            "[cam1][jogo0]vstack=inputs=2,setsar=1[c0];")


def layout_pedido(cfg: dict, status: dict, corte: dict | None = None) -> str:
    """Layout pedido para o Short: o do corte > o desta live > o padrão do canal para a plataforma da live."""
    for escolha in ((corte or {}).get("layout"), (status.get("opcoes") or {}).get("layout")):
        if escolha in camera.LAYOUTS:
            return escolha
    if (status.get("live") or {}).get("plataforma") in fontes.PLATAFORMAS_HLS:
        escolha = cfg.get("layout_twitch_kick", "auto")
    else:
        escolha = cfg.get("layout", "moldura")
    return escolha if escolha in camera.LAYOUTS else "moldura"


def fonte_do_corte(projeto: Projeto, corte: dict, fonte_video: Path) -> tuple[Path, float]:
    """Arquivo de onde sai o corte e o segundo, nele, em que o corte começa.

    Twitch/Kick (hd_por_trecho): a análise usou uma cópia leve da live; aqui baixa só este trecho em HD (com
    MARGEM_HD de folga) e guarda o arquivo, para refazer o Short sem baixar de novo.
    """
    status = projeto.status
    if not status.get("hd_por_trecho"):
        return fonte_video, float(corte["inicio"])
    from .orquestrador import _arquivo_inicio  # import tardio: o orquestrador importa este módulo

    inicio_live = _arquivo_inicio(status) + float(corte["inicio"])
    a = max(0.0, inicio_live - MARGEM_HD)
    b = _arquivo_inicio(status) + float(corte["fim"]) + MARGEM_HD
    nome = f"hd_{int(a * 10)}_{int(b * 10)}"
    pronto = projeto.p("cortes", f"{nome}.mp4")
    if not pronto.exists():
        try:
            pronto = ingestao.baixar_trecho((status.get("live") or {})["url"], projeto.p("cortes"), a, b,
                                            int(status.get("altura_hd", 1080)), nome)
        except (ingestao.ErroIngestao, KeyError) as exc:
            log.aviso(f"Corte {corte['indice']}: o trecho em HD não baixou ({str(exc)[-160:]}); usando a cópia da análise.")
            projeto.alertar("edicao", f"Short {corte['indice']} saiu na resolução da análise: o trecho em HD não baixou.")
            return fonte_video, float(corte["inicio"])
    return pronto, inicio_live - a


FORMATOS_AUDIO = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus")


def trilhas_do_canal(canal: Canal) -> list[Path]:
    pasta = canal.pasta_trilhas
    if not pasta.exists():
        return []
    return sorted(p for p in pasta.iterdir() if p.suffix.lower() in FORMATOS_AUDIO and ".tmp" not in p.name)


def escolher_trilha_corte(canal: Canal, projeto: Projeto, corte: dict) -> Path | None:
    """Trilha do Short: a escolhida no painel, "nenhuma", ou uma das trilhas do canal em rodízio."""
    escolha = str(corte.get("trilha") or "")
    if escolha == "nenhuma":
        return None
    trilhas = trilhas_do_canal(canal)
    if not trilhas and canal.cfg.get("cortes", {}).get("gerar_trilhas", True):
        from . import synthwave

        log.info("O canal ainda não tem trilhas: compondo o pacote synthwave original…")
        synthwave.gerar_pacote(canal.pasta_trilhas)
        trilhas = trilhas_do_canal(canal)
    if not trilhas:
        return None
    if escolha:
        for t in trilhas:
            if t.name == escolha:
                return t
    return trilhas[(int(util.hash_dados(projeto.id), 16) + int(corte.get("indice", 1))) % len(trilhas)]


def _audio(bruto: Path, saida: Path, canal: Canal, projeto: Projeto, duracao: float, volume_db: float,
           trilha: Path | None, indice: int = 1, filtro_voz: str = "anull") -> None:
    voz = saida.with_name(saida.stem + "_voz.wav")
    ffmpeg.rodar(["-i", bruto, "-vn", "-ac", "2", "-ar", "48000", "-af", filtro_voz, "-c:a", "pcm_s16le", voz],
                 descricao="Áudio do corte")
    mix = saida.with_name(saida.stem + "_mix.wav")
    if trilha and volume_db > -60:
        # começa depois da introdução (já com bateria) e varia o ponto de entrada entre os Shorts
        total = ffmpeg.duracao(trilha) or 0
        janela = max(0.0, total - duracao - 3)
        inicio = 0.0 if janela < 6 else 6 + (indice * 13.7) % (janela - 6)
        formato = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        grafo = (f"[1:a]{formato},volume={volume_db:.1f}dB,afade=t=in:st=0:d=1.2,"
                 f"afade=t=out:st={max(0.0, duracao - 1.8):.2f}:d=1.8,atrim=0:{duracao:.3f}[m];"
                 f"[0:a]{formato},asplit=2[v1][v2];"
                 # ducking leve: em gameplay o som do jogo não para, então a música só abaixa um pouco
                 "[m][v2]sidechaincompress=threshold=0.06:ratio=4:attack=20:release=400:makeup=1[md];"
                 "[v1][md]amix=inputs=2:duration=first:normalize=0[a]")
        ffmpeg.rodar(["-i", voz, "-stream_loop", "-1", "-ss", f"{inicio:.2f}", "-i", trilha, "-filter_complex", grafo,
                      "-map", "[a]", "-t", f"{duracao:.3f}", "-c:a", "pcm_s16le", mix], descricao="Música com ducking")
    else:
        shutil.copyfile(voz, mix)
    ffmpeg.normalizar_audio(mix, saida, -14.0, -1.5, 11.0)
    voz.unlink(missing_ok=True)
    mix.unlink(missing_ok=True)


def renderizar(projeto: Projeto, canal: Canal, corte: dict, fonte_video: Path, credito: str, idioma: str | None,
               nav: Navegador) -> dict:
    from .orquestrador import cfg_cortes  # import tardio: o orquestrador importa este módulo

    cfg = cfg_cortes(canal, projeto)
    n = corte["indice"]
    pasta_tmp, pasta_shorts = projeto.p("cortes"), projeto.p("shorts")
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    duracao = round(corte["fim"] - corte["inicio"], 3)
    bruto = pasta_tmp / f"corte_{n}_bruto.mp4"
    origem, inicio_origem = fonte_do_corte(projeto, corte, fonte_video)
    ffmpeg.rodar(["-ss", f"{inicio_origem:.3f}", "-i", origem, "-t", f"{duracao:.3f}", "-c:v", "libx264",
                  "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "256k", bruto], descricao=f"Corte {n}")
    log.info(f"Corte {n}: {util.formatar_tempo(corte['inicio'])}–{util.formatar_tempo(corte['fim'])} ({duracao:.0f}s) "
             f"— {corte.get('gancho') or corte.get('titulo')}")
    editada = legenda_editada(projeto, corte)
    if editada is not None:  # você corrigiu a legenda: usa a sua (não transcreve nem traduz de novo)
        palavras, idioma_fala = editada["palavras"], editada.get("idioma")
        log.info(f"Corte {n}: usando a legenda ajustada por você")
    else:
        palavras, idioma_fala = palavras_do_corte(bruto, cfg.get("idioma_fala") or None)
        projeto.salvar(f"cortes/corte_{n}_palavras.json", palavras)
    info = ffmpeg.info_midia(bruto)
    tema = templates.tema_canal(canal)
    textos = {"gancho": corte.get("gancho", ""), "titulo": corte.get("titulo", ""), "descricao": corte.get("descricao", "")}
    legenda, traduzida_de = palavras, (editada or {}).get("traduzida_de")
    destino = str(cfg.get("idioma_legenda", "pt"))
    if cfg.get("traduzir", True) and traducao.precisa_traduzir(idioma_fala, destino):
        contexto = f"canal de {canal.nicho}"
        nome = traducao.nome_idioma(idioma_fala)
        if palavras and editada is None:
            em_pt = traducao.palavras_traduzidas(palavras, idioma_fala, contexto)
            if em_pt:
                legenda, traduzida_de = em_pt, idioma_fala
                projeto.salvar(f"cortes/corte_{n}_palavras_pt.json", em_pt)
                log.info(f"Corte {n}: fala em {nome} — legenda traduzida para o português")
            else:
                log.aviso(f"Corte {n}: fala em {nome} e nenhum tradutor respondeu (configure uma IA no .env ou "
                          "instale um modelo no Ollama); legenda no idioma original.")
        if corte.get("automatico"):  # textos tirados da fala (sem IA): gancho, título e descrição em português
            chaves = [k for k in ("gancho", "titulo", "descricao") if textos[k]]
            novos = traducao.traduzir([textos[k] for k in chaves], idioma_fala, contexto)
            if novos is None:  # pacote falhou: tenta um por um
                novos = [(traducao.traduzir([textos[k]], idioma_fala, contexto) or [textos[k]])[0] for k in chaves]
            for k, v in zip(chaves, novos):
                textos[k] = v
            if textos["gancho"]:
                textos["gancho"] = " ".join(textos["gancho"].split()[:7]).rstrip(",.;:")
            textos["titulo"] = util.limitar(textos["titulo"], 80, "")
    projeto.salvar(f"cortes/corte_{n}_legenda.json", {"palavras": legenda, "idioma": idioma_fala,
                                                     "traduzida_de": traduzida_de, "inicio": corte["inicio"],
                                                     "fim": corte["fim"], "editada": editada is not None})
    gancho = textos["gancho"] or textos["titulo"]
    largura_src, altura_src = info.get("largura", 1920), info.get("altura", 1080)
    layout, facecam = _decidir_layout(layout_pedido(cfg, projeto.status, corte), bruto, pasta_tmp, n)
    zooms = efeitos.picos_de_reacao(efeitos.volume_db(bruto), duracao) if cfg.get("zoom_reacoes", True) else []
    if zooms:
        log.info(f"Corte {n}: zoom nas reações em " + ", ".join(f"{a:.1f}s" for a, _ in zooms))
    realce = efeitos.realce(cfg)
    if layout == "dividido":  # câmera do streamer em cima, jogo embaixo, gancho na emenda
        cam = camera.caixa_camera(facecam, largura_src, altura_src)
        cadeia = _cadeia_dividido(cam, camera.caixa_jogo(largura_src, altura_src, cam), zooms, realce)
        y_legenda, ancora, tamanho_padrao, enquadramento = LEGENDA_DIVIDIDO_Y, 5, 84, "dividido"
        html = html_dividido(canal, cfg, pasta_shorts / f"corte_{n}_titulo.html", gancho, credito)
    elif layout == "moldura":  # modelo do canal: foto + @ no topo, gancho, vídeo inteiro no centro, legenda embaixo
        cadeia = _cadeia_moldura(str(cfg.get("cor_fundo", "#000000")), zooms, realce)
        y_legenda, ancora, tamanho_padrao, enquadramento = LEGENDA_MOLDURA_Y, 8, 80, "moldura"
        html = html_moldura(canal, cfg, pasta_shorts / f"corte_{n}_titulo.html", gancho, credito,
                            modelo_do_corte(cfg, corte))
    else:  # vertical: recorte 9:16 seguindo o rosto (ou fundo desfocado)
        modo = str(cfg.get("enquadramento", "auto"))
        trilha_rosto = trilha_do_rosto(bruto, pasta_tmp / f"rostos_{n}") if modo in ("auto", "rosto") else None
        cadeia, altura_legenda = _cadeia_quadro(modo, trilha_rosto, largura_src, altura_src, zooms, realce)
        y_legenda, ancora, tamanho_padrao = int(ALTURA * altura_legenda), 5, 88
        enquadramento = "rosto" if (modo != "desfoque" and trilha_rosto) else ("centro" if modo == "centro" else "desfoque")
        html = templates.renderizar("cenas/short_titulo.html", pasta_shorts / f"corte_{n}_titulo.html", tema,
                                    {"titulo": gancho, "handle": credito or canal.handle or canal.nome},
                                    LARGURA, ALTURA, True)
    titulo_png = nav.capturar(html, pasta_shorts / f"corte_{n}_titulo.png", LARGURA, ALTURA, t=5, transparente=True)
    fonte_legenda = tema["fonte_titulos"] if templates.fonte_disponivel(tema["fonte_titulos"]) else "Arial"
    util.salvar_texto(pasta_shorts / f"corte_{n}.ass",
                      gerar_ass(legenda, fonte_legenda, _cor_ass(canal.paleta.get("secundaria", "#ffd700")),
                                y_legenda, tamanho=int(cfg.get("tamanho_legenda", tamanho_padrao)), ancora=ancora))
    audio = pasta_tmp / f"corte_{n}_audio.wav"
    trilha = escolher_trilha_corte(canal, projeto, corte)
    _audio(bruto, audio, canal, projeto, duracao, float(cfg.get("musica_volume_db", -20)), trilha, n,
           efeitos.voz(cfg))
    grafo = cadeia + "[c0][1:v]overlay=0:0[c1];"
    if ffmpeg.tem_filtro("subtitles") and legenda:
        fontes = os.path.relpath(PASTA_FONTES, pasta_shorts).replace("\\", "/") if any(PASTA_FONTES.glob("*.ttf")) else ""
        grafo += f"[c1]subtitles=corte_{n}.ass" + (f":fontsdir={fontes}" if fontes else "") + "[c2];"
    else:
        grafo += "[c1]null[c2];"
    grafo += "[c2]fade=t=in:st=0:d=0.25,format=yuv420p[v]"
    saida = pasta_shorts / f"corte_{n}.mp4"
    ffmpeg.rodar(["-i", str(bruto), "-i", titulo_png.name, "-i", str(audio), "-filter_complex", grafo, "-map", "[v]",
                  "-map", "2:a:0", "-t", f"{duracao:.3f}", "-r", "30", *ffmpeg.args_video("short"), "-c:a", "aac",
                  "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", saida.name],
                 cwd=pasta_shorts, descricao=f"Renderização do corte {n}")
    for temporario in (html, titulo_png, audio, bruto):
        Path(temporario).unlink(missing_ok=True)
    return {"indice": n, "arquivo": f"shorts/{saida.name}", "inicio": corte["inicio"], "duracao": duracao,
            "blocos": [], "gancho": gancho, "titulo": textos["titulo"],
            "descricao": textos["descricao"], "tags": corte.get("tags", []), "motivo": corte.get("motivo", ""),
            "enquadramento": enquadramento, "palavras": len(palavras), "idioma_fala": idioma_fala,
            "trilha": trilha.name if trilha else "nenhuma", "legenda_traduzida_de": traduzida_de,
            "legenda_editada": editada is not None, "layout": layout, "zooms": len(zooms),
            "modelo": modelo_do_corte(cfg, corte) if layout == "moldura" else None}


def _decidir_layout(pedido: str, bruto: Path, pasta_tmp: Path, n: int) -> tuple[str, camera.Facecam | None]:
    """auto: webcam pequena -> dividido; rosto grande -> vertical; sem rosto fixo -> moldura do canal."""
    if pedido not in ("auto", "dividido"):
        return pedido, None
    facecam = camera.detectar(bruto, pasta_tmp / f"camera_{n}")
    if facecam is None:
        if pedido == "dividido":
            log.aviso(f"Corte {n}: não achei a câmera do streamer; usando a moldura do canal.")
        else:
            log.info(f"Corte {n}: sem câmera fixa no vídeo — moldura do canal")
        return "moldura", None
    layout = camera.escolher_layout(facecam) if pedido == "auto" else "dividido"
    log.info(f"Corte {n}: câmera em ({facecam.cx:.2f}, {facecam.cy:.2f}), rosto com {facecam.rosto:.0%} da largura, "
             f"presente em {facecam.presenca:.0%} do corte -> {layout}")
    return layout, facecam
