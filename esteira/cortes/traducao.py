"""Tradução para português do Brasil do que é falado nos cortes (legenda, gancho, título, descrição).

Ordem dos tradutores: a IA configurada no .env (Anthropic, OpenAI-compatível…) → o Ollama local com o maior
modelo instalado (grátis, sem internet) → nenhum (mantém o original e o editor avisa).
A legenda continua palavra por palavra: cada frase é traduzida inteira e as palavras traduzidas são
distribuídas no tempo em que a frase original foi falada.
"""
from __future__ import annotations

import json
import re

import requests

from .. import log
from ..config import env

IDIOMAS = {"en": "inglês", "es": "espanhol", "fr": "francês", "de": "alemão", "it": "italiano", "ja": "japonês",
           "ko": "coreano", "zh": "chinês", "ru": "russo", "ar": "árabe", "hi": "hindi", "pt": "português"}
_cache_modelo: dict[str, str | None] = {}


def nome_idioma(codigo: str | None) -> str:
    return IDIOMAS.get((codigo or "")[:2], codigo or "outra língua")


def precisa_traduzir(idioma: str | None, destino: str = "pt") -> bool:
    return bool(idioma) and idioma[:2].lower() != destino[:2].lower()


def _modelo_ollama() -> str | None:
    """Maior modelo de texto instalado no Ollama (ou TRADUCAO_MODELO do .env)."""
    if "modelo" in _cache_modelo:
        return _cache_modelo["modelo"]
    escolhido = env("TRADUCAO_MODELO") or None
    if not escolhido:
        from ..llm import modelos_ollama

        instalados = [m for m in (modelos_ollama() or []) if not re.search(r"embed|whisper|vision|llava", m, re.I)]

        def tamanho(nome: str) -> float:
            m = re.search(r"(\d+(?:\.\d+)?)b\b", nome.lower())
            return float(m.group(1)) if m else 0.0

        candidatos = sorted(instalados, key=tamanho, reverse=True)
        escolhido = candidatos[0] if candidatos and tamanho(candidatos[0]) >= 1.5 else None
    _cache_modelo["modelo"] = escolhido
    return escolhido


def _pedido(linhas: list[str], origem: str, contexto: str) -> str:
    return ("Traduza cada linha do " + nome_idioma(origem) + " para português do Brasil, de forma natural e falada, "
            "como legenda de vídeo" + (f" ({contexto})" if contexto else "") + ". Mantenha nomes próprios, nomes de "
            "personagens e termos de jogo conhecidos. Não explique nada. Responda só JSON no formato "
            '{"t": ["...", ...]} com exatamente ' + str(len(linhas)) + " itens, na mesma ordem.\n\n"
            + "\n".join(f"{i + 1}. {l}" for i, l in enumerate(linhas)))


def _via_llm(linhas: list[str], origem: str, contexto: str) -> list[str] | None:
    from ..llm import ErroLLM, obter_cliente

    cliente = obter_cliente()
    if not cliente:
        return None
    esquema = {"type": "object", "properties": {"t": {"type": "array", "items": {"type": "string"}}},
               "required": ["t"], "additionalProperties": False}
    try:
        return list(cliente.gerar_json("Você é um tradutor de legendas.", _pedido(linhas, origem, contexto),
                                       esquema, max_tokens=4000).get("t", []))
    except ErroLLM as exc:
        log.aviso(f"Tradução pela IA falhou ({exc}); tentando o Ollama local.")
        return None


def _via_ollama(linhas: list[str], origem: str, contexto: str) -> list[str] | None:
    modelo = _modelo_ollama()
    if not modelo:
        return None
    raiz = env("OLLAMA_URL", "http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
    try:
        resposta = requests.post(raiz + "/api/chat", timeout=600, json={
            "model": modelo, "stream": False, "think": False, "format": "json", "keep_alive": "3m",
            "options": {"temperature": 0.2}, "messages": [{"role": "user", "content": _pedido(linhas, origem, contexto)}]})
        resposta.raise_for_status()
        return list(json.loads(resposta.json()["message"]["content"]).get("t", []))
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.aviso(f"Tradução pelo Ollama ({modelo}) falhou: {exc}")
        return None


def traduzir(linhas: list[str], origem: str, contexto: str = "") -> list[str] | None:
    """Traduz uma lista de frases. Devolve None se não houver tradutor ou se a resposta vier quebrada."""
    if not linhas:
        return []
    for tentativa in (_via_llm, _via_ollama):
        for _ in range(2):
            saida = tentativa(linhas, origem, contexto)
            if saida is None:
                break
            saida = [str(s).strip() for s in saida]
            if len(saida) == len(linhas) and all(saida):
                return saida
            log.debug(f"Tradução veio com {len(saida)} de {len(linhas)} linhas; tentando de novo.")
    return None


def disponivel() -> str | None:
    """Descrição do tradutor que será usado (ou None)."""
    from ..llm import obter_cliente

    cliente = obter_cliente()
    if cliente:
        return cliente.descricao
    modelo = _modelo_ollama()
    return f"Ollama local ({modelo})" if modelo else None


# ------------------------------------------------------------------ legenda palavra por palavra
def frases_da_fala(palavras: list[dict], max_palavras: int = 9, pausa: float = 0.6) -> list[dict]:
    """Agrupa as palavras do Whisper em frases curtas (fim de frase, pausa longa ou limite de palavras)."""
    frases, atual = [], []
    for i, p in enumerate(palavras):
        atual.append(p)
        proxima = palavras[i + 1] if i + 1 < len(palavras) else None
        fim_de_frase = re.search(r"[.!?…]$", p["palavra"])
        pausa_longa = proxima is not None and proxima["inicio"] - p["fim"] > pausa
        if fim_de_frase or pausa_longa or len(atual) >= max_palavras or proxima is None:
            frases.append({"inicio": atual[0]["inicio"], "fim": atual[-1]["fim"],
                           "texto": " ".join(x["palavra"] for x in atual)})
            atual = []
    return frases


def palavras_traduzidas(palavras: list[dict], origem: str, contexto: str = "") -> list[dict] | None:
    """Mesma estrutura de palavras ({palavra, inicio, fim}), mas em português, no ritmo da fala original."""
    frases = frases_da_fala(palavras)
    traduzidas = traduzir([f["texto"] for f in frases], origem, contexto)
    if traduzidas is None:
        return None
    saida = []
    for frase, texto in zip(frases, traduzidas):
        saida.extend(distribuir_palavras(frase["inicio"], frase["fim"], texto))
    return saida


def distribuir_palavras(inicio: float, fim: float, texto: str) -> list[dict]:
    """Espalha as palavras de uma frase dentro do tempo dela (palavras maiores ficam mais tempo na tela)."""
    termos = texto.split()
    if not termos:
        return []
    pesos = [len(t) + 2 for t in termos]
    total, duracao, acumulado = sum(pesos), max(0.3, fim - inicio), 0.0
    saida = []
    for termo, peso in zip(termos, pesos):
        comeco = inicio + duracao * acumulado / total
        acumulado += peso
        saida.append({"palavra": termo, "inicio": round(comeco, 3), "fim": round(inicio + duracao * acumulado / total, 3)})
    return saida
