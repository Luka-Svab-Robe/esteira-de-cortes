"""Cliente de modelo de linguagem com saída JSON estruturada.

Provedores:
  - anthropic: SDK oficial (Claude), saída estruturada por JSON Schema.
  - openai / gemini / groq / openrouter / ollama: API compatível com
    /chat/completions (todos expõem esse formato).
Sem provedor disponível, a esteira usa o modo agente (o Claude Code ou
outro agente escreve o roteiro) ou o gerador heurístico.
"""
from __future__ import annotations

import json
import re

import requests

from . import log
from .config import env, env_bool


class ErroLLM(RuntimeError):
    pass


PROVEDORES_COMPATIVEIS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1", "OPENAI_MODELO", "gpt-4.1-mini"),
    "gemini": ("GEMINI_API_KEY", None, "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_MODELO",
               "gemini-2.5-flash"),
    "groq": ("GROQ_API_KEY", None, "https://api.groq.com/openai/v1", "GROQ_MODELO", "llama-3.3-70b-versatile"),
    "openrouter": ("OPENROUTER_API_KEY", None, "https://openrouter.ai/api/v1", "OPENROUTER_MODELO",
                   "meta-llama/llama-3.3-70b-instruct"),
    "ollama": (None, "OLLAMA_URL", "http://localhost:11434/v1", "OLLAMA_MODELO", "qwen2.5:7b"),
}


def extrair_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.S)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio >= 0 and fim > inicio:
            return json.loads(texto[inicio:fim + 1])
        raise


class ClienteLLM:
    def __init__(self, provedor: str, modelo: str, base_url: str | None = None, chave: str | None = None):
        self.provedor = provedor
        self.modelo = modelo
        self.base_url = base_url
        self.chave = chave
        self.esforco = env("LLM_ESFORCO", "medium")

    @property
    def descricao(self) -> str:
        return f"{self.provedor} ({self.modelo})"

    def gerar_json(self, sistema: str, usuario: str, esquema: dict, max_tokens: int = 32000) -> dict:
        if self.provedor == "anthropic":
            return self._anthropic(sistema, usuario, esquema, max_tokens)
        return self._compativel(sistema, usuario, esquema, max_tokens)

    # ----------------------------------------------------------- Anthropic
    def _anthropic(self, sistema: str, usuario: str, esquema: dict, max_tokens: int) -> dict:
        try:
            import anthropic
        except ImportError as exc:
            raise ErroLLM("Pacote 'anthropic' não instalado (rode python instalar.py).") from exc
        cliente = anthropic.Anthropic(api_key=self.chave, max_retries=3) if self.chave else anthropic.Anthropic(max_retries=3)
        parametros = dict(
            model=self.modelo, max_tokens=max_tokens, system=sistema,
            messages=[{"role": "user", "content": usuario}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.esforco, "format": {"type": "json_schema", "schema": esquema}},
        )
        try:
            mensagem = self._anthropic_stream(cliente, parametros)
        except anthropic.AuthenticationError as exc:
            raise ErroLLM("Credencial da Anthropic inválida — confira ANTHROPIC_API_KEY no .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ErroLLM(f"A chave da Anthropic não tem permissão para este modelo: {exc}") from exc
        except anthropic.NotFoundError as exc:
            raise ErroLLM(f"Modelo '{self.modelo}' não encontrado — confira ANTHROPIC_MODELO.") from exc
        except anthropic.RateLimitError as exc:
            raise ErroLLM("Limite de uso da API Anthropic atingido; tente novamente em alguns minutos.") from exc
        except anthropic.BadRequestError as exc:
            raise ErroLLM(f"Requisição recusada pela API Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ErroLLM(f"Erro da API Anthropic ({exc.status_code}); tente de novo mais tarde.") from exc
        except anthropic.APIConnectionError as exc:
            raise ErroLLM("Sem conexão com a API Anthropic.") from exc
        if mensagem.stop_reason == "refusal":
            raise ErroLLM("O modelo recusou gerar este conteúdo. Reformule o tema.")
        if mensagem.stop_reason == "max_tokens":
            raise ErroLLM("A resposta do modelo foi cortada (max_tokens). Reduza a duração alvo do vídeo.")
        texto = "".join(getattr(b, "text", "") for b in mensagem.content if getattr(b, "type", "") == "text")
        try:
            return extrair_json(texto)
        except json.JSONDecodeError as exc:
            raise ErroLLM(f"O modelo devolveu JSON inválido: {exc}") from exc

    @staticmethod
    def _anthropic_stream(cliente, parametros: dict):
        """Streaming evita timeouts em respostas longas. Tenta com fallback do servidor
        (recusa -> outro modelo) e degrada com elegância em SDKs mais antigos."""
        if env_bool("ANTHROPIC_FALLBACK", True):
            try:
                with cliente.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                  **parametros) as fluxo:
                    return fluxo.get_final_message()
            except TypeError:
                log.debug("SDK sem suporte a 'fallbacks'; seguindo sem fallback do servidor.")
        try:
            with cliente.messages.stream(**parametros) as fluxo:
                return fluxo.get_final_message()
        except TypeError:
            extras = {"thinking": parametros.pop("thinking"), "output_config": parametros.pop("output_config")}
            with cliente.messages.stream(**parametros, extra_body=extras) as fluxo:
                return fluxo.get_final_message()

    # ------------------------------------------------- compatíveis OpenAI
    def _compativel(self, sistema: str, usuario: str, esquema: dict, max_tokens: int) -> dict:
        url = (self.base_url or "").rstrip("/") + "/chat/completions"
        instrucao = ("\n\nResponda SOMENTE com um objeto JSON válido (sem markdown) que siga exatamente este "
                     "JSON Schema:\n" + json.dumps(esquema, ensure_ascii=False))
        corpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema + instrucao}, {"role": "user", "content": usuario}],
            "response_format": {"type": "json_object"},
            "max_tokens": min(max_tokens, 16000),
        }
        cabecalhos = {"Content-Type": "application/json"}
        if self.chave:
            cabecalhos["Authorization"] = f"Bearer {self.chave}"
        for tentativa in range(2):
            try:
                resposta = requests.post(url, json=corpo, headers=cabecalhos, timeout=900)
            except requests.RequestException as exc:
                raise ErroLLM(f"Sem conexão com {self.provedor}: {exc}") from exc
            if resposta.status_code == 400 and "response_format" in resposta.text and tentativa == 0:
                corpo.pop("response_format", None)
                continue
            if resposta.status_code >= 400:
                raise ErroLLM(f"{self.provedor} respondeu {resposta.status_code}: {resposta.text[:300]}")
            try:
                conteudo = resposta.json()["choices"][0]["message"]["content"]
                return extrair_json(conteudo)
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                raise ErroLLM(f"Resposta inválida de {self.provedor}: {exc}") from exc
        raise ErroLLM(f"{self.provedor} não aceitou a requisição.")


def modelos_ollama(base_url: str | None = None) -> list[str] | None:
    """Modelos instalados no Ollama local (None se o Ollama não estiver rodando)."""
    raiz = (base_url or env("OLLAMA_URL", "http://localhost:11434/v1")).rstrip("/").removesuffix("/v1")
    try:
        resposta = requests.get(raiz + "/api/tags", timeout=1.5)
        if not resposta.ok:
            return None
        return [m.get("name", "") for m in resposta.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return None


def _ollama_pronto(base_url: str, modelo: str) -> bool:
    """No modo automático, só usa o Ollama se o modelo configurado estiver instalado."""
    instalados = modelos_ollama(base_url)
    if not instalados:
        return False
    return modelo in instalados or f"{modelo}:latest" in instalados


def _cliente_para(provedor: str, exigir_chave: bool = True) -> ClienteLLM | None:
    if provedor == "anthropic":
        tem_credencial = bool(env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN"))
        if exigir_chave and not tem_credencial:
            return None
        return ClienteLLM("anthropic", env("ANTHROPIC_MODELO", "claude-opus-5-5"))
    if provedor in PROVEDORES_COMPATIVEIS:
        var_chave, var_url, url_padrao, var_modelo, modelo_padrao = PROVEDORES_COMPATIVEIS[provedor]
        chave = env(var_chave) if var_chave else None
        base = env(var_url, url_padrao) if var_url else url_padrao
        if provedor == "ollama":
            if exigir_chave and not _ollama_pronto(base, env(var_modelo, modelo_padrao)):
                return None
        elif exigir_chave and not chave:
            return None
        return ClienteLLM(provedor, env(var_modelo, modelo_padrao), base, chave)
    return None


def obter_cliente() -> ClienteLLM | None:
    """Devolve o cliente configurado ou None (modo agente / heurístico)."""
    preferido = env("LLM_PROVEDOR", "auto").lower()
    if preferido in ("nenhum", "none", "agente", "off"):
        return None
    if preferido != "auto":
        cliente = _cliente_para(preferido, exigir_chave=False)
        if cliente is None:
            log.aviso(f"Provedor de LLM '{preferido}' desconhecido; seguindo sem LLM.")
        return cliente
    for provedor in ("anthropic", "openai", "gemini", "groq", "openrouter", "ollama"):
        cliente = _cliente_para(provedor)
        if cliente:
            return cliente
    return None
