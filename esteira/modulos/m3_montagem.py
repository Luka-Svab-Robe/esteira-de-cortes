"""Módulo 3 — Montagem visual, motion graphics e masterização de áudio.

Cada bloco vira um clipe com a duração exata do seu áudio (+ sobreposição da
transição). Clipes inalterados são reaproveitados pelo cache, então corrigir
"a cena 4" re-renderiza apenas a cena 4 e a junção final.
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from .. import ffmpeg, log, metadados, midia, templates, util
from ..canal import Canal
from ..navegador import Navegador
from ..projeto import Projeto

VERSAO_CENA = 5
FPS = 30
LARGURA, ALTURA = 1920, 1080
TRANSICOES = {"fade", "dissolve", "fadeblack", "fadewhite", "smoothleft", "smoothright", "smoothup", "smoothdown",
              "slideleft", "slideright", "slideup", "slidedown", "wipeleft", "wiperight", "circleopen",
              "circleclose", "radial", "distance", "hblur", "pixelize", "horzopen", "vertopen"}

CENARIOS = {
    "espaco": ("espaco", "space", "planet", "estrela", "star", "galax", "universo", "universe", "lua", "moon",
               "astronaut", "nasa", "foguete", "rocket", "cosmo", "orbit", "alien", "ovni", "ufo"),
    "oceano": ("mar", "ocean", "navio", "ship", "praia", "beach", "agua", "water", "peixe", "fish", "submarin",
               "naufrag", "wreck", "titanic", "ilha", "island", "onda", "wave", "bermudas"),
    "cidade": ("cidade", "city", "predio", "building", "rua", "street", "urban", "empresa", "company", "dinheiro",
               "money", "econom", "bolsa", "banco", "bank", "tecnolog", "internet", "hacker"),
    "floresta": ("floresta", "forest", "arvore", "tree", "selva", "jungle", "natureza", "nature", "animal",
                 "bosque", "amazon", "trilha", "lobo"),
    "deserto": ("deserto", "desert", "egito", "egypt", "piramide", "pyramid", "areia", "sand", "farao", "pharaoh",
                "mumia", "mummy", "oasis"),
    "laboratorio": ("ciencia", "science", "laborat", "quimic", "chemi", "atomo", "atom", "cerebro", "brain",
                    "medic", "doenca", "disease", "virus", "dna", "experiment", "cientista"),
    "historia": ("guerra", "war", "imperio", "empire", "castelo", "castle", "rei ", "king", "rainha", "queen",
                 "roma", "rome", "medieval", "antig", "ancient", "soldado", "soldier", "batalha", "battle",
                 "templo", "temple", "seculo", "colonia"),
}
ICONES = {
    "lupa": ("investig", "pista", "detetive", "search", "clue", "descobr", "evidenc", "misterio", "mystery"),
    "olho": ("olho", "eye", "vigil", "observ", "espia", "spy", "testemunha", "viu"),
    "raio": ("energia", "eletric", "raio", "lightning", "power", "explos"),
    "atomo": ("atomo", "atom", "fisica", "physics", "nuclear", "quantic"),
    "coroa": ("rei", "rainha", "imperio", "king", "queen", "crown", "poder", "trono"),
    "livro": ("livro", "book", "manuscrito", "biblioteca", "escrit", "document", "carta"),
    "globo": ("mundo", "world", "pais", "global", "terra", "earth", "mapa", "map"),
    "relogio": ("tempo", "time", "seculo", "hora", "relogio", "clock", "decada", "ano "),
    "cadeado": ("segredo", "secret", "proibido", "lock", "oculto", "confidencial", "trancad"),
    "caveira": ("morte", "death", "morreu", "assassin", "crime", "skull", "maldic", "curse", "mortos", "cadaver"),
    "foguete": ("foguete", "rocket", "lancamento", "nasa", "missao espacial"),
    "dinheiro": ("dinheiro", "money", "rico", "fortuna", "ouro", "gold", "tesouro", "treasure", "milion"),
    "cerebro": ("cerebro", "mente", "mind", "brain", "psicolog", "memoria", "consciencia"),
    "interrogacao": ("?", "porque", "pergunta", "enigma", "duvida"),
}
# quando nenhuma palavra-chave casa, sorteia só entre ícones que servem a qualquer tema
ICONES_NEUTROS = ["lupa", "olho", "interrogacao", "relogio", "livro", "globo"]
FILTROS_COR = {
    "nenhum": "",
    "cinematico": "eq=contrast=1.06:saturation=0.88:brightness=-0.02,vignette=PI/5",
    "quente": "colorbalance=rs=0.05:bs=-0.05,eq=saturation=1.05",
    "frio": "colorbalance=rs=-0.04:bs=0.06,eq=saturation=0.95",
    "noir": "hue=s=0,eq=contrast=1.18:brightness=-0.03,vignette=PI/4",
}
MOVIMENTOS = ("zoom_in", "pan_direita", "zoom_out", "pan_esquerda")


class ErroMontagem(RuntimeError):
    pass


# ------------------------------------------------------------- utilidades
def _escolher(chaves: dict, texto: str, padrao: str) -> str:
    alvo = " " + util.sem_acentos(texto.lower()) + " "
    termos = util.normalizar(texto).split()

    def casa(chave: str) -> int:  # casa no INÍCIO das palavras ("mente" não casa com "simplesmente")
        chave = chave.strip()
        if chave == "?" or " " in chave:
            return alvo.count(chave)
        return sum(1 for t in termos if t.startswith(chave))

    pontos = {nome: sum(casa(p) for p in palavras) for nome, palavras in chaves.items()}
    melhor = max(pontos, key=pontos.get)
    return melhor if pontos[melhor] > 0 else padrao


def resolver_modo(bloco: dict, estilo: str) -> str:
    modo = bloco.get("modo_visual", "video_banco")
    if estilo == "tipografia" and not (modo == "cartao_animado" and bloco.get("tipo_cartao") in ("contador", "lista", "grafico")):
        return "tipografia"
    return modo


def _preparar_capa(origem: Path, destino: Path) -> Path:
    from PIL import Image, ImageOps

    with Image.open(origem) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (LARGURA * 2, ALTURA * 2), Image.LANCZOS, centering=(0.5, 0.45))
        im.save(destino, quality=93)
    return destino


def _salvar_quadro(origem: Path, destino: Path) -> Path:
    from PIL import Image, ImageOps

    with Image.open(origem) as im:
        ImageOps.fit(im.convert("RGB"), (LARGURA, ALTURA), Image.LANCZOS).save(destino, quality=92)
    return destino


def _movimento_zoompan(tipo: str, quadros: int, intensidade: float) -> str:
    z = intensidade
    centro_x, centro_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if tipo == "zoom_out":
        return f"zoompan=z='{z}-({z}-1)*on/{quadros}':x='{centro_x}':y='{centro_y}'"
    if tipo == "pan_direita":
        return f"zoompan=z='{z}':x='(iw-iw/zoom)*on/{quadros}':y='{centro_y}'"
    if tipo == "pan_esquerda":
        return f"zoompan=z='{z}':x='(iw-iw/zoom)*(1-on/{quadros})':y='{centro_y}'"
    return f"zoompan=z='1+({z}-1)*on/{quadros}':x='{centro_x}':y='{centro_y}'"


def _cadeia_overlay(overlay: Path | None, duracao: float, indice: int) -> tuple[list, str]:
    if not overlay:
        return [], "[base]format=yuv420p[v]"
    saida_fade = max(0.5, duracao - 0.7)
    return (["-loop", "1", "-t", f"{duracao:.3f}", "-i", overlay],
            f"[{indice}:v]format=rgba,fade=t=in:st=0.35:d=0.45:alpha=1,fade=t=out:st={saida_fade:.2f}:d=0.45:alpha=1[ov];"
            f"[base][ov]overlay=0:0:format=auto,format=yuv420p[v]")


def cena_ken_burns(capa: Path, duracao: float, saida: Path, movimento: str, intensidade: float, filtro_cor: str,
                   overlay: Path | None) -> None:
    quadros = max(2, int(round((duracao + 0.2) * FPS)))
    cadeia = _movimento_zoompan(movimento, quadros, intensidade) + f":d={quadros}:s={LARGURA}x{ALTURA}:fps={FPS}"
    if filtro_cor:
        cadeia += "," + filtro_cor
    extras, final = _cadeia_overlay(overlay, duracao, 1)
    ffmpeg.rodar(["-i", capa, *extras, "-filter_complex", f"[0:v]{cadeia},setsar=1[base];{final}", "-map", "[v]",
                  "-t", f"{duracao:.3f}", "-r", str(FPS), *ffmpeg.args_video("cena"), "-an", saida],
                 descricao=f"Ken Burns de {Path(capa).name}")


def cena_video(video: Path, duracao: float, saida: Path, velocidade: float, filtro_cor: str,
               overlay: Path | None) -> None:
    cadeia = (f"setpts=PTS/{velocidade:.3f},scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
              f"crop={LARGURA}:{ALTURA},fps={FPS}")
    if filtro_cor:
        cadeia += "," + filtro_cor
    extras, final = _cadeia_overlay(overlay, duracao, 1)
    ffmpeg.rodar(["-stream_loop", "-1", "-i", video, *extras, "-filter_complex",
                  f"[0:v]{cadeia},setsar=1[base];{final}", "-map", "[v]", "-t", f"{duracao:.3f}", "-r", str(FPS),
                  *ffmpeg.args_video("cena"), "-an", saida], descricao=f"Clipe de {Path(video).name}")


# ------------------------------------------------------------------ cenas
class Montador:
    def __init__(self, projeto: Projeto, canal: Canal, nav: Navegador):
        self.projeto = projeto
        self.canal = canal
        self.nav = nav
        self.tema = templates.tema_canal(canal)
        self.cache = projeto.ler("cenas/cache.json", {}) or {}
        self.indice_midia = projeto.ler("midia/indice.json", {}) or {}
        self.usados = {v.get("identificador") for v in self.indice_midia.values() if v.get("identificador")}
        self.pasta_html = projeto.p("cenas", "html")
        self.pasta_html.mkdir(parents=True, exist_ok=True)
        self.filtro_cor = FILTROS_COR.get(canal.cfg["identidade_visual"].get("filtro_cor", "nenhum"), "")
        dinamico = canal.ritmo.get("movimento_camera") == "dinamico"
        self.intensidade = 1.22 if dinamico else 1.12
        self.velocidade_video = 1.0 if dinamico else 0.9
        self.anim_seg = float(canal.ritmo.get("animacao_seg", 3.0))

    # ---------------------------------------------------------------- mídia
    def _midia(self, bloco: dict, modo: str) -> midia.Midia | None:
        bid = str(bloco["id"])
        chave = util.hash_dados({"modo": modo, "busca": bloco.get("busca_visual"), "variante": bloco.get("midia_variante", 0),
                                 "arquivo": bloco.get("midia_arquivo")})
        salvo = self.indice_midia.get(bid)
        if salvo and salvo.get("chave") == chave and self.projeto.p(salvo["arquivo"]).exists():
            return midia.Midia(self.projeto.p(salvo["arquivo"]), salvo["tipo"], salvo.get("credito", {}),
                               salvo.get("identificador", ""))
        if salvo and salvo.get("identificador"):
            self.usados.discard(salvo["identificador"])
        base = self.projeto.p("midia", f"bloco_{bloco['id']:03d}")
        for antigo in self.projeto.p("midia").glob(f"bloco_{bloco['id']:03d}*"):
            antigo.unlink(missing_ok=True)
        resultado = None
        arquivo_usuario = bloco.get("midia_arquivo")
        termo = bloco.get("busca_visual") or bloco["narracao"]
        contexto = bloco["narracao"]  # nomes próprios da narração ajudam a busca (ex.: "Flannan")
        variante = int(bloco.get("midia_variante", 0) or 0)
        if arquivo_usuario:
            origem = Path(arquivo_usuario)
            origem = origem if origem.is_absolute() else self.projeto.p(arquivo_usuario)
            if origem.exists():
                tipo = "video" if origem.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") else "imagem"
                destino = base.with_suffix(origem.suffix.lower())
                shutil.copyfile(origem, destino)
                resultado = midia.Midia(destino, tipo, {"tipo": "Mídia", "autor": "arquivo do usuário", "fonte": "local"})
            else:
                log.aviso(f"Bloco {bid}: arquivo de mídia '{arquivo_usuario}' não encontrado; buscando outra.")
        if resultado is None and modo == "video_banco":
            resultado = midia.buscar_video(termo, base, variante, self.usados, contexto) or midia.buscar_imagem(termo, base, variante, self.usados, contexto)
        if resultado is None and modo == "imagem_ia":
            resultado = midia.gerar_imagem_ia(termo, base, semente=bloco["id"] * 97 + variante) or \
                midia.buscar_imagem(termo, base, variante, self.usados, contexto)
        if resultado is None:
            return None
        self.usados.add(resultado.identificador)
        self.indice_midia[bid] = {"chave": chave, "arquivo": str(resultado.arquivo.relative_to(self.projeto.pasta)),
                                  "tipo": resultado.tipo, "credito": resultado.credito,
                                  "identificador": resultado.identificador}
        self.projeto.salvar("midia/indice.json", self.indice_midia)
        return resultado

    # ------------------------------------------------------------- render
    def _html(self, nome_template: str, nome: str, dados: dict, transparente: bool = False,
              largura: int = LARGURA, altura: int = ALTURA) -> Path:
        return templates.renderizar(nome_template, self.pasta_html / f"{nome}.html", self.tema, dados, largura, altura,
                                    transparente)

    def overlay_destaque(self, bloco: dict) -> Path | None:
        texto = (bloco.get("destaque") or "").strip()
        if not texto:
            return None
        destino = self.projeto.p("cenas", "overlays", f"destaque_{bloco['id']:03d}.png")
        html = self._html("cenas/overlay_destaque.html", f"overlay_{bloco['id']:03d}", {"texto": texto},
                          transparente=True)
        return self.nav.capturar(html, destino, LARGURA, ALTURA, t=5.0, transparente=True)

    def _dados_ilustracao(self, bloco: dict, sem_texto: bool = False) -> dict:
        texto_busca = f"{bloco.get('busca_visual', '')} {bloco['narracao']}"
        return {"cenario": _escolher(CENARIOS, texto_busca, "montanhas"),
                "icone": _escolher(ICONES, texto_busca, random.Random(bloco["id"]).choice(ICONES_NEUTROS)),
                "texto": "" if sem_texto else bloco.get("destaque", ""), "semente": bloco["id"]}

    def construir(self, bloco: dict, duracao: float, palavras: list[dict], forcar: bool) -> dict:
        bid = bloco["id"]
        modo = resolver_modo(bloco, self.canal.estilo)
        saida = self.projeto.p("cenas", f"cena_{bid:03d}.mp4")
        chave = util.hash_dados({
            "v": VERSAO_CENA, "modo": modo, "tipo": bloco.get("tipo_cartao"), "dados": bloco.get("dados_cartao"),
            "destaque": bloco.get("destaque"), "busca": bloco.get("busca_visual"), "variante": bloco.get("midia_variante"),
            "arquivo": bloco.get("midia_arquivo"), "duracao": round(duracao, 3), "tema": {k: v for k, v in self.tema.items() if k not in templates.PESADOS},
            "filtro": self.filtro_cor, "ritmo": self.canal.ritmo,
            "palavras": palavras if modo == "tipografia" else None, "narracao": bloco["narracao"] if modo == "tipografia" else None,
        })
        salvo = self.cache.get(str(bid))
        if not forcar and salvo and salvo.get("chave") == chave and saida.exists():
            return salvo
        log.info(f"Cena {bid:03d}: {modo}" + (f"/{bloco.get('tipo_cartao')}" if modo == "cartao_animado" else "")
                 + f" ({duracao:.1f}s)")
        quadro_limpo = None
        modo_final = modo
        if modo in ("video_banco", "imagem_ia"):
            recurso = self._midia(bloco, modo)
            if recurso is None:
                log.aviso(f"Cena {bid}: nenhuma mídia encontrada para '{bloco.get('busca_visual')}'; usando ilustração 2D.")
                self.projeto.alertar("montagem", f"Cena {bid}: mídia não encontrada, substituída por ilustração 2D.")
                modo_final = "ilustracao_2d"
            else:
                overlay = self.overlay_destaque(bloco)
                if recurso.tipo == "video":
                    cena_video(recurso.arquivo, duracao, saida, self.velocidade_video, self.filtro_cor, overlay)
                    quadro_limpo = self.projeto.p("midia", f"bloco_{bid:03d}_quadro.jpg")
                    tempo = min(ffmpeg.duracao(recurso.arquivo) * 0.4, max(0.1, duracao * 0.4))
                    ffmpeg.rodar(["-ss", f"{tempo:.2f}", "-i", recurso.arquivo, "-frames:v", "1", "-vf",
                                  f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,crop={LARGURA}:{ALTURA}",
                                  "-q:v", "2", quadro_limpo], descricao="Quadro de referência")
                else:
                    capa = _preparar_capa(recurso.arquivo, self.projeto.p("midia", f"bloco_{bid:03d}_capa.jpg"))
                    movimento = MOVIMENTOS[bid % len(MOVIMENTOS)]
                    cena_ken_burns(capa, duracao, saida, movimento, self.intensidade, self.filtro_cor, overlay)
                    quadro_limpo = _salvar_quadro(capa, self.projeto.p("midia", f"bloco_{bid:03d}_quadro.jpg"))
        if modo_final == "ilustracao_2d":
            html = self._html("cenas/ilustracao_2d.html", f"cena_{bid:03d}", self._dados_ilustracao(bloco))
            self.nav.renderizar_clipe(html, saida, duracao, fps=15, anim_seg=None)
            html_limpo = self._html("cenas/ilustracao_2d.html", f"quadro_{bid:03d}", self._dados_ilustracao(bloco, True))
            png = self.nav.capturar(html_limpo, self.projeto.p("midia", f"bloco_{bid:03d}_quadro.png"), LARGURA, ALTURA,
                                    t=min(2.5, duracao / 2))
            quadro_limpo = _salvar_quadro(png, self.projeto.p("midia", f"bloco_{bid:03d}_quadro.jpg"))
            png.unlink(missing_ok=True)
        elif modo_final == "tipografia":
            inicio_bloco = palavras[0]["inicio"] if palavras else 0
            dados = {"palavras": palavras, "titulo": bloco.get("destaque", ""), "inicio_bloco": inicio_bloco,
                     "duracao": duracao}
            html = self._html("cenas/tipografia.html", f"cena_{bid:03d}", dados)
            self.nav.renderizar_clipe(html, saida, duracao, fps=15, anim_seg=None)
        elif modo_final == "cartao_animado":
            tipo = bloco.get("tipo_cartao") if bloco.get("tipo_cartao") in ("titulo", "contador", "lista", "grafico", "citacao") else "titulo"
            dados = dict(bloco.get("dados_cartao") or {})
            dados.setdefault("titulo", bloco.get("destaque", ""))
            dados["destaque"] = bloco.get("destaque", "")
            dados["indice"] = bid
            html = self._html(f"cenas/cartao_{tipo}.html", f"cena_{bid:03d}", dados)
            self.nav.renderizar_clipe(html, saida, duracao, fps=FPS, anim_seg=self.anim_seg)
        previa = self.projeto.p("cenas", f"previa_{bid:03d}.jpg")
        ffmpeg.rodar(["-ss", f"{min(duracao * 0.6, max(0.0, duracao - 0.2)):.2f}", "-i", saida, "-frames:v", "1",
                      "-vf", "scale=480:-2", "-q:v", "4", previa], descricao="Prévia da cena")
        registro = {"chave": chave, "modo": modo_final, "arquivo": f"cenas/cena_{bid:03d}.mp4",
                    "previa": f"cenas/previa_{bid:03d}.jpg",
                    "quadro_limpo": str(quadro_limpo.relative_to(self.projeto.pasta)) if quadro_limpo else None}
        self.cache[str(bid)] = registro
        self.projeto.salvar("cenas/cache.json", self.cache)
        return registro

    def garantir_logo(self) -> Path:
        logo = self.canal.caminho_logo()
        if logo:
            return logo
        destino = self.canal.pasta / "assets" / "logo_auto.png"
        html = self._html("cenas/logo.html", "logo", {"nome": self.canal.nome}, transparente=True, largura=512, altura=512)
        self.nav.capturar(html, destino, 512, 512, t=0, transparente=True)
        log.info(f"Logo gerado automaticamente em {destino} (substitua por assets/logo.png quando quiser)")
        return destino


# ------------------------------------------------------- junção de cenas
def _posicao_logo(posicao: str, margem: int) -> tuple[str, str]:
    x = f"{margem}" if "esquerdo" in posicao else f"main_w-overlay_w-{margem}"
    y = f"{margem}" if "superior" in posicao else f"main_h-overlay_h-{margem}"
    return x, y


def _juntar_lote(clipes: list[Path], duracoes: list[float], d: float, transicao: str, saida: Path, total: float,
                 perfil: str, logo: dict | None = None, audio: Path | None = None) -> None:
    entradas, partes = [], []
    for i, clipe in enumerate(clipes):
        entradas += ["-i", clipe]
        partes.append(f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p,setsar=1[v{i}]")
    atual, offset = "v0", 0.0
    for i in range(1, len(clipes)):
        offset += duracoes[i - 1]
        partes.append(f"[{atual}][v{i}]xfade=transition={transicao}:duration={d:.3f}:offset={offset:.3f}[x{i}]")
        atual = f"x{i}"
    n = len(clipes)
    if logo:
        entradas += ["-i", logo["arquivo"]]
        partes.append(f"[{n}:v]format=rgba,scale={logo['largura']}:-1,colorchannelmixer=aa={logo['opacidade']:.2f}[lg]")
        partes.append(f"[{atual}][lg]overlay={logo['x']}:{logo['y']}:format=auto,format=yuv420p[vout]")
        atual = "vout"
        n += 1
    mapa = ["-map", f"[{atual}]"]
    audio_args: list[str] = []
    if audio:
        entradas += ["-i", audio]
        mapa += ["-map", f"{n}:a:0"]
        audio_args = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"]
    ffmpeg.rodar([*entradas, "-filter_complex", ";".join(partes), *mapa, "-t", f"{total:.3f}", "-r", str(FPS),
                  *ffmpeg.args_video(perfil), *audio_args, "-movflags", "+faststart", saida],
                 descricao="Junção das cenas")


def juntar_com_transicoes(clipes: list[Path], duracoes: list[float], d: float, transicao: str, saida: Path,
                          pasta_tmp: Path, logo: dict | None, audio: Path | None, lote: int = 12) -> None:
    """Transições cruzadas em níveis (lotes), para não abrir dezenas de vídeos ao mesmo tempo."""
    total = sum(duracoes)
    if len(clipes) <= lote:
        _juntar_lote(clipes, duracoes, d, transicao, saida, total, "final", logo, audio)
        return
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    grupos = list(util.lotes(list(range(len(clipes))), lote))
    intermediarios, duracoes_grupo = [], []
    for gi, grupo in enumerate(grupos):
        destino = pasta_tmp / f"grupo_{gi:02d}.mp4"
        dur = [duracoes[i] for i in grupo]
        ultimo = gi == len(grupos) - 1
        _juntar_lote([clipes[i] for i in grupo], dur, d, transicao, destino, sum(dur) + (0.1 if ultimo else d + 0.1),
                     "intermediario")
        intermediarios.append(destino)
        duracoes_grupo.append(sum(dur))
    juntar_com_transicoes(intermediarios, duracoes_grupo, d, transicao, saida, pasta_tmp / "nivel", logo, audio, lote)


def juntar_com_cortes(clipes: list[Path], saida: Path, pasta_tmp: Path, total: float, logo: dict | None,
                      audio: Path | None) -> None:
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    lista = pasta_tmp / "cortes.txt"
    util.salvar_texto(lista, "\n".join(f"file '{Path(c).resolve().as_posix()}'" for c in clipes) + "\n")
    unido = pasta_tmp / "unido.mp4"
    ffmpeg.rodar(["-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", unido], descricao="Concatenação das cenas")
    _juntar_lote([unido], [total], 0, "fade", saida, total, "final", logo, audio)


# ------------------------------------------------------------------ áudio
def _gerar_ambiente(destino: Path, duracao: float) -> Path:
    """Trilha ambiente sutil (acorde menor com variações lentas) quando o canal não tem músicas."""
    esquerdo = ("0.10*sin(2*PI*55*t)+0.07*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*0.07*t))"
                "+0.05*sin(2*PI*130.81*t)*(0.5+0.5*sin(2*PI*0.05*t+1))+0.04*sin(2*PI*164.81*t)*(0.5+0.5*sin(2*PI*0.03*t+2))")
    direito = ("0.10*sin(2*PI*55.2*t)+0.07*sin(2*PI*110.3*t)*(0.6+0.4*sin(2*PI*0.06*t+0.5))"
               "+0.05*sin(2*PI*130.6*t)*(0.5+0.5*sin(2*PI*0.045*t+2))+0.04*sin(2*PI*196*t)*(0.5+0.5*sin(2*PI*0.035*t+1))")
    ffmpeg.rodar(["-f", "lavfi", "-i", f"aevalsrc='{esquerdo}|{direito}':s=48000:d={duracao + 1:.2f}", "-af",
                  "lowpass=f=900,aecho=0.8:0.7:180|360:0.3|0.2,volume=4dB", "-c:a", "pcm_s16le", destino],
                 descricao="Geração da trilha ambiente")
    return destino


def escolher_trilha(projeto: Projeto, canal: Canal, duracao: float) -> Path | None:
    pasta = canal.pasta_trilhas
    trilhas = sorted(p for p in pasta.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".flac")) \
        if pasta.exists() else []
    if trilhas:
        return trilhas[int(util.hash_dados(projeto.id), 16) % len(trilhas)]
    if canal.musica.get("gerar_ambiente_se_vazio", True):
        return _gerar_ambiente(projeto.p("render", "ambiente.wav"), duracao)
    return None


def mixar_e_masterizar(projeto: Projeto, canal: Canal, duracao: float) -> tuple[Path, dict]:
    narracao = projeto.p("audio", "narracao_completa.wav")
    mix = projeto.p("render", "mix.wav")
    trilha = escolher_trilha(projeto, canal, duracao)
    formato = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if trilha:
        log.info(f"Trilha: {trilha.name} ({canal.musica.get('volume_db', -20)} dB"
                 f"{', com ducking' if canal.musica.get('ducking', True) else ''})")
        musica = (f"[1:a]{formato},volume={float(canal.musica.get('volume_db', -20)):.1f}dB,"
                  f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.0, duracao - 3):.2f}:d=3,atrim=0:{duracao:.3f}[m]")
        if canal.musica.get("ducking", True):
            grafo = (f"{musica};[0:a]{formato},asplit=2[v1][v2];"
                     "[m][v2]sidechaincompress=threshold=0.025:ratio=10:attack=15:release=400:makeup=1[md];"
                     "[v1][md]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            grafo = f"{musica};[0:a]{formato}[v1];[v1][m]amix=inputs=2:duration=first:normalize=0[a]"
        ffmpeg.rodar(["-i", narracao, "-stream_loop", "-1", "-i", trilha, "-filter_complex", grafo, "-map", "[a]",
                      "-t", f"{duracao:.3f}", "-c:a", "pcm_s16le", mix], descricao="Mixagem com trilha e ducking")
    else:
        ffmpeg.rodar(["-i", narracao, "-af", formato, "-c:a", "pcm_s16le", mix], descricao="Mixagem da narração")
    master = projeto.p("render", "audio_master.wav")
    medicao = ffmpeg.normalizar_audio(mix, master, -14.0, -1.5, 11.0)
    return master, medicao


# ------------------------------------------------------------------ etapa
def executar(projeto: Projeto, canal: Canal, blocos_alvo: set[int] | None = None, forcar: bool = False) -> None:
    log.etapa(f"Módulo 3 — Montagem visual (estilo {canal.estilo}) e masterização")
    roteiro = projeto.roteiro()
    linha = projeto.linha_tempo()
    if len(linha) != len(roteiro["blocos"]):
        raise ErroMontagem("A linha do tempo não corresponde ao roteiro: rode a narração novamente.")
    palavras = projeto.ler("legendas/palavras.json", []) or []
    transicao = str(canal.ritmo.get("transicao", "fade")).lower()
    d = 0.0 if transicao == "corte" else max(0.1, float(canal.ritmo.get("transicao_seg", 0.5)))
    if transicao != "corte" and transicao not in TRANSICOES:
        log.aviso(f"Transição '{transicao}' desconhecida; usando 'fade'.")
        transicao = "fade"
    duracoes = [item["duracao"] for item in linha]
    total = sum(duracoes)
    projeto.limpar_alertas("montagem")
    clipes = []
    with Navegador() as nav:
        montador = Montador(projeto, canal, nav)
        logo_arquivo = montador.garantir_logo()
        quadro_anterior = 0
        for i, (bloco, item) in enumerate(zip(roteiro["blocos"], linha)):
            ultimo = i == len(linha) - 1
            if transicao == "corte":
                # limites exatos em quadros evitam deriva entre áudio e vídeo
                quadro_fim = int(round(item["fim"] * FPS))
                duracao_clipe = (quadro_fim - quadro_anterior) / FPS
                quadro_anterior = quadro_fim
            else:
                duracao_clipe = item["duracao"] + (0.1 if ultimo else d + 0.1)
            palavras_bloco = [{"palavra": p["palavra"], "inicio": round(p["inicio"] - item["inicio"], 3),
                               "fim": round(p["fim"] - item["inicio"], 3)} for p in palavras if p["bloco"] == bloco["id"]]
            alvo = forcar or (blocos_alvo is not None and bloco["id"] in blocos_alvo)
            registro = montador.construir(bloco, duracao_clipe, palavras_bloco, alvo)
            clipes.append(projeto.p(registro["arquivo"]))
    creditos = [v["credito"] for k, v in sorted(montador.indice_midia.items(), key=lambda kv: int(kv[0]))
                if v.get("credito") and v["credito"].get("fonte") not in ("local", "IA") and int(k) in {b["id"] for b in roteiro["blocos"]}]
    projeto.salvar("midia/creditos.json", creditos)

    log.info("Mixando trilha, ducking e loudness (-14 LUFS / -1.5 dBTP)…")
    master, medicao = mixar_e_masterizar(projeto, canal, total)
    marca = canal.marca
    x, y = _posicao_logo(marca.get("posicao", "inferior_direito"), int(marca.get("margem", 40)))
    logo = {"arquivo": logo_arquivo, "largura": max(48, int(LARGURA * float(marca.get("escala", 0.08)))),
            "opacidade": float(marca.get("opacidade", 0.8)), "x": x, "y": y}
    temporario = projeto.p("render", "video_tmp.mp4")
    pasta_juncao = projeto.p("render", "juncao")
    shutil.rmtree(pasta_juncao, ignore_errors=True)
    log.info(f"Unindo {len(clipes)} cenas com transição '{transicao}'…")
    if transicao == "corte":
        juntar_com_cortes(clipes, temporario, pasta_juncao, total, logo, master)
    else:
        juntar_com_transicoes(clipes, duracoes, d, transicao, temporario, pasta_juncao, logo, master)
    final = ffmpeg.medir_loudness(temporario)
    if final.get("input_tp", -99) > -1.5:
        ganho = -1.5 - float(final["input_tp"]) - 0.1
        corrigido = projeto.p("render", "video_pico.mp4")
        ffmpeg.ajustar_pico(temporario, corrigido, ganho)
        temporario.unlink(missing_ok=True)
        corrigido.replace(temporario)
        final = ffmpeg.medir_loudness(temporario)
    temporario.replace(projeto.video_final)
    shutil.rmtree(pasta_juncao, ignore_errors=True)
    loudness = {"integrado_lufs": round(float(final.get("input_i", 0)), 2),
                "pico_dbtp": round(float(final.get("input_tp", 0)), 2), "lra": round(float(final.get("input_lra", 0)), 2)}
    metadados.atualizar(projeto, canal)
    projeto.marcar_etapa("montagem", True, duracao_seg=round(total, 2), cenas=len(clipes), loudness=loudness,
                         encoder=ffmpeg.encoder_video())
    log.ok(f"Vídeo final: {projeto.video_final.name} — {util.formatar_tempo(total)}, "
           f"{loudness['integrado_lufs']} LUFS, pico {loudness['pico_dbtp']} dBTP")
