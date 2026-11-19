"""Rotas de conta: primeiro acesso, entrar/sair, perfil e visibilidade, senha, conexões, usuários e novos canais."""
from __future__ import annotations

import re

import requests
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from esteira import util
from esteira.canal import Canal
from painel import contas, sessao

router = APIRouter()


def _eh_canal_de_cortes(canal_id: str) -> bool:
    try:
        return Canal.carregar(canal_id).editorial.get("tipo_canal", "dark") == "cortes"
    except Exception:
        return False


def _resposta_sessao(usuario: dict | None) -> dict:
    modo = contas.modo_acesso()
    if not usuario:
        return {"usuario": None, "precisa_setup": modo is None, "modo": modo}
    return {"usuario": {**usuario, "publico": contas.perfil_visivel(usuario)}, "precisa_setup": False, "modo": modo,
            "canais": contas.canais_do_usuario(usuario["id"])}


def _adotar_canais_existentes(usuario: dict) -> None:
    contas.adotar_canais_sem_dono(usuario["id"], [c for c in Canal.listar() if _eh_canal_de_cortes(c)])


@router.get("/api/sessao")
def ver_sessao():
    return _resposta_sessao(sessao.USUARIO.get())


@router.post("/api/setup")
def primeiro_acesso(request: Request, corpo: dict = Body(...)):
    """Primeiro acesso (só quando ainda não existe ninguém). modo "aberto": usar sem senha neste computador;
    senão cria o administrador com e-mail e senha. Os canais que já existiam passam a ser dessa conta."""
    if contas.existe_algum_usuario():
        raise HTTPException(409, {"mensagem": "A plataforma já foi configurada. Entre com sua conta."})
    if corpo.get("modo") == "aberto":
        if not sessao.acesso_local(request):
            raise HTTPException(400, {"mensagem": "O uso sem senha só pode ser escolhido neste próprio computador."})
        try:
            usuario = contas.criar_usuario_local(str(corpo.get("nome", "")))
        except contas.PlataformaJaConfigurada as exc:
            raise HTTPException(409, {"mensagem": str(exc)})
        _adotar_canais_existentes(usuario)
        return _resposta_sessao(usuario)
    try:
        usuario = contas.criar_usuario(corpo.get("email", ""), corpo.get("nome", ""), corpo.get("senha", ""), "admin",
                                       primeiro=True)
    except contas.PlataformaJaConfigurada as exc:
        raise HTTPException(409, {"mensagem": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"mensagem": str(exc)})
    _adotar_canais_existentes(usuario)
    resposta = JSONResponse(_resposta_sessao(usuario))
    sessao.gravar_cookie(resposta, contas.criar_sessao(usuario["id"], request.headers.get("user-agent", "")), request)
    return resposta


@router.post("/api/login")
def entrar(request: Request, corpo: dict = Body(...)):
    email = str(corpo.get("email", "")).strip().lower()
    chave = f"{request.client.host if request.client else '?'}|{email}"
    sessao.registrar_tentativa(chave)
    usuario = contas.autenticar(email, str(corpo.get("senha", "")))
    if not usuario:
        raise HTTPException(401, {"mensagem": "E-mail ou senha incorretos."})
    sessao.limpar_tentativas(chave)
    resposta = JSONResponse(_resposta_sessao(usuario))
    sessao.gravar_cookie(resposta, contas.criar_sessao(usuario["id"], request.headers.get("user-agent", "")), request)
    return resposta


@router.post("/api/logout")
def sair(request: Request):
    contas.encerrar_sessao(request.cookies.get(sessao.COOKIE))
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(sessao.COOKIE, path="/")
    return resposta


# ------------------------------------------------------------------ conta
@router.get("/api/conta")
def ver_conta():
    usuario = sessao.usuario_atual()
    return {**usuario, "publico": contas.perfil_visivel(usuario), "campos": list(contas.CAMPOS_PERFIL)}


@router.put("/api/conta")
def editar_conta(corpo: dict = Body(...)):
    usuario = sessao.usuario_atual()
    atualizado = contas.atualizar_conta(usuario["id"], corpo.get("nome"), corpo.get("perfil"), corpo.get("visivel"))
    return {**atualizado, "publico": contas.perfil_visivel(atualizado)}


@router.post("/api/conta/senha")
def mudar_senha(request: Request, corpo: dict = Body(...)):
    usuario = sessao.usuario_atual()
    sessao.registrar_tentativa(f"senha|{usuario['id']}", limite=5, janela=600)
    try:
        contas.trocar_senha(usuario["id"], str(corpo.get("atual", "")), str(corpo.get("nova", "")))
    except ValueError as exc:
        raise HTTPException(400, {"mensagem": str(exc)})
    resposta = JSONResponse({"ok": True})  # as outras sessões caíram; esta ganha um token novo
    sessao.gravar_cookie(resposta, contas.criar_sessao(usuario["id"], request.headers.get("user-agent", "")), request)
    return resposta


@router.post("/api/conta/proteger")
def proteger_com_senha(request: Request, corpo: dict = Body(...)):
    """Modo aberto → protegido: a conta local ganha e-mail e senha e o painel passa a pedir login."""
    usuario = sessao.usuario_atual()
    if contas.modo_acesso() != "aberto":
        raise HTTPException(409, {"mensagem": "O painel já pede e-mail e senha."})
    sessao.registrar_tentativa(f"proteger|{usuario['id']}", limite=5, janela=600)
    try:
        protegido = contas.proteger_conta(usuario["id"], str(corpo.get("email", "")), str(corpo.get("senha", "")))
    except ValueError as exc:
        raise HTTPException(400, {"mensagem": str(exc)})
    resposta = JSONResponse(_resposta_sessao(protegido))
    sessao.gravar_cookie(resposta, contas.criar_sessao(protegido["id"], request.headers.get("user-agent", "")), request)
    return resposta


@router.post("/api/conta/sem-senha")
def usar_sem_senha(request: Request, corpo: dict = Body(...)):
    """Modo protegido → aberto: só o administrador, confirmando a senha, neste computador e sem equipe ativa."""
    admin = sessao.exigir_admin()
    if contas.modo_acesso() != "protegido":
        raise HTTPException(409, {"mensagem": "O painel já está sem senha."})
    if not sessao.acesso_local(request):
        raise HTTPException(400, {"mensagem": "O uso sem senha só pode ser ligado neste próprio computador."})
    sessao.registrar_tentativa(f"sem-senha|{admin['id']}", limite=5, janela=600)
    try:
        contas.liberar_sem_senha(admin["id"], str(corpo.get("senha", "")))
    except ValueError as exc:
        raise HTTPException(400, {"mensagem": str(exc)})
    return _resposta_sessao(admin)


# ------------------------------------------------------------------ conexões externas
@router.get("/api/conexoes")
def ver_conexoes():
    return contas.conexoes_mascaradas(sessao.usuario_atual()["id"])


@router.put("/api/conexoes")
def salvar_conexoes(corpo: dict = Body(...)):
    usuario = sessao.usuario_atual()
    valores = {}
    for chave, valor in corpo.items():
        if chave not in contas.CONEXOES:
            continue
        valor = str(valor or "").strip()
        if valor.startswith("•"):  # campo mascarado que não foi alterado
            continue
        if chave.endswith("_URL") and valor and (not re.match(r"^https?://", valor) or not _endereco_permitido(valor)):
            raise HTTPException(400, {"mensagem": f"{contas.CONEXOES[chave][0]}: use um endereço http:// ou https:// válido."})
        valores[chave] = valor
    contas.salvar_conexoes(usuario["id"], valores)
    return contas.conexoes_mascaradas(usuario["id"])


_HOSTS_BLOQUEADOS = {"metadata.google.internal", "metadata", "169.254.169.254"}


def _endereco_permitido(url: str) -> bool:
    """Bloqueia endereços de metadados de nuvem e link-local (não há motivo para uma IA morar lá)."""
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host or host in _HOSTS_BLOQUEADOS:
        return False
    try:
        return not ipaddress.ip_address(host).is_link_local
    except ValueError:
        return True  # nome de host comum


@router.post("/api/conexoes/testar")
def testar_conexao(corpo: dict = Body(...)):
    """Testa uma conexão sem gastar créditos (só lista modelos)."""
    usuario = sessao.usuario_atual()
    valores = contas.conexoes_do_usuario(usuario["id"])
    tipo = str(corpo.get("tipo", ""))
    try:
        if tipo == "anthropic":
            if not valores.get("ANTHROPIC_API_KEY"):
                return {"ok": False, "mensagem": "Coloque a chave da Anthropic e salve antes de testar."}
            import anthropic

            anthropic.Anthropic(api_key=valores["ANTHROPIC_API_KEY"], timeout=20).models.list(limit=1)
            return {"ok": True, "mensagem": "Claude conectado."}
        if tipo == "openai":
            base = (valores.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
            if not _endereco_permitido(base):
                return {"ok": False, "mensagem": "Endereço não permitido."}
            r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {valores.get('OPENAI_API_KEY', '')}"},
                             timeout=20, allow_redirects=False)
            return {"ok": r.ok, "mensagem": "Endpoint conectado." if r.ok else f"O endpoint respondeu {r.status_code}."}
        if tipo == "ollama":
            base = (valores.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/").removesuffix("/v1")
            if not _endereco_permitido(base):
                return {"ok": False, "mensagem": "Endereço não permitido."}
            r = requests.get(f"{base}/api/tags", timeout=5, allow_redirects=False)
            modelos = [m.get("name") for m in r.json().get("models", [])] if r.ok else []
            return {"ok": r.ok, "mensagem": f"Ollama conectado: {', '.join(modelos[:6]) or 'sem modelos'}." if r.ok
                    else f"O Ollama respondeu {r.status_code}."}
    except Exception:
        return {"ok": False, "mensagem": "Não conectou: confira o endereço, a chave e se o serviço está ligado."}
    raise HTTPException(400, {"mensagem": "Tipo de conexão desconhecido."})


# ------------------------------------------------------------------ usuários (admin)
@router.get("/api/usuarios")
def listar_usuarios():
    sessao.exigir_admin()
    return [{k: u[k] for k in ("id", "email", "nome", "papel", "ativo")} for u in contas.listar_usuarios()]


@router.post("/api/usuarios")
def criar_usuario(corpo: dict = Body(...)):
    sessao.exigir_admin()
    if contas.modo_acesso() == "aberto":
        raise HTTPException(409, {"mensagem": "Para ter equipe, ative o login com e-mail e senha na aba Conta."})
    try:
        u = contas.criar_usuario(corpo.get("email", ""), corpo.get("nome", ""), corpo.get("senha", ""),
                                 "admin" if corpo.get("papel") == "admin" else "usuario")
    except ValueError as exc:
        raise HTTPException(400, {"mensagem": str(exc)})
    return {k: u[k] for k in ("id", "email", "nome", "papel", "ativo")}


@router.post("/api/usuarios/{usuario_id}/ativo")
def ativar_usuario(usuario_id: int, corpo: dict = Body(...)):
    admin = sessao.exigir_admin()
    if usuario_id == admin["id"]:
        raise HTTPException(400, {"mensagem": "Você não pode desativar a própria conta."})
    contas.definir_ativo(usuario_id, bool(corpo.get("ativo")))
    return {"ok": True}


# ------------------------------------------------------------------ canais
@router.post("/api/canais/novo")
def criar_canal(corpo: dict = Body(...)):
    """Cria um canal de cortes para o usuário logado (depois ele conecta o YouTube pelo selo do canal)."""
    from esteira.canal import criar_canal as criar

    usuario = sessao.usuario_atual()
    nome = str(corpo.get("nome", "")).strip()[:60]
    if len(nome) < 3:
        raise HTTPException(400, {"mensagem": "Dê um nome ao canal (pelo menos 3 letras)."})
    base = util.slugify(nome, 30).replace("-", "_") or "canal"
    canal_id, n = base, 2
    existentes = set(Canal.listar(incluir_ocultos=True))
    while canal_id in existentes:
        canal_id, n = f"{base}_{n}", n + 1
    try:
        canal = criar(canal_id, nome, str(corpo.get("nicho") or "cortes de lives"), str(corpo.get("tom") or "curioso"),
                      str(corpo.get("paleta") or "roxo e amarelo"), str(corpo.get("ritmo") or "rapido"), tipo="cortes")
    except Exception as exc:
        raise HTTPException(400, {"mensagem": f"Não deu para criar o canal: {str(exc)[:200]}"})
    contas.atribuir_canal(canal.id, usuario["id"])
    return {"ok": True, "canal": canal.id}
