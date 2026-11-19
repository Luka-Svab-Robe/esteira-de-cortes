"""Copiloto de IA do painel: sugere gancho, título, descrição e tags de um Short a partir da fala do corte.

Ordem dos motores: a IA que o usuário conectou na Conta (Claude ou endpoint compatível com OpenAI) →
a IA da plataforma (.env) → Ollama local → regras simples (sempre funciona, sem custo).
"""
from __future__ import annotations

import json
import re

import requests

from esteira import util
from esteira.config import env
from esteira.llm import PROVEDORES_COMPATIVEIS, ClienteLLM, ErroLLM, obter_cliente
from painel import contas

CAMPOS = {
    "gancho": "o GANCHO: frase curta (máx. 6 palavras, máx. 50 letras) que aparece grande no topo do Short. "
              "Use *palavra* para destacar UMA palavra",
    "titulo": "o TÍTULO do Short no YouTube (máx. 80 letras), curioso e fiel ao que acontece no trecho",
    "descricao": "a DESCRIÇÃO do Short (2 ou 3 frases curtas) contando o que acontece, sem inventar nada",
    "tags": "uma lista de 8 a 12 TAGS em minúsculas, separadas por vírgula",
}
LIMITE_CAMPO = {"gancho": 60, "titulo": 90, "descricao": 900, "tags": 400}
QUANTIDADE = 3
TEMPO_OLLAMA = 75
FALA_MAX_IA, FALA_MAX_LOCAL = 2500, 700  # a IA local (CPU) fica rápida com pedido curto
OPCOES_OLLAMA = {"temperature": 0.7, "num_predict": 180, "num_ctx": 2048}
ESQUEMA = {"type": "object", "properties": {"sugestoes": {"type": "array", "items": {"type": "string"}}},
           "required": ["sugestoes"], "additionalProperties": False}
SISTEMA = ("Você é o redator de uma equipe de cortes de lives para YouTube Shorts, em português do Brasil. "
           "Escreva como gente fala, com energia, sem clickbait mentiroso. Baseie-se só na fala do trecho e no "
           "título da live: não invente fatos, nomes, números nem resultados que não estejam ali. Escreva sempre "
           "em português do Brasil, mesmo quando a fala for em outra língua (traduza a ideia).")


def _cliente_do_usuario(usuario_id: int) -> ClienteLLM | None:
    valores = contas.conexoes_do_usuario(usuario_id)
    if valores.get("ANTHROPIC_API_KEY"):
        cliente = ClienteLLM("anthropic", valores.get("ANTHROPIC_MODELO") or "claude-opus-5-5",
                             chave=valores["ANTHROPIC_API_KEY"])
    elif valores.get("OPENAI_API_KEY") or valores.get("OPENAI_BASE_URL"):
        _, _, url_padrao, _, modelo_padrao = PROVEDORES_COMPATIVEIS["openai"]
        cliente = ClienteLLM("openai", valores.get("OPENAI_MODELO") or modelo_padrao,
                             valores.get("OPENAI_BASE_URL") or url_padrao, valores.get("OPENAI_API_KEY"))
    else:
        cliente = obter_cliente()
        if cliente is None or cliente.provedor == "ollama":  # o Ollama vai pelo caminho próprio (sem "pensar")
            return None
    cliente.esforco = "low"  # sugestões curtas: rápido e barato
    return cliente


def _pedido(campo: str, ctx: dict, fala_max: int = FALA_MAX_IA) -> str:
    partes = [f"Escreva {QUANTIDADE} opções diferentes para {CAMPOS[campo]}."]
    if ctx.get("canal"):
        partes.append(f"Canal: {ctx['canal']}" + (f" (nicho: {ctx['nicho']})" if ctx.get("nicho") else ""))
    if ctx.get("titulo_live"):
        partes.append(f"Título da live: {ctx['titulo_live']}")
    if ctx.get("fala"):
        partes.append(f"Fala do trecho (transcrição automática, pode ter erros):\n\"\"\"{ctx['fala'][:fala_max]}\"\"\"")
    for chave, rotulo in (("gancho", "Gancho atual"), ("titulo", "Título atual")):
        if ctx.get(chave) and chave != campo:
            partes.append(f"{rotulo}: {ctx[chave]}")
    if ctx.get("atual"):
        partes.append(f"Versão atual de {campo} (melhore ou proponha alternativas): {ctx['atual']}")
    partes.append('Responda só JSON: {"sugestoes": ["...", "...", "..."]}')
    return "\n\n".join(partes)


def _via_ollama(campo: str, ctx: dict, usuario_id: int) -> list[str] | None:
    from esteira.cortes.traducao import _modelo_ollama

    valores = contas.conexoes_do_usuario(usuario_id)
    modelo = valores.get("TRADUCAO_MODELO") or _modelo_ollama()
    if not modelo:
        return None
    raiz = (valores.get("OLLAMA_URL") or env("OLLAMA_URL", "http://localhost:11434/v1")).rstrip("/").removesuffix("/v1")
    try:
        resposta = requests.post(raiz + "/api/chat", timeout=TEMPO_OLLAMA, allow_redirects=False, json={
            "model": modelo, "stream": False, "think": False, "format": "json", "keep_alive": "15m",
            "options": OPCOES_OLLAMA,
            "messages": [{"role": "system", "content": SISTEMA},
                         {"role": "user", "content": _pedido(campo, ctx, FALA_MAX_LOCAL)}]})
        resposta.raise_for_status()
        return list(json.loads(resposta.json()["message"]["content"]).get("sugestoes", []))
    except (requests.RequestException, ValueError, KeyError, AttributeError):
        return None


def _por_regras(campo: str, ctx: dict) -> list[str]:
    """Sem IA disponível: tira opções direto da fala do trecho."""
    fala = re.sub(r"\s+", " ", ctx.get("fala") or "").strip()
    frases = [f for f in util.dividir_frases(fala) if len(f.split()) >= 3] or ([fala] if fala else [])
    base = frases or [ctx.get("titulo_live") or "Momento da live"]
    if campo == "gancho":
        return [" ".join(f.split()[:6]).rstrip(",.;:!?") for f in base[:QUANTIDADE]]
    if campo == "titulo":
        return [util.limitar(f, 80, "") for f in base[:QUANTIDADE]]
    if campo == "descricao":
        texto = util.limitar(" ".join(base[:3]), 260)
        live = ctx.get("titulo_live")
        return [texto, f"{texto}\n\nTrecho da live: {live}" if live else texto, util.limitar(base[0], 160)]
    chaves = util.palavras_chave(" ".join([fala, ctx.get("titulo_live", "")]), 12)
    return [", ".join(chaves), ", ".join(chaves[:8] + ["cortes", "shorts"]), ", ".join(["cortes de live"] + chaves[:9])]


def _limpar(campo: str, sugestoes: list) -> list[str]:
    limpas = []
    for s in sugestoes:
        if isinstance(s, list):
            s = ", ".join(str(x) for x in s)
        texto = re.sub(r"\s+", " ", str(s)).strip().strip('"') if campo != "descricao" else str(s).strip()
        if campo == "tags":
            texto = ", ".join(t.strip().lstrip("#").lower() for t in texto.split(",") if t.strip())
        texto = texto[:LIMITE_CAMPO[campo]]
        if texto and texto not in limpas:
            limpas.append(texto)
    return limpas[:QUANTIDADE]


def sugerir(usuario_id: int, campo: str, ctx: dict) -> dict:
    if campo not in CAMPOS:
        raise ValueError("Campo desconhecido.")
    pedido = _pedido(campo, ctx)
    cliente = _cliente_do_usuario(usuario_id)
    if cliente is not None:
        try:
            sugestoes = _limpar(campo, cliente.gerar_json(SISTEMA, pedido, ESQUEMA, max_tokens=4000).get("sugestoes", []))
            if sugestoes:
                return {"sugestoes": sugestoes, "motor": "Claude" if cliente.provedor == "anthropic" else cliente.provedor}
        except ErroLLM:
            pass
    sugestoes = _limpar(campo, _via_ollama(campo, ctx, usuario_id) or [])
    if sugestoes:
        return {"sugestoes": sugestoes, "motor": "IA local"}
    return {"sugestoes": _limpar(campo, _por_regras(campo, ctx)), "motor": "regras"}
