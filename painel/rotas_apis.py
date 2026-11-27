"""Aba APIs: o app do YouTube (Google Cloud) e a autorização de cada canal de cortes.

A esteira só agenda Shorts no YouTube depois de duas coisas:
  1. o app OAuth do Google, criado uma vez (ID e segredo do cliente), gravado em credenciais/client_secret.json;
  2. a autorização de cada canal, feita com o login da conta do YouTube dele (token em
     canais/<c>/credenciais/token_youtube.json). Não existe uma "API por canal".

O segredo nunca sai pela API: a resposta mostra só os últimos caracteres.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Body, HTTPException

from esteira import util
from esteira.canal import Canal
from painel import contas, sessao

router = APIRouter()

_ID_CLIENTE = re.compile(r"[\w-]{5,120}\.apps\.googleusercontent\.com")
_SEGREDO_CLIENTE = re.compile(r"[\w-]{10,200}")
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_CERTS_URI = "https://www.googleapis.com/oauth2/v1/certs"


def _m6():
    # importado sob demanda: o módulo puxa as bibliotecas do Google, que deixam o arranque do painel mais lento
    from esteira.modulos import m6_publicacao

    return m6_publicacao


def _mascarar(segredo: str) -> str:
    return "•" * 8 + segredo[-4:] if len(segredo) > 4 else "•" * 8


def _situacao_app() -> dict:
    caminho = _m6().caminho_segredo()
    erro = ""
    try:
        dados = util.ler_json(caminho, {}) or {}
    except ValueError:
        dados, erro = {}, "O arquivo do app está corrompido. Cole o ID e o segredo de novo."
    if not isinstance(dados, dict):
        dados, erro = {}, "O arquivo do app está em formato inesperado. Cole o ID e o segredo de novo."
    cliente = dados.get("installed") or dados.get("web") or {}
    id_cliente, segredo = str(cliente.get("client_id", "") or ""), str(cliente.get("client_secret", "") or "")
    configurado = bool(id_cliente and segredo) and not erro
    return {"configurado": configurado, "client_id": id_cliente if configurado else "",
            "segredo": _mascarar(segredo) if configurado else "", "erro": erro}


def _canais_do_usuario(usuario: dict) -> list[dict]:
    canais = []
    for canal_id in contas.canais_do_usuario(usuario["id"]):
        try:
            c = Canal.carregar(canal_id)
        except Exception:
            continue
        if c.editorial.get("tipo_canal", "dark") != "cortes":
            continue
        info = util.ler_json(c.pasta_credenciais / "canal_youtube.json", {}) or {}
        canais.append({"id": c.id, "nome": c.nome, "conectado": c.caminho_token_youtube.exists(),
                       "canal_youtube": info.get("titulo", ""), "handle": info.get("handle", "")})
    return canais


def _restringir(caminho) -> None:
    """Só o dono do computador lê o arquivo (ele dá acesso ao app do Google)."""
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass


@router.get("/api/apis/youtube")
def ver_apis_youtube():
    usuario = sessao.usuario_atual()
    app = _situacao_app()
    canais = _canais_do_usuario(usuario)
    return {"app": app, "canais": canais, "pode_editar_app": usuario["papel"] == "admin",
            "pronto": app["configurado"] and any(c["conectado"] for c in canais)}


@router.put("/api/apis/youtube/app")
def salvar_app_youtube(corpo: dict = Body(...)):
    """Grava o app do Google. Só o administrador: as credenciais valem para o computador inteiro."""
    sessao.exigir_admin()
    m6 = _m6()
    id_cliente = str(corpo.get("client_id", "") or "").strip()
    segredo = str(corpo.get("client_secret", "") or "").strip()
    if not _ID_CLIENTE.fullmatch(id_cliente):
        raise HTTPException(400, {"mensagem": "O ID do cliente termina em .apps.googleusercontent.com. "
                                              "Copie do Google Cloud (passo a passo em credenciais/LEIA-ME.md)."})
    if not _SEGREDO_CLIENTE.fullmatch(segredo):
        raise HTTPException(400, {"mensagem": "O segredo do cliente não parece válido. Copie de novo do Google Cloud."})
    conteudo = {"installed": {"client_id": id_cliente, "client_secret": segredo, "auth_uri": _AUTH_URI,
                              "token_uri": _TOKEN_URI, "auth_provider_x509_cert_url": _CERTS_URI,
                              "redirect_uris": ["http://localhost"]}}
    # confere o formato sem falar com o Google: usa o mesmo leitor que o login do canal usa
    from google_auth_oauthlib.flow import InstalledAppFlow

    try:
        InstalledAppFlow.from_client_config(conteudo, m6.ESCOPOS)
    except Exception:
        raise HTTPException(400, {"mensagem": "Não consegui ler essas credenciais. Confira o ID e o segredo."})
    caminho = m6.caminho_segredo()
    util.salvar_json(caminho, conteudo)
    _restringir(caminho)
    return _situacao_app()
