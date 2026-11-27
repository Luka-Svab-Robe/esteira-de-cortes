"""Contas da plataforma: usuários, sessões, perfil (com visibilidade), conexões criptografadas e dono dos canais.

Banco: dados/plataforma.db (SQLite, WAL). Senhas com scrypt (stdlib); o cookie de sessão é um token aleatório e
no banco fica só o SHA-256 dele; chaves de API ficam cifradas com Fernet (chave em dados/.chave_plataforma, 0600).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from esteira.config import RAIZ

PASTA_DADOS = Path(os.environ.get("PLATAFORMA_DADOS") or RAIZ / "dados")
BANCO = PASTA_DADOS / "plataforma.db"
CHAVE = PASTA_DADOS / ".chave_plataforma"
SESSAO_DIAS = 30
CAMPOS_PERFIL = ("nome_publico", "bio", "cidade", "site", "instagram", "telefone")
# conexões externas que a plataforma entende (rótulo, se é segredo)
CONEXOES = {
    "ANTHROPIC_API_KEY": ("Chave da API Anthropic (Claude)", True),
    "ANTHROPIC_MODELO": ("Modelo Claude", False),
    "OPENAI_API_KEY": ("Chave de API compatível com OpenAI", True),
    "OPENAI_BASE_URL": ("Endpoint compatível com OpenAI", False),
    "OPENAI_MODELO": ("Modelo do endpoint OpenAI", False),
    "OLLAMA_URL": ("Endereço do Ollama local", False),
    "TRADUCAO_MODELO": ("Modelo do Ollama para tradução/copiloto", False),
    "WEBHOOK_URL": ("Webhook avisado quando os Shorts ficam prontos", False),
}
_trava = threading.Lock()


# ------------------------------------------------------------------ banco
class _Conexao(sqlite3.Connection):
    """F14: o "with" do sqlite3 não fecha a conexão. Esta fecha ao sair do bloco (o banco não fica travado
    no Windows, e a pasta de dados pode ser apagada/restaurada)."""

    def __exit__(self, tipo, valor, tb):
        try:
            return super().__exit__(tipo, valor, tb)
        finally:
            self.close()


def _con() -> sqlite3.Connection:
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO, timeout=15, isolation_level=None, factory=_Conexao)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def iniciar() -> None:
    with _con() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, nome TEXT NOT NULL,
            senha_hash TEXT NOT NULL, papel TEXT NOT NULL DEFAULT 'usuario', ativo INTEGER NOT NULL DEFAULT 1,
            perfil TEXT NOT NULL DEFAULT '{}', visivel TEXT NOT NULL DEFAULT '{}', criado_em REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessoes (
            token_hash TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            criado_em REAL NOT NULL, expira_em REAL NOT NULL, agente TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS conexoes (
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE, chave TEXT NOT NULL,
            valor TEXT NOT NULL, atualizado_em REAL NOT NULL, PRIMARY KEY (usuario_id, chave));
        CREATE TABLE IF NOT EXISTS canais_dono (
            canal_id TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_sessoes_usuario ON sessoes(usuario_id);
        CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
        """)
    for arquivo in (BANCO, CHAVE):
        if arquivo.exists():
            os.chmod(arquivo, 0o600)


def _fernet() -> Fernet:
    with _trava:
        if not CHAVE.exists():
            PASTA_DADOS.mkdir(parents=True, exist_ok=True)
            CHAVE.write_bytes(Fernet.generate_key())
            os.chmod(CHAVE, 0o600)
    return Fernet(CHAVE.read_bytes())


# ------------------------------------------------------------------ senhas
def _hash_senha(senha: str, sal: bytes | None = None) -> str:
    sal = sal or secrets.token_bytes(16)
    derivada = hashlib.scrypt(senha.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${sal.hex()}${derivada.hex()}"


def _confere_senha(senha: str, guardada: str) -> bool:
    try:
        _, sal, derivada = guardada.split("$")
        return hmac.compare_digest(_hash_senha(senha, bytes.fromhex(sal)).split("$")[2], derivada)
    except ValueError:
        return False


def validar_senha(senha: str) -> None:
    if len(senha or "") < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")


def _publico(linha: sqlite3.Row | None) -> dict | None:
    if not linha:
        return None
    return {"id": linha["id"], "email": linha["email"], "nome": linha["nome"], "papel": linha["papel"],
            "ativo": bool(linha["ativo"]), "perfil": json.loads(linha["perfil"] or "{}"),
            "visivel": json.loads(linha["visivel"] or "{}")}


# ------------------------------------------------------------------ usuários
def existe_algum_usuario() -> bool:
    with _con() as con:
        return con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None


class PlataformaJaConfigurada(Exception):
    pass


# ------------------------------------------------------------------ modo de acesso (com ou sem senha)
# "protegido": cada pessoa entra com e-mail e senha. "aberto": quem usa este computador entra direto, sem senha,
# numa conta local de administrador (o painel só escuta em 127.0.0.1). Bancos antigos, que já têm contas, são
# "protegido" sem mudar nada.
EMAIL_LOCAL = "local@localhost"


def _config(con: sqlite3.Connection, chave: str) -> str | None:
    linha = con.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha else None


def modo_acesso() -> str | None:
    """aberto | protegido | None (ninguém configurou ainda: o primeiro acesso pergunta)."""
    with _con() as con:
        modo = _config(con, "modo_acesso")
        if modo:
            return modo
        return "protegido" if con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() else None


def usuario_aberto() -> dict | None:
    """No modo aberto, a conta que fica entrada para quem usa este computador."""
    with _con() as con:
        if _config(con, "modo_acesso") != "aberto":
            return None
        usuario_id = _config(con, "usuario_aberto")
        linha = con.execute("SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (usuario_id,)).fetchone()
    return _publico(linha)


def criar_usuario_local(nome: str) -> dict:
    """Primeiro acesso sem senha: cria a conta local de administrador e liga o modo aberto (só se não houver ninguém)."""
    nome = (nome or "").strip()[:120] or "Você"
    with _con() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            if con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
                raise PlataformaJaConfigurada("A plataforma já foi configurada. Entre com sua conta.")
            cur = con.execute("INSERT INTO usuarios (email, nome, senha_hash, papel, criado_em) VALUES (?, ?, '', 'admin', ?)",
                              (EMAIL_LOCAL, nome, time.time()))
            con.execute("INSERT OR REPLACE INTO config VALUES ('modo_acesso', 'aberto')")
            con.execute("INSERT OR REPLACE INTO config VALUES ('usuario_aberto', ?)", (str(cur.lastrowid),))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return usuario(cur.lastrowid)


def proteger_conta(usuario_id: int, email: str, senha: str) -> dict:
    """Modo aberto → protegido: a conta local ganha e-mail e senha, e o painel passa a pedir login."""
    email = _email_valido(email)
    validar_senha(senha)
    try:
        with _con() as con:
            con.execute("UPDATE usuarios SET email = ?, senha_hash = ? WHERE id = ?", (email, _hash_senha(senha), usuario_id))
            con.execute("INSERT OR REPLACE INTO config VALUES ('modo_acesso', 'protegido')")
            con.execute("DELETE FROM config WHERE chave = 'usuario_aberto'")
    except sqlite3.IntegrityError:
        raise ValueError("Já existe uma conta com esse e-mail.")
    return usuario(usuario_id)


def liberar_sem_senha(usuario_id: int, senha: str) -> None:
    """Modo protegido → aberto (só o administrador, confirmando a senha, e sem outras contas ativas na equipe)."""
    with _con() as con:
        linha = con.execute("SELECT senha_hash, papel FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        outros = con.execute("SELECT COUNT(*) FROM usuarios WHERE ativo = 1 AND id != ?", (usuario_id,)).fetchone()[0]
    if not linha or linha["papel"] != "admin":
        raise ValueError("Só o administrador pode fazer isso.")
    if not _confere_senha(senha or "", linha["senha_hash"]):
        raise ValueError("A senha não confere.")
    if outros:
        raise ValueError("Há outras contas ativas na equipe. Desative-as antes de usar sem senha.")
    with _con() as con:
        con.execute("INSERT OR REPLACE INTO config VALUES ('modo_acesso', 'aberto')")
        con.execute("INSERT OR REPLACE INTO config VALUES ('usuario_aberto', ?)", (str(usuario_id),))


def _email_valido(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 200 or email == EMAIL_LOCAL:
        raise ValueError("Informe um e-mail válido.")
    return email


def criar_usuario(email: str, nome: str, senha: str, papel: str = "usuario", primeiro: bool = False) -> dict:
    """Cria uma conta. Com primeiro=True só cria se ainda não houver ninguém (checagem e inserção na mesma transação)."""
    email = _email_valido(email)
    if not (nome or "").strip():
        raise ValueError("Informe o nome.")
    validar_senha(senha)
    senha_hash = _hash_senha(senha)
    try:
        with _con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                if primeiro and con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
                    raise PlataformaJaConfigurada("A plataforma já foi configurada. Entre com sua conta.")
                cur = con.execute("INSERT INTO usuarios (email, nome, senha_hash, papel, criado_em) VALUES (?, ?, ?, ?, ?)",
                                  (email, nome.strip()[:120], senha_hash, papel, time.time()))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return usuario(cur.lastrowid)
    except sqlite3.IntegrityError:
        raise ValueError("Já existe uma conta com esse e-mail.")


def usuario(usuario_id: int) -> dict | None:
    with _con() as con:
        return _publico(con.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone())


def listar_usuarios() -> list[dict]:
    with _con() as con:
        return [_publico(l) for l in con.execute("SELECT * FROM usuarios ORDER BY criado_em")]


def autenticar(email: str, senha: str) -> dict | None:
    with _con() as con:
        linha = con.execute("SELECT * FROM usuarios WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
    if not linha or not linha["ativo"]:
        _hash_senha(senha or "x")  # tempo parecido com o de uma senha errada
        return None
    return _publico(linha) if _confere_senha(senha or "", linha["senha_hash"]) else None


def atualizar_conta(usuario_id: int, nome: str | None = None, perfil: dict | None = None,
                    visivel: dict | None = None) -> dict:
    atual = usuario(usuario_id)
    if not atual:
        raise ValueError("Conta não encontrada.")
    novo_perfil = dict(atual["perfil"])
    for campo in CAMPOS_PERFIL:
        if perfil and campo in perfil:
            novo_perfil[campo] = str(perfil[campo] or "").strip()[:300]
    novo_visivel = dict(atual["visivel"])
    for campo in ("nome", "email", *CAMPOS_PERFIL):
        if visivel and campo in visivel:
            novo_visivel[campo] = bool(visivel[campo])
    with _con() as con:
        con.execute("UPDATE usuarios SET nome = ?, perfil = ?, visivel = ? WHERE id = ?",
                    ((nome or atual["nome"]).strip()[:120], json.dumps(novo_perfil, ensure_ascii=False),
                     json.dumps(novo_visivel), usuario_id))
    return usuario(usuario_id)


def trocar_senha(usuario_id: int, atual: str, nova: str) -> None:
    with _con() as con:
        linha = con.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if not linha or not _confere_senha(atual or "", linha["senha_hash"]):
        raise ValueError("A senha atual não confere.")
    validar_senha(nova)
    with _con() as con:
        con.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (_hash_senha(nova), usuario_id))
        con.execute("DELETE FROM sessoes WHERE usuario_id = ?", (usuario_id,))  # derruba as outras sessões


def definir_ativo(usuario_id: int, ativo: bool) -> None:
    with _con() as con:
        con.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (1 if ativo else 0, usuario_id))
        if not ativo:
            con.execute("DELETE FROM sessoes WHERE usuario_id = ?", (usuario_id,))


def perfil_visivel(u: dict) -> dict:
    """Só os dados que o usuário marcou como visíveis no site."""
    visiveis = {"nome": u["nome"]} if u["visivel"].get("nome", True) else {}
    if u["visivel"].get("email"):
        visiveis["email"] = u["email"]
    for campo in CAMPOS_PERFIL:
        if u["visivel"].get(campo) and u["perfil"].get(campo):
            visiveis[campo] = u["perfil"][campo]
    return visiveis


# ------------------------------------------------------------------ sessões
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def criar_sessao(usuario_id: int, agente: str = "") -> str:
    token = secrets.token_urlsafe(32)
    agora = time.time()
    with _con() as con:
        con.execute("INSERT INTO sessoes VALUES (?, ?, ?, ?, ?)",
                    (_hash_token(token), usuario_id, agora, agora + SESSAO_DIAS * 86400, (agente or "")[:200]))
        con.execute("DELETE FROM sessoes WHERE expira_em < ?", (agora,))
    return token


def usuario_da_sessao(token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    agora = time.time()
    with _con() as con:
        linha = con.execute("SELECT u.* FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id "
                            "WHERE s.token_hash = ? AND s.expira_em > ? AND u.ativo = 1",
                            (_hash_token(token), agora)).fetchone()
        if linha:  # sessão deslizante: renova se faltar menos da metade do prazo
            con.execute("UPDATE sessoes SET expira_em = ? WHERE token_hash = ? AND expira_em < ?",
                        (agora + SESSAO_DIAS * 86400, _hash_token(token), agora + SESSAO_DIAS * 43200))
    return _publico(linha)


def encerrar_sessao(token: str | None) -> None:
    if token:
        with _con() as con:
            con.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash_token(token),))


# ------------------------------------------------------------------ conexões (chaves de API, endpoints)
def salvar_conexoes(usuario_id: int, valores: dict) -> None:
    f = _fernet()
    with _con() as con:
        for chave, valor in valores.items():
            if chave not in CONEXOES:
                continue
            valor = str(valor or "").strip()
            if not valor:
                con.execute("DELETE FROM conexoes WHERE usuario_id = ? AND chave = ?", (usuario_id, chave))
                continue
            con.execute("INSERT INTO conexoes VALUES (?, ?, ?, ?) ON CONFLICT(usuario_id, chave) DO UPDATE SET "
                        "valor = excluded.valor, atualizado_em = excluded.atualizado_em",
                        (usuario_id, chave, f.encrypt(valor[:500].encode()).decode(), time.time()))


def conexoes_do_usuario(usuario_id: int) -> dict:
    """Valores em claro (só para uso interno: subprocessos e copiloto). Nunca devolva isto ao navegador."""
    f = _fernet()
    with _con() as con:
        linhas = con.execute("SELECT chave, valor FROM conexoes WHERE usuario_id = ?", (usuario_id,)).fetchall()
    saida = {}
    for linha in linhas:
        try:
            saida[linha["chave"]] = f.decrypt(linha["valor"].encode()).decode()
        except InvalidToken:
            continue
    return saida


def conexoes_mascaradas(usuario_id: int) -> list[dict]:
    valores = conexoes_do_usuario(usuario_id)
    saida = []
    for chave, (rotulo, segredo) in CONEXOES.items():
        valor = valores.get(chave, "")
        exibido = ("•" * 8 + valor[-4:]) if (segredo and valor) else valor
        saida.append({"chave": chave, "rotulo": rotulo, "segredo": segredo, "configurado": bool(valor), "valor": exibido})
    return saida


def ambiente_do_usuario(usuario_id: int) -> dict:
    """Variáveis de ambiente para os processos da esteira deste usuário (as conexões dele têm prioridade)."""
    valores = conexoes_do_usuario(usuario_id)
    extra = {k: v for k, v in valores.items() if k != "WEBHOOK_URL"}
    if valores.get("OPENAI_BASE_URL"):
        # endpoint escolhido pelo usuário nunca recebe a chave da plataforma (.env): vai a dele ou nenhuma
        extra["OPENAI_API_KEY"] = valores.get("OPENAI_API_KEY", "")
        if not valores.get("ANTHROPIC_API_KEY"):
            extra.setdefault("LLM_PROVEDOR", "openai")
    if valores.get("ANTHROPIC_API_KEY"):
        extra.setdefault("LLM_PROVEDOR", "anthropic")
    elif valores.get("OPENAI_API_KEY"):
        extra.setdefault("LLM_PROVEDOR", "openai")
    return extra


# ------------------------------------------------------------------ canais de cada usuário
def dono_do_canal(canal_id: str) -> int | None:
    with _con() as con:
        linha = con.execute("SELECT usuario_id FROM canais_dono WHERE canal_id = ?", (canal_id,)).fetchone()
    return linha["usuario_id"] if linha else None


def atribuir_canal(canal_id: str, usuario_id: int) -> None:
    with _con() as con:
        con.execute("INSERT INTO canais_dono VALUES (?, ?) ON CONFLICT(canal_id) DO UPDATE SET usuario_id = excluded.usuario_id",
                    (canal_id, usuario_id))


def canais_do_usuario(usuario_id: int) -> list[str]:
    with _con() as con:
        return [l["canal_id"] for l in con.execute("SELECT canal_id FROM canais_dono WHERE usuario_id = ? ORDER BY canal_id",
                                                    (usuario_id,))]


def adotar_canais_sem_dono(usuario_id: int, canais: list[str]) -> None:
    """Na criação do primeiro administrador, os canais que já existiam passam a ser dele."""
    for canal_id in canais:
        if dono_do_canal(canal_id) is None:
            atribuir_canal(canal_id, usuario_id)
