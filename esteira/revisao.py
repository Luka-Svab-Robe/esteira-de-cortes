"""Interpreta o motivo de uma reprovação e sugere a etapa mínima a refazer."""
from __future__ import annotations

import re

from . import util

# Ordem do encadeamento: refazer uma etapa exige refazer as posteriores
# (o cache por bloco garante que só o que mudou seja realmente reprocessado).
CADEIA = ("roteiro", "narracao", "montagem", "shorts", "thumbnails")

PALAVRAS = {
    "metadados": ("titulo", "descricao", "tags", "tag ", "capitulo", "hashtag", "seo"),
    "thumbnails": ("capa", "thumbnail", "thumb", "miniatura"),
    "shorts": ("short", "vertical", "reels"),
    "narracao": ("narracao", "narrador", "voz", "pronuncia", "pronunci", "sotaque", "leu ", "falou", "sigla",
                 "dicção", "diccao", "gaguej", "velocidade da fala", "audio da fala"),
    "roteiro": ("roteiro", "texto da narracao", "informacao errada", "dado errado", "fato errado", "incorreto",
                "encurtar o video", "mais curto", "mais longo", "reescrever", "reescreva", "mudar o texto",
                "frase da narracao"),
    "montagem": ("imagem", "cena", "clipe", "video de fundo", "transicao", "logo", "marca d", "musica", "trilha",
                 "volume", "cor ", "cores", "destaque", "texto na tela", "grafico", "cartao", "animacao", "corte",
                 "foto", "visual"),
}


def _extrair_blocos(texto: str) -> list[int]:
    blocos: set[int] = set()
    for trecho in re.findall(r"(?:cena|bloco|trecho)s?\s*(?:n[ºo°.]?\s*)?((?:\d+\s*(?:,|e|a|-|até|ate)?\s*)+)", texto):
        intervalos = re.findall(r"(\d+)\s*(?:-|a|até|ate)\s*(\d+)", trecho)
        for ini, fim in intervalos:
            blocos.update(range(int(ini), int(fim) + 1))
        blocos.update(int(n) for n in re.findall(r"\d+", trecho))
    return sorted(b for b in blocos if 0 < b < 1000)


def interpretar_motivo(motivo: str) -> dict:
    texto = " " + util.sem_acentos(motivo.lower()) + " "
    etapas = [etapa for etapa, chaves in PALAVRAS.items() if any(util.sem_acentos(c) in texto for c in chaves)]
    if "thumbnails" in etapas and "montagem" in etapas and not re.search(r"\bcena|bloco|clipe|trilha|musica", texto):
        etapas.remove("montagem")  # "texto da capa" não é montagem
    blocos = _extrair_blocos(texto)
    if blocos and not etapas:
        etapas = ["montagem"]
    if not etapas:
        etapas = ["roteiro"]
    producao = [e for e in CADEIA if e in etapas]
    inicio = producao[0] if producao else None
    passos = []
    if "metadados" in etapas:
        passos.append("ajustar título/descrição/tags/capítulos em metadados.json (não precisa renderizar)")
    if inicio == "roteiro":
        passos.append("editar o texto dos blocos afetados em roteiro.json")
    if inicio == "narracao":
        passos.append("ajustar 'texto_fonetico' dos blocos afetados (ou 'voz.pronuncias' do canal)")
    if inicio == "montagem":
        passos.append("ajustar 'modo_visual', 'busca_visual', 'destaque', 'dados_cartao' ou incrementar "
                      "'midia_variante' dos blocos afetados em roteiro.json")
    if inicio == "thumbnails":
        passos.append("editar 'frases_thumbnail' em metadados.json (ou trocar o template) e refazer as capas")
    if inicio == "shorts":
        passos.append("ajustar 'potencial_short' dos blocos ou os títulos dos shorts e refazer os shorts")
    alvo = f" (blocos {', '.join(map(str, blocos))})" if blocos else ""
    comando = (f"python esteira.py continuar --projeto <projeto> --a-partir-de {inicio}" if inicio
               else "nenhuma renderização necessária")
    instrucao = "; ".join(passos) + f"{alvo}. Depois: {comando}"
    return {"etapas": etapas, "blocos": blocos, "etapa_inicial": inicio, "instrucao": instrucao}
