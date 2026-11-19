"""Sessão e segurança do painel: quem está logado (ContextVar por requisição), proteção das rotas e limite de login."""
from __future__ import annotations

import time
from contextvars import ContextVar

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from painel import contas

COOKIE = "sessao"
USUARIO: ContextVar[dict | None] = ContextVar("usuario", default=None)
LIVRES = {"/", "/api/sessao", "/api/login", "/api/setup", "/api/saude", "/favicon.ico", "/manifest.webmanifest", "/sw.js"}
PREFIXOS_LIVRES = ("/static/", "/fontes/")
METODOS_ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}
_tentativas: dict[str, list[float]] = {}
MAX_CHAVES_TENTATIVAS, JANELA_MAXIMA = 5000, 3600


def usuario_atual() -> dict:
    usuario = USUARIO.get()
    if not usuario:
        raise HTTPException(401, {"codigo": "sessao", "mensagem": "Entre na sua conta."})
    return usuario


def exigir_admin() -> dict:
    usuario = usuario_atual()
    if usuario["papel"] != "admin":
        raise HTTPException(403, {"mensagem": "Só o administrador pode fazer isso."})
    return usuario


def registrar_tentativa(chave: str, limite: int = 8, janela: float = 600,
                        mensagem: str = "Muitas tentativas. Espere alguns minutos e tente de novo.") -> None:
    """Limita tentativas (login por IP + e-mail, troca de senha, copiloto) para evitar abuso e adivinhação."""
    agora = time.time()
    if len(_tentativas) > MAX_CHAVES_TENTATIVAS:  # não deixa o contador crescer sem limite
        for velha in [k for k, v in _tentativas.items() if not v or agora - v[-1] > JANELA_MAXIMA]:
            _tentativas.pop(velha, None)
    lista = [t for t in _tentativas.get(chave, []) if agora - t < janela]
    if len(lista) >= limite:
        raise HTTPException(429, {"mensagem": mensagem})
    lista.append(agora)
    _tentativas[chave] = lista


def limpar_tentativas(chave: str) -> None:
    _tentativas.pop(chave, None)


def gravar_cookie(resposta, token: str, request: Request) -> None:
    resposta.set_cookie(COOKIE, token, max_age=contas.SESSAO_DIAS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


async def middleware(request: Request, call_next):
    caminho = request.url.path
    usuario = contas.usuario_da_sessao(request.cookies.get(COOKIE))
    USUARIO.set(usuario)
    livre = caminho in LIVRES or caminho.startswith(PREFIXOS_LIVRES)
    if not livre and not usuario:
        return JSONResponse({"detail": {"codigo": "sessao", "mensagem": "Entre na sua conta."}}, status_code=401)
    # anti-CSRF: o painel sempre manda este cabeçalho; formulários de outros sites não conseguem mandar
    if request.method in METODOS_ESCRITA and request.headers.get("x-esteira") != "1":
        return JSONResponse({"detail": {"mensagem": "Requisição recusada."}}, status_code=403)
    resposta = await call_next(request)
    if caminho.startswith("/api/"):
        resposta.headers.setdefault("Cache-Control", "no-store")
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    return resposta
