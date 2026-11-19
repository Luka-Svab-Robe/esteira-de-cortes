"""Hashtags, tags e convite fixos do canal de cortes: os mesmos em todos os Shorts (o canal fala de um assunto só).

Configuração (canais/<c>/identidade.yaml → cortes):
  hashtags_fixas:  linha de hashtags da descrição, igual em todos os Shorts. As 3 primeiras aparecem
                   em cima do título no YouTube, então a ordem importa.
  hashtags_titulo: as que entram no fim do título (padrão: só #shorts).
  tags_fixas:      palavras-chave (campo "tags" do YouTube), somadas às tags de cada corte.
  convite_link:    link que vai em todos os Shorts, com a apresentação de convite_texto ({link} = o link),
                   logo antes da linha de hashtags. Vazio = sem convite.
Sem hashtags_fixas o comportamento antigo continua: #shorts + 3 hashtags tiradas das tags do corte.
"""
from __future__ import annotations

import re

from .. import util

LIMITE_TITULO = 100
MAX_HASHTAGS = 15  # o YouTube ignora TODAS as hashtags de um vídeo com mais de 60; acima de 15 já soa a spam


def normalizar(hashtag: str) -> str:
    """'#Meu Jogo 2!' -> 'MeuJogo2' (o YouTube só aceita letras, números e _ numa hashtag)."""
    return re.sub(r"[^\wÀ-ÿ]", "", str(hashtag or "").strip().lstrip("#"))


def _unicas(valores: list | str, limite: int) -> list[str]:
    if isinstance(valores, str):  # "hashtags_fixas: MeuJogo, Gameplay" escrito sem colchetes no YAML
        valores = re.split(r"[,\s]+", valores)
    saida, vistas = [], set()
    for valor in valores or []:
        h = normalizar(valor)
        if h and h.lower() not in vistas:
            vistas.add(h.lower())
            saida.append(h)
    return saida[:limite]


def fixas(cfg: dict) -> list[str]:
    return _unicas(cfg.get("hashtags_fixas") or [], MAX_HASHTAGS)


def do_titulo(cfg: dict) -> list[str]:
    return _unicas(cfg.get("hashtags_titulo") or ["shorts"], 3)


def linha(cfg: dict, tags_corte: list | None = None) -> str:
    """Linha de hashtags do fim da descrição."""
    lista = fixas(cfg)
    if not lista:  # canal sem hashtags fixas: #shorts + 3 tiradas das tags do corte
        lista = _unicas(["shorts"] + [util.slugify(t, 25).replace("-", "") for t in (tags_corte or [])[:3]], 4)
    return " ".join(f"#{h}" for h in lista)


def _tem(texto: str, hashtag: str) -> bool:
    return re.search(rf"(?<![\w#])#{re.escape(hashtag)}(?!\w)", texto or "", re.I) is not None


def titulo(base: str, cfg: dict) -> str:
    """Título + hashtags do título (sem repetir as que já estão nele), dentro dos 100 caracteres do YouTube."""
    base = str(base or "").strip()
    faltando = [h for h in do_titulo(cfg) if not _tem(base, h)]
    sufixo = " ".join(f"#{h}" for h in faltando)
    if not sufixo:
        return util.limitar(base, LIMITE_TITULO, "")
    return f"{util.limitar(base, LIMITE_TITULO - len(sufixo) - 1, '')} {sufixo}".strip()


def tags(cfg: dict, tags_corte: list | None = None) -> list[str]:
    vistas, saida = set(), []
    for tag in list(cfg.get("tags_fixas") or []) + list(tags_corte or []):
        tag = str(tag).strip()
        if tag and tag.lower() not in vistas:
            vistas.add(tag.lower())
            saida.append(tag)
    return saida


CONVITE_PADRAO = "✂️ Quer fazer cortes como estes e postar no seu canal também? Comece por aqui: {link}"


def convite(cfg: dict) -> str:
    """Apresentação + link do convite (vazio se o canal não tiver convite_link)."""
    link = str(cfg.get("convite_link") or "").strip()
    if not link:
        return ""
    texto = str(cfg.get("convite_texto") or CONVITE_PADRAO).strip()
    return texto.replace("{link}", link) if "{link}" in texto else f"{texto} {link}"


def com_convite(descricao: str, cfg: dict) -> str:
    """Põe o convite antes da linha final de hashtags (ou no fim), se o link ainda não estiver na descrição."""
    texto = convite(cfg)
    link = str(cfg.get("convite_link") or "").strip()
    if not texto or link in (descricao or ""):
        return descricao
    blocos = [b for b in (descricao or "").rstrip().split("\n\n")]
    if blocos and blocos[-1].lstrip().startswith("#"):
        return "\n\n".join(blocos[:-1] + [texto, blocos[-1]]).strip()
    return f"{(descricao or '').rstrip()}\n\n{texto}".strip()


def finalizar(titulo_atual: str, descricao: str, tags_atuais: list, cfg: dict) -> tuple[str, str, list[str]]:
    """Garantia na hora de publicar: mesmo textos reescritos à mão saem com o convite, as hashtags e as tags
    fixas do canal."""
    novo_titulo = titulo(titulo_atual, cfg)
    faltando = [h for h in fixas(cfg) if not _tem(descricao, h)]
    nova_descricao = com_convite(descricao, cfg)
    if faltando:
        nova_descricao = f"{(nova_descricao or '').rstrip()}\n\n{' '.join('#' + h for h in faltando)}".strip()
    return novo_titulo, nova_descricao, tags(cfg, tags_atuais)
