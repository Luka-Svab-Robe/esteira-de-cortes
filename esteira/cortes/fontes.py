"""Cadastro das fontes (canais de lives) que o canal de cortes pode usar.

Só lives de fontes cadastradas são processadas; o dono adiciona quantas quiser (aba Canais do painel ou
`esteira.py fontes adicionar`). O canal do YouTube conectado serve só para postar e não vira fonte sozinho.
Cada fonte é do tipo:
  - proprio:    lives do seu próprio canal (sem crédito a terceiros);
  - autorizado: canal de outra pessoa que permite cortes (crédito obrigatório na tela e na descrição).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import urlparse

import yaml

from .. import util
from ..canal import Canal

TIPOS = ("proprio", "autorizado")
# domínio -> plataforma (o host do link precisa ser um destes ou um subdomínio deles)
DOMINIOS = {"youtube.com": "youtube", "youtu.be": "youtube", "twitch.tv": "twitch", "kick.com": "kick"}
PLATAFORMAS_HLS = ("twitch", "kick")  # VODs em HLS: dá para baixar só o trecho de cada corte, rápido e em HD


@dataclass
class Fonte:
    nome: str
    url: str
    plataforma: str  # youtube | twitch | kick
    tipo: str = "autorizado"
    credito: str = ""
    autorizacao: str = ""
    monitorar: bool = True
    ids: list = field(default_factory=list)  # channel_id, @handle, login da Twitch/Kick

    @property
    def texto_credito(self) -> str:
        """Crédito na tela: lives de outros criadores sempre mostram de quem é (e onde achar, na Twitch/Kick)."""
        if self.credito or self.tipo != "autorizado":
            return self.credito
        if self.plataforma in PLATAFORMAS_HLS:
            return f"Live de {self.nome} · {endereco_curto(self.url)}"
        return f"Corte da live de {self.nome}"


def plataforma_da_url(url: str) -> str:
    """youtube | twitch | kick, olhando o domínio de verdade (um "?x=twitch.tv" no fim não engana).

    Links com usuário@ no endereço, barra invertida ou espaços são recusados: navegadores e Python discordam
    sobre qual é o domínio nesses casos (ex.: "https://evil.com\\@twitch.tv/…")."""
    texto = str(url).strip()
    partes = urlparse(texto)
    if "\\" in texto or any(c.isspace() for c in texto) or "@" in partes.netloc:
        raise ValueError(f"Plataforma não suportada: {url} (link com caracteres inválidos)")
    host = (partes.hostname or "").lower()
    for dominio, plataforma in DOMINIOS.items():
        if host == dominio or host.endswith("." + dominio):
            return plataforma
    raise ValueError(f"Plataforma não suportada: {url} (use links da Twitch, da Kick ou do YouTube)")


def endereco_curto(url: str) -> str:
    """twitch.tv/nome, kick.com/nome (sem https:// nem www.)."""
    partes = urlparse(str(url).strip())
    host = (partes.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return f"{host}{partes.path}".rstrip("/")


def _caminho(canal: Canal):
    return canal.pasta / "fontes.yaml"


def carregar(canal: Canal) -> list[Fonte]:
    arquivo = _caminho(canal)
    if not arquivo.exists():
        return []
    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    return [Fonte(**{k: v for k, v in f.items() if k in Fonte.__dataclass_fields__}) for f in dados.get("fontes", [])]


def salvar(canal: Canal, fontes: list[Fonte]) -> None:
    cabecalho = ("# Fontes autorizadas do canal de cortes (gerenciado por: python esteira.py fontes ...)\n"
                 "# tipo: proprio (lives suas) | autorizado (outro canal que permite cortes; crédito obrigatório)\n")
    corpo = yaml.safe_dump({"fontes": [asdict(f) for f in fontes]}, allow_unicode=True, sort_keys=False)
    util.salvar_texto(_caminho(canal), cabecalho + corpo)


def _normalizar_id(valor: str) -> str:
    return str(valor or "").strip().lower().lstrip("@")


def identificadores(info: dict) -> set[str]:
    """IDs do canal dono de um vídeo/live, a partir do JSON do yt-dlp."""
    candidatos = {info.get("channel_id"), info.get("uploader_id"), info.get("channel"), info.get("uploader"),
                  info.get("channel_url"), info.get("uploader_url")}
    ids = set()
    for c in candidatos:
        if not c:
            continue
        ids.add(_normalizar_id(c))
        m = re.search(r"(?:youtube\.com/(?:channel/|@)|twitch\.tv/|kick\.com/)([\w.-]+)", str(c))
        if m:
            ids.add(_normalizar_id(m.group(1)))
    return {i for i in ids if i}


def localizar(canal: Canal, info: dict) -> Fonte | None:
    ids = identificadores(info)
    for fonte in carregar(canal):
        if ids & {_normalizar_id(i) for i in fonte.ids}:
            return fonte
    return None


def adicionar(canal: Canal, url: str, tipo: str, nome: str = "", credito: str = "", autorizacao: str = "",
              monitorar: bool = True, info: dict | None = None) -> Fonte:
    """Cadastra um canal de lives. info = dados (yt-dlp) de uma live dele, quando já lidos: evita ler o canal de
    novo (a página de um canal da Kick fora do ar não abre no yt-dlp)."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de fonte inválido: use {' ou '.join(TIPOS)}")
    if tipo == "autorizado" and not autorizacao.strip():
        raise ValueError("Para fontes de terceiros, descreva a autorização (ex.: 'programa de cortes oficial, "
                         "regras em <link>' ou 'autorização por e-mail em 10/11/2026').")
    from . import ingestao

    plataforma = plataforma_da_url(url)
    info = info or ingestao.info_canal(url)
    nome = nome or info.get("channel") or info.get("uploader") or info.get("title") or url
    fonte = Fonte(nome=nome, url=url.rstrip("/"), plataforma=plataforma, tipo=tipo, credito=credito,
                  autorizacao=autorizacao.strip(), monitorar=monitorar, ids=sorted(identificadores(info)))
    if not fonte.ids:
        raise ValueError(f"Não consegui identificar o canal de {url}.")
    fontes = [f for f in carregar(canal) if not (set(f.ids) & set(fonte.ids))]
    fontes.append(fonte)
    salvar(canal, fontes)
    return fonte


def remover(canal: Canal, termo: str) -> bool:
    termo = _normalizar_id(termo)
    fontes = carregar(canal)
    restantes = [f for f in fontes if termo not in {_normalizar_id(f.nome), _normalizar_id(f.url)} | set(f.ids)]
    if len(restantes) == len(fontes):
        return False  # nada a tirar: não regrava o arquivo
    salvar(canal, restantes)
    return True


def _sem_prefixo(url: str) -> str:
    """twitch.tv/nome para https://www.twitch.tv/nome/, http://twitch.tv/nome etc. (mesmo canal)."""
    return re.sub(r"^https?://(www\.|m\.)?", "", _normalizar_id(str(url).strip())).rstrip("/")


def buscar(canal: Canal, url: str) -> Fonte | None:
    """A fonte cadastrada com este link (o mesmo que aparece na aba Canais; com ou sem www)."""
    alvo = _sem_prefixo(url)
    return next((f for f in carregar(canal) if _sem_prefixo(f.url) == alvo), None)


def definir_monitorar(canal: Canal, url: str, monitorar: bool) -> Fonte | None:
    """Liga/desliga a vigia automática de uma fonte. Devolve a fonte atualizada (None se o link não está cadastrado)."""
    alvo = buscar(canal, url)
    if alvo is None:
        return None
    atualizada = replace(alvo, monitorar=bool(monitorar))
    salvar(canal, [atualizada if f.url == alvo.url else f for f in carregar(canal)])
    return atualizada
