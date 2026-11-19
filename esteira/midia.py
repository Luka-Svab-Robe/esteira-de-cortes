"""Aquisição de mídia: vídeos/fotos de bancos gratuitos e imagens geradas por IA.

Cadeia de alternativas (configurável pelo .env):
  vídeo  -> Pexels -> Pixabay
  foto   -> Pexels -> Pixabay -> Openverse (Creative Commons, sem chave)
  IA     -> Pollinations (grátis) ou comando local (ex.: Stable Diffusion na GPU)
"""
from __future__ import annotations

import platform
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from . import log, util
from .config import env

AGENTE_HTTP = "EsteiraCanalDark/1.0 (+producao audiovisual automatizada)"
_ultimo_pollinations = 0.0  # horário da última chamada ao Pollinations (limite gratuito por IP)
_ultimo_por_host: dict[str, float] = {}
_bloqueado_ate: dict[str, float] = {}  # host -> horário até o qual não vale tentar (HTTP 429 longo)


def agente_http() -> str:
    """User-Agent descritivo. A Wikimedia (origem da maioria das imagens do Openverse) pede um
    contato no User-Agent e limita mais quem não informa; defina CONTATO_USER_AGENT no .env."""
    contato = env("CONTATO_USER_AGENT")
    detalhe = f"producao de video; contato: {contato}" if contato else "producao de video pessoal"
    return f"EsteiraCanalDark/1.0 ({detalhe}) python-requests/{requests.__version__}"
_PALAVRAS_PT = {"de", "da", "do", "das", "dos", "uma", "um", "com", "em", "na", "no", "para", "e"}


@dataclass
class Midia:
    arquivo: Path
    tipo: str  # video | imagem
    credito: dict = field(default_factory=dict)
    identificador: str = ""


# Substantivos visuais comuns: os bancos gratuitos (sobretudo o Openverse) quase só respondem em inglês.
_PT_EN = {
    "farol": "lighthouse", "faroleiro": "lighthouse keeper", "navio": "ship", "barco": "boat", "bote": "lifeboat",
    "mar": "sea", "oceano": "ocean", "ilha": "island", "praia": "beach", "costa": "coast", "tempestade": "storm",
    "onda": "wave", "floresta": "forest", "arvore": "tree", "montanha": "mountain", "deserto": "desert",
    "cidade": "city", "rua": "street", "casa": "house", "castelo": "castle", "igreja": "church", "guerra": "war",
    "soldado": "soldier", "rei": "king", "rainha": "queen", "noite": "night", "lua": "moon", "sol": "sun",
    "ceu": "sky", "estrela": "star", "espaco": "space", "planeta": "planet", "aviao": "airplane", "voo": "flight",
    "piloto": "pilot", "trem": "train", "carro": "car", "estrada": "road", "ponte": "bridge", "rio": "river",
    "lago": "lake", "neve": "snow", "gelo": "ice", "fogo": "fire", "chuva": "rain", "nevoa": "fog",
    "neblina": "fog", "caverna": "cave", "tunel": "tunnel", "mapa": "map", "livro": "book", "carta": "letter",
    "manuscrito": "manuscript", "documento": "document", "dinheiro": "money", "ouro": "gold", "tesouro": "treasure",
    "policia": "police", "detetive": "detective", "cemiterio": "cemetery", "esqueleto": "skeleton", "cranio": "skull",
    "laboratorio": "laboratory", "cientista": "scientist", "hospital": "hospital", "medico": "doctor",
    "computador": "computer", "relogio": "clock", "porta": "door", "portao": "gate", "janela": "window", "cama": "bed",
    "casaco": "coat", "corda": "rope", "tripulacao": "crew", "capitao": "captain", "marinheiro": "sailor",
    "equipe": "team", "homem": "man", "mulher": "woman", "crianca": "child", "pessoa": "people", "multidao": "crowd",
    "piramide": "pyramid", "templo": "temple", "ruina": "ruins", "barril": "barrel", "luz": "light",
    "escuridao": "darkness", "sombra": "shadow", "fantasma": "ghost", "misterio": "mystery", "ponte": "bridge",
    "egito": "egypt", "escocia": "scotland", "inglaterra": "england", "irlanda": "ireland", "russia": "russia",
    "japao": "japan", "china": "china", "brasil": "brazil", "atlantico": "atlantic", "pacifico": "pacific",
    "europa": "europe", "africa": "africa", "antartida": "antarctica", "vulcao": "volcano", "terremoto": "earthquake",
    "satelite": "satellite", "foguete": "rocket", "submarino": "submarine", "naufragio": "shipwreck",
}


def _idioma(termo: str) -> str:
    palavras = {util.radical(p) for p in util.normalizar(termo).split()}
    radicais_pt = {util.radical(p) for p in _PT_EN} | {util.radical(p) for p in _PALAVRAS_PT}
    if termo != util.sem_acentos(termo) or palavras & radicais_pt:
        return "pt"
    return "en"


def nomes_proprios(texto: str) -> list[str]:
    """Palavras com inicial maiúscula fora do começo de frase (ex.: Flannan, Escócia)."""
    nomes = []
    for frase in util.dividir_frases(texto or ""):
        for palavra in re.findall(r"[\wÀ-ÿ]+", frase)[1:]:
            if palavra[:1].isupper() and len(palavra) > 2 and util.normalizar(palavra) not in util.STOPWORDS:
                if palavra not in nomes:
                    nomes.append(palavra)
    return nomes[:3]


def traduzir_termo(termo: str, contexto: str = "", com_nomes: bool = True) -> str:
    """Tradução aproximada (dicionário) de um termo de busca em português (+ nomes próprios do contexto)."""
    saida: list[str] = []
    for nome in (nomes_proprios(contexto) if com_nomes else []):
        normal = util.normalizar(nome)
        saida.append(_PT_EN.get(normal, nome))
    for palavra in util.normalizar(termo).split():
        base = palavra[:-1] if palavra.endswith("s") and palavra[:-1] in _PT_EN else palavra
        if base in _PT_EN:
            saida.append(_PT_EN[base])
    unicos: list[str] = []
    for item in saida:
        if item.lower() not in (u.lower() for u in unicos):
            unicos.append(item)
    return " ".join(unicos[:5])


def variantes_busca(termo: str, contexto: str = "") -> list[str]:
    variantes = [termo]
    if _idioma(termo) == "pt" or nomes_proprios(contexto):
        # bancos como o Openverse exigem TODOS os termos: um nome raro ("Flannan") zera a busca,
        # então tenta primeiro com os nomes próprios e depois só com os substantivos traduzidos.
        # Uma palavra solta ("team") traria fotos genéricas fora de contexto: exige ao menos duas.
        for traduzido in (traduzir_termo(termo, contexto), traduzir_termo(termo, contexto, com_nomes=False)):
            if len(traduzido.split()) >= 2 and traduzido.lower() not in (v.lower() for v in variantes):
                variantes.append(traduzido)
        curto = " ".join(util.palavras_chave(termo, 3))
        if curto and curto not in variantes:
            variantes.append(curto)
    return variantes


def _espacar(url: str, intervalo: float = 1.2) -> None:
    """Espaça downloads seguidos ao mesmo servidor (Wikimedia e Openverse limitam rajadas)."""
    host = urlparse(url).netloc
    espera = intervalo - (time.time() - _ultimo_por_host.get(host, 0.0))
    if espera > 0:
        time.sleep(espera)
    _ultimo_por_host[host] = time.time()


def _baixar(url: str, destino: Path, max_mb: int = 250, cabecalhos: dict | None = None, timeout: int = 60) -> Path | None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".parcial")
    host = urlparse(url).netloc
    if _bloqueado_ate.get(host, 0) > time.time():
        return None  # servidor pediu pausa longa: pula sem esperar (a busca tenta outras fontes)
    for tentativa in range(3):
        _espacar(url)
        try:
            with requests.get(url, stream=True, timeout=timeout,
                              headers={"User-Agent": agente_http(), **(cabecalhos or {})}) as r:
                if r.status_code == 429:
                    pedido = float(r.headers.get("Retry-After", "") or 5 * (tentativa + 1))
                    if pedido > 60 or tentativa == 2:
                        # pausa longa (ex.: Wikimedia pede 10 min): bloqueia o servidor nesta execução
                        _bloqueado_ate[host] = time.time() + max(pedido, 60)
                        log.aviso(f"{host} limitou os downloads por {pedido / 60:.0f} min; usando outras fontes.")
                        return None
                    log.debug(f"{host} pediu para aguardar {pedido:.0f}s (HTTP 429).")
                    time.sleep(pedido)
                    continue
                if r.status_code >= 400:
                    log.debug(f"Download recusado ({r.status_code}): {url}")
                    return None
                total = 0
                with open(temporario, "wb") as f:
                    for pedaco in r.iter_content(1 << 16):
                        total += len(pedaco)
                        if total > max_mb * 1024 * 1024:
                            raise ValueError("arquivo grande demais")
                        f.write(pedaco)
            temporario.replace(destino)
            return destino
        except Exception as exc:
            log.debug(f"Download falhou ({url}): {exc}")
            temporario.unlink(missing_ok=True)
            return None
    return None


def _remover_faixa_inferior(caminho: Path, fracao: float) -> None:
    """Corta a faixa de baixo da imagem (onde ficam marcas d'água de serviços gratuitos)."""
    try:
        from PIL import Image

        with Image.open(caminho) as im:
            largura, altura = im.size
            recortada = im.convert("RGB").crop((0, 0, largura, int(altura * (1 - fracao))))
        recortada.save(caminho, quality=95)
    except Exception as exc:
        log.debug(f"Não foi possível recortar {caminho.name}: {exc}")


def _imagem_valida(caminho: Path, minimo: int = 640) -> bool:
    try:
        from PIL import Image

        with Image.open(caminho) as im:
            im.verify()
        with Image.open(caminho) as im:
            return min(im.size) >= minimo * 0.5 and max(im.size) >= minimo
    except Exception:
        return False


# --------------------------------------------------------------------- vídeo
def _pexels_videos(termo: str) -> list[dict]:
    chave = env("PEXELS_API_KEY")
    if not chave:
        return []
    try:
        dados = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": chave},
                             params={"query": termo, "orientation": "landscape", "per_page": 15,
                                     "locale": "pt-BR" if _idioma(termo) == "pt" else "en-US"}, timeout=20).json()
    except (requests.RequestException, ValueError) as exc:
        log.aviso(f"Pexels indisponível: {exc}")
        return []
    itens = []
    for v in dados.get("videos", []):
        arquivos = [a for a in v.get("video_files", []) if a.get("width") and a.get("file_type") == "video/mp4"]
        arquivos = [a for a in arquivos if a["width"] >= 1280] or arquivos
        if not arquivos:
            continue
        melhor = min(arquivos, key=lambda a: abs(a["width"] - 1920))
        itens.append({"id": f"pexels-v-{v['id']}", "url": melhor["link"], "duracao": v.get("duration", 0),
                      "credito": {"tipo": "Vídeo", "autor": v.get("user", {}).get("name", ""), "fonte": "Pexels",
                                  "url": v.get("url", "")}})
    return itens


def _pixabay_videos(termo: str) -> list[dict]:
    chave = env("PIXABAY_API_KEY")
    if not chave:
        return []
    try:
        dados = requests.get("https://pixabay.com/api/videos/", params={
            "key": chave, "q": termo[:100], "per_page": 15, "safesearch": "true",
            "lang": "pt" if _idioma(termo) == "pt" else "en"}, timeout=20).json()
    except (requests.RequestException, ValueError) as exc:
        log.aviso(f"Pixabay indisponível: {exc}")
        return []
    itens = []
    for v in dados.get("hits", []):
        versoes = v.get("videos", {})
        escolha = next((versoes[k] for k in ("large", "medium", "small") if versoes.get(k, {}).get("url")), None)
        if not escolha:
            continue
        itens.append({"id": f"pixabay-v-{v['id']}", "url": escolha["url"], "duracao": v.get("duration", 0),
                      "credito": {"tipo": "Vídeo", "autor": v.get("user", ""), "fonte": "Pixabay",
                                  "url": v.get("pageURL", "")}})
    return itens


def _desativada() -> bool:
    return env("MIDIA_DESATIVADA") == "1"


def buscar_video(termo: str, destino_base: Path, variante: int = 0, usados: set | None = None,
                 contexto: str = "") -> Midia | None:
    if _desativada() or not (env("PEXELS_API_KEY") or env("PIXABAY_API_KEY")):
        return None
    usados = usados or set()
    candidatos: list[dict] = []
    for consulta in variantes_busca(termo, contexto):
        candidatos = _pexels_videos(consulta) or _pixabay_videos(consulta)
        if candidatos:
            break
    livres = [c for c in candidatos if c["id"] not in usados] or candidatos
    if not livres:
        return None
    for item in livres[variante % len(livres):] + livres[: variante % len(livres)]:
        arquivo = _baixar(item["url"], destino_base.with_suffix(".mp4"))
        if arquivo:
            return Midia(arquivo, "video", item["credito"], item["id"])
    return None


# --------------------------------------------------------------------- fotos
def _pexels_fotos(termo: str) -> list[dict]:
    chave = env("PEXELS_API_KEY")
    if not chave:
        return []
    try:
        dados = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": chave},
                             params={"query": termo, "orientation": "landscape", "per_page": 15,
                                     "locale": "pt-BR" if _idioma(termo) == "pt" else "en-US"}, timeout=20).json()
    except (requests.RequestException, ValueError):
        return []
    return [{"id": f"pexels-f-{f['id']}", "url": f["src"].get("large2x") or f["src"]["original"],
             "credito": {"tipo": "Foto", "autor": f.get("photographer", ""), "fonte": "Pexels", "url": f.get("url", "")}}
            for f in dados.get("photos", [])]


def _pixabay_fotos(termo: str) -> list[dict]:
    chave = env("PIXABAY_API_KEY")
    if not chave:
        return []
    try:
        dados = requests.get("https://pixabay.com/api/", params={
            "key": chave, "q": termo[:100], "image_type": "photo", "orientation": "horizontal", "per_page": 15,
            "safesearch": "true", "lang": "pt" if _idioma(termo) == "pt" else "en"}, timeout=20).json()
    except (requests.RequestException, ValueError):
        return []
    return [{"id": f"pixabay-f-{f['id']}", "url": f.get("largeImageURL") or f.get("webformatURL"),
             "credito": {"tipo": "Foto", "autor": f.get("user", ""), "fonte": "Pixabay", "url": f.get("pageURL", "")}}
            for f in dados.get("hits", []) if f.get("largeImageURL") or f.get("webformatURL")]


def _openverse(termo: str) -> list[dict]:
    """Imagens Creative Commons sem chave (limite anônimo: ~20 consultas/min e 200/dia)."""
    url = "https://api.openverse.org/v1/images/"
    parametros = {"q": termo[:200], "page_size": 15, "license_type": "commercial,modification",
                  "aspect_ratio": "wide", "size": "large", "mature": "false"}
    if _bloqueado_ate.get("upload.wikimedia.org", 0) > time.time():
        parametros["excluded_source"] = "wikimedia"  # Wikimedia pediu pausa: busca em outras fontes
    dados: dict = {}
    for tentativa in range(2):
        _espacar(url, 3.1)  # fica abaixo de 20 consultas por minuto
        try:
            resposta = requests.get(url, headers={"User-Agent": agente_http()}, params=parametros, timeout=25)
        except requests.RequestException as exc:
            log.debug(f"Openverse indisponível: {exc}")
            return []
        if resposta.status_code == 429 and tentativa == 0:
            espera = min(60.0, float(resposta.headers.get("Retry-After", "") or 20))
            log.info(f"Openverse pediu para aguardar {espera:.0f}s (limite de consultas).")
            time.sleep(espera)
            continue
        if resposta.status_code == 429:
            log.aviso("Limite diário/por minuto do Openverse atingido; algumas cenas usarão ilustração 2D.")
            return []
        try:
            dados = resposta.json()
        except ValueError:
            return []
        break
    itens = []
    for f in dados.get("results", []):
        if not f.get("url"):
            continue
        licenca = f"{(f.get('license') or '').upper()} {f.get('license_version') or ''}".strip()
        itens.append({"id": f"openverse-{f.get('id')}", "url": f["url"],
                      "credito": {"tipo": "Imagem", "autor": f.get("creator") or "", "fonte": f.get("source") or "Openverse",
                                  "licenca": licenca, "url": f.get("foreign_landing_url") or f["url"]}})
    return itens


def buscar_imagem(termo: str, destino_base: Path, variante: int = 0, usados: set | None = None,
                  contexto: str = "") -> Midia | None:
    if _desativada():
        return None
    usados = usados or set()
    variantes = variantes_busca(termo, contexto)
    for provedor in (_pexels_fotos, _pixabay_fotos, _openverse):
        candidatos: list[dict] = []
        for consulta in variantes:
            candidatos = provedor(consulta)
            if candidatos:
                break
        livres = [c for c in candidatos if c["id"] not in usados] or candidatos
        if not livres:
            continue
        ordem = livres[variante % len(livres):] + livres[: variante % len(livres)]
        for item in ordem[:5]:
            arquivo = _baixar(item["url"], destino_base.with_suffix(".jpg"), max_mb=40)
            if arquivo and _imagem_valida(arquivo):
                return Midia(arquivo, "imagem", item["credito"], item["id"])
    return None


# ----------------------------------------------------------- imagem por IA
def gerar_imagem_ia(prompt: str, destino_base: Path, semente: int = 1, largura: int = 1920,
                    altura: int = 1080) -> Midia | None:
    provedor = env("IMAGEM_IA_PROVEDOR", "pollinations").lower()
    destino = destino_base.with_suffix(".jpg")
    if provedor in ("nenhum", "off", "none") or _desativada():
        return None
    if provedor == "comando":
        modelo = env("IMAGEM_IA_COMANDO")
        if not modelo:
            log.aviso("IMAGEM_IA_PROVEDOR=comando, mas IMAGEM_IA_COMANDO está vazio.")
            return None
        comando = modelo.format(prompt=prompt.replace('"', "'"), saida=str(destino), largura=largura, altura=altura,
                                semente=semente)
        try:
            subprocess.run(shlex.split(comando, posix=platform.system() != "Windows"), check=True, timeout=900)
        except Exception as exc:
            log.aviso(f"Gerador local de imagens falhou: {exc}")
            return None
        if destino.exists() and _imagem_valida(destino):
            return Midia(destino, "imagem", {"tipo": "Imagem", "autor": "gerada por IA (local)", "fonte": "IA"}, "")
        return None
    # Pollinations (gratuito)
    estilo = ", cinematic, dramatic lighting, highly detailed, 16:9, no text, no watermark"
    url = env("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/{prompt}?width={largura}&height={altura}"
              "&seed={semente}&nologo=true&model=flux").format(
        prompt=quote((prompt + estilo)[:900]), largura=largura, altura=altura, semente=semente)
    global _ultimo_pollinations
    cabecalhos = {"User-Agent": agente_http()}
    token = env("POLLINATIONS_TOKEN")
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    # Camada gratuita: 1 imagem a cada ~45 s por IP. Sem token, espaça as chamadas para não levar HTTP 402.
    intervalo = 0.0 if token else float(env("POLLINATIONS_INTERVALO_SEG", "46") or 46)
    for tentativa in range(3):
        espera = intervalo - (time.time() - _ultimo_pollinations)
        if espera > 0:
            log.info(f"Aguardando {espera:.0f}s pelo limite gratuito do Pollinations "
                     "(defina POLLINATIONS_TOKEN no .env para gerar sem espera)…")
            time.sleep(espera)
        try:
            resposta = requests.get(url, headers=cabecalhos, timeout=180)
        except requests.RequestException as exc:
            log.debug(f"Pollinations falhou: {exc}")
            resposta = None
        _ultimo_pollinations = time.time()
        if resposta is not None and resposta.status_code == 402:
            m = re.search(r"[Rr]etry after (\d+)", resposta.text)
            espera_extra = int(m.group(1)) + 2 if m else intervalo
            log.info(f"Pollinations pediu para aguardar {espera_extra}s (limite gratuito).")
            _ultimo_pollinations = time.time() - intervalo + espera_extra
            continue
        if resposta is not None and resposta.ok and resposta.headers.get("Content-Type", "").startswith("image/"):
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(resposta.content)
            if not token:
                _remover_faixa_inferior(destino, 0.09)  # a camada gratuita carimba "pollinations.ai" no rodapé
            if _imagem_valida(destino):
                return Midia(destino, "imagem", {"tipo": "Imagem", "autor": "gerada por IA", "fonte": "Pollinations.ai"},
                             f"ia-{semente}")
        elif resposta is not None:
            log.debug(f"Pollinations respondeu {resposta.status_code}: {resposta.text[:200]}")
    log.aviso("Geração de imagem por IA indisponível no momento; usando alternativa.")
    return None
