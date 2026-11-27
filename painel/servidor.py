"""Backend do painel de revisão (FastAPI). Escuta apenas em 127.0.0.1.

Rotas principais:
  GET  /api/projetos                         lista (filtros: canal, estado)
  GET  /api/projetos/{canal}/{pasta}         tudo o que a tela de revisão precisa
  PUT  /api/projetos/{canal}/{pasta}/metadados
  POST /api/projetos/{canal}/{pasta}/aprovar  salva edições, aprova e agenda no YouTube
  POST /api/projetos/{canal}/{pasta}/reprovar registra o motivo para o agente corrigir
  POST /api/projetos/{canal}/{pasta}/publicar tenta publicar de novo (após erro)
  GET  /arquivos/{canal}/{pasta}/{caminho}    mídia do projeto (com suporte a Range)
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from esteira import log, orquestrador, util
from esteira.canal import Canal
from esteira.config import PASTA_PAINEL, PASTA_PROJETOS, env, env_bool, env_int
from esteira.projeto import ESTADOS, Projeto
from painel import contas, progresso, rotas_agenda, rotas_apis, rotas_conta, rotas_etapas, rotas_legenda, sessao

ESTATICOS = PASTA_PAINEL / "static"
app = FastAPI(title="Esteira de Cortes", docs_url=None, redoc_url=None, openapi_url=None)
contas.iniciar()
app.middleware("http")(sessao.middleware)
app.add_middleware(GZipMiddleware, minimum_size=800)
# só responde pelos nomes locais (evita DNS rebinding); PAINEL_HOSTS="nome1,nome2" libera outros nomes da rede
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]",
                   *[h.strip() for h in env("PAINEL_HOSTS", "").split(",") if h.strip()]])
app.include_router(rotas_agenda.router)
app.include_router(rotas_apis.router)
app.include_router(rotas_conta.router)
app.include_router(rotas_etapas.router)
app.include_router(rotas_legenda.router)


class _EstaticoComCache(StaticFiles):
    """Código (js/css) revalida pelo ETag a cada carga (resposta 304 em ~1 ms, nunca fica velho depois de uma
    atualização); fontes e imagens ficam 7 dias no cache do navegador."""

    async def get_response(self, path, scope):
        resposta = await super().get_response(path, scope)
        codigo = str(path).endswith((".js", ".mjs", ".css", ".html"))
        resposta.headers.setdefault("Cache-Control", "no-cache" if codigo else "public, max-age=604800")
        return resposta


app.mount("/static", _EstaticoComCache(directory=str(ESTATICOS)), name="static")
from esteira.config import PASTA_FONTES as _PASTA_FONTES  # noqa: E402

if _PASTA_FONTES.exists():
    app.mount("/fontes", _EstaticoComCache(directory=str(_PASTA_FONTES)), name="fontes")
_trabalhos: dict[str, threading.Thread] = {}
_conexoes: dict[str, dict] = {}  # canal -> {"em_andamento": bool, "erro": str}
_NOME_SEGURO = re.compile(r"[\w\-]+")
_TESTES = env_bool("PAINEL_MOSTRAR_TESTES")  # só para testes internos (canais começando com "_")


def _projeto(canal: str, pasta: str) -> Projeto:
    if not _NOME_SEGURO.fullmatch(canal) or not _NOME_SEGURO.fullmatch(pasta) or not _eh_cortes(canal):
        raise HTTPException(404, "Projeto não encontrado")
    caminho = PASTA_PROJETOS / canal / pasta
    if not (caminho / "status.json").exists():
        raise HTTPException(404, "Projeto não encontrado")
    return Projeto(caminho)


def _url(projeto: Projeto, relativo: str | None) -> str | None:
    if not relativo:
        return None
    arquivo = projeto.p(relativo)
    if not arquivo.exists():
        return None
    return f"/arquivos/{projeto.id}/{relativo}?v={int(arquivo.stat().st_mtime)}"


# ------------------------------------------------------------------ páginas
@app.get("/")
def inicio():
    return FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifesto_do_app():
    return FileResponse(ESTATICOS / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    """Fica na raiz para cuidar do site inteiro (o app instalado abre rápido e avisa se o painel estiver desligado)."""
    return FileResponse(ESTATICOS / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
def icone_do_site():
    return FileResponse(ESTATICOS / "icones" / "app-192.png", media_type="image/png")


@app.get("/api/saude")
def saude():
    return {"ok": True}


def _tipo_cortes(canal_id: str, _cache: dict = {}) -> bool:
    if canal_id not in _cache:
        try:
            _cache[canal_id] = Canal.carregar(canal_id).editorial.get("tipo_canal", "dark") == "cortes"
        except Exception:
            return False
    return _cache[canal_id]


def _eh_cortes(canal_id: str) -> bool:
    """Canal visível para quem está logado: de cortes e dele (canais "_" só no painel de testes)."""
    if canal_id.startswith("_"):
        return _TESTES and _tipo_cortes(canal_id)
    usuario = sessao.USUARIO.get()
    return bool(usuario) and _tipo_cortes(canal_id) and contas.dono_do_canal(canal_id) == usuario["id"]


@app.get("/api/canais")
def canais():
    resultado = []
    for canal_id in Canal.listar():
        if not _eh_cortes(canal_id):
            continue
        c = Canal.carregar(canal_id)
        info_yt = util.ler_json(c.pasta_credenciais / "canal_youtube.json", {}) or {}
        resultado.append({"id": c.id, "nome": c.nome, "estilo": c.estilo, "fila": len(c.fila_listar()),
                          "tipo": c.editorial.get("tipo_canal", "dark"),
                          "youtube": {"conectado": c.caminho_token_youtube.exists(), "nome": info_yt.get("titulo", ""),
                                      **_conexoes.get(c.id, {})}})
    return resultado


@app.get("/api/projetos")
def projetos(canal: str | None = None, estado: str | None = None):
    estados = tuple(estado.split(",")) if estado else None
    lista = []
    for p in Projeto.listar(canal, estados):
        if not _eh_cortes(p.canal_id):
            continue
        resumo = p.resumo()
        meta = p.ler("metadados.json", {}) or {}
        escolhida = int(meta.get("thumbnail_escolhida", 1) or 1)
        resumo["capa"] = _url(p, f"thumbnails/thumb_{escolhida}.jpg") or _url(p, "cenas/previa_001.jpg")
        lista.append(resumo)
    return lista


@app.get("/api/projetos/{canal}/{pasta}")
def detalhes(canal: str, pasta: str):
    p = _projeto(canal, pasta)
    status = p.status
    meta = p.ler("metadados.json", {}) or {}
    roteiro = p.ler("roteiro.json", {}) or {}
    linha = {item["bloco"]: item for item in p.linha_tempo()}
    auditoria = {a["bloco"]: a for a in (p.ler("audio/auditoria.json", []) or [])}
    cache_cenas = p.ler("cenas/cache.json", {}) or {}
    shorts_meta = {s["indice"]: s for s in meta.get("shorts", [])}
    shorts = []
    for s in p.ler("shorts/shorts.json", []) or []:
        extra = shorts_meta.get(s["indice"], {})
        shorts.append({**s, "url": _url(p, s["arquivo"]), "selecionado": extra.get("selecionado", True),
                       "titulo": extra.get("titulo", s.get("titulo", "")),
                       "descricao": extra.get("descricao", s.get("descricao", "")),
                       "tags": extra.get("tags", s.get("tags", []))})
    thumbs = [{**t, "url": _url(p, t["arquivo"])} for t in (p.ler("thumbnails/thumbs.json", []) or [])]
    cenas = []
    for bloco in roteiro.get("blocos", []):
        tempo = linha.get(bloco["id"], {})
        aud = auditoria.get(bloco["id"], {})
        cena = cache_cenas.get(str(bloco["id"]), {})
        cenas.append({
            "id": bloco["id"], "narracao": bloco["narracao"], "modo": cena.get("modo") or bloco.get("modo_visual"),
            "tipo_cartao": bloco.get("tipo_cartao"), "destaque": bloco.get("destaque"),
            "busca_visual": bloco.get("busca_visual"), "potencial_short": bloco.get("potencial_short"),
            "capitulo": bloco.get("capitulo"), "inicio": tempo.get("inicio"), "duracao": tempo.get("duracao"),
            "wer": aud.get("wer"), "motor_voz": aud.get("motor"), "alerta_pronuncia": aud.get("acima_do_limite", False),
            "previa": _url(p, cena.get("previa")),
        })
    return {
        "resumo": p.resumo(),
        "tipo": status.get("tipo", "dark"), "live": status.get("live"), "fonte": status.get("fonte"),
        "status": {k: status.get(k) for k in ("estado", "detalhe_estado", "alertas", "reprovacoes", "youtube", "etapas")}
                  | {"eventos": (status.get("eventos") or [])[-40:]},
        "metadados": meta,
        "video": _url(p, "video_final.mp4"),
        "legendas": _url(p, "legendas/video.srt"),
        "shorts": shorts,
        "thumbnails": thumbs,
        "fontes": p.ler("fontes.json", []) or [],
        "cenas": cenas,
        "estados": ESTADOS,
        "publicando": canal + "/" + pasta in _trabalhos and _trabalhos[canal + "/" + pasta].is_alive(),
    }


@app.put("/api/projetos/{canal}/{pasta}/metadados")
def salvar_metadados(canal: str, pasta: str, edicoes: dict = Body(...)):
    p = _projeto(canal, pasta)
    if p.estado in ("publicando", "agendado"):
        raise HTTPException(409, "O vídeo já foi enviado ao YouTube; edite por lá.")
    meta = orquestrador.salvar_edicoes(p, edicoes)
    p.evento("Metadados editados no painel")
    return {"ok": True, "metadados": meta}


def _publicar(p: Projeto) -> None:
    try:
        orquestrador.aprovar(p, publicar=True)
    except Exception as exc:  # o estado de erro fica registrado no status.json
        log.erro(f"Publicação de {p.id} falhou: {exc}")
        if p.estado in ("aprovado", "publicando"):
            p.definir_estado("erro_publicacao", str(exc))


def _iniciar_publicacao(p: Projeto) -> None:
    trabalho = _trabalhos.get(p.id)
    if trabalho and trabalho.is_alive():
        raise HTTPException(409, "A publicação deste projeto já está em andamento.")
    trabalho = threading.Thread(target=_publicar, args=(p,), daemon=False, name=f"publicar-{p.pasta.name}")
    _trabalhos[p.id] = trabalho
    trabalho.start()


@app.post("/api/projetos/{canal}/{pasta}/aprovar")
def aprovar(canal: str, pasta: str, edicoes: dict = Body(default={})):
    p = _projeto(canal, pasta)
    if p.estado not in ("pendente_revisao", "reprovado", "erro_publicacao", "aprovado"):
        raise HTTPException(409, f"Não é possível aprovar um projeto '{ESTADOS.get(p.estado, p.estado)}'.")
    if p.status.get("tipo") == "cortes":
        if not (p.ler("shorts/shorts.json", []) or []):
            raise HTTPException(409, "Nenhum Short pronto neste projeto.")
    elif not p.video_final.exists():
        raise HTTPException(409, "O vídeo final ainda não existe.")
    if edicoes:
        orquestrador.salvar_edicoes(p, edicoes)
    p.evento("Aprovado no painel")
    _iniciar_publicacao(p)
    return {"ok": True, "estado": "aprovado", "publicando": True}


@app.post("/api/projetos/{canal}/{pasta}/reprovar")
def reprovar(canal: str, pasta: str, corpo: dict = Body(...)):
    p = _projeto(canal, pasta)
    motivo = str(corpo.get("motivo", "")).strip()
    if not motivo:
        raise HTTPException(400, "Descreva o motivo da reprovação.")
    if p.estado in ("publicando", "agendado"):
        raise HTTPException(409, "O vídeo já foi enviado ao YouTube.")
    etapas = [e for e in corpo.get("etapas", []) if e in ("roteiro", "narracao", "montagem", "shorts", "thumbnails", "metadados")]
    blocos = [int(b) for b in corpo.get("blocos", []) if str(b).isdigit()]
    registro = orquestrador.reprovar(p, motivo, etapas or None, blocos or None)
    return {"ok": True, "reprovacao": registro}


@app.post("/api/projetos/{canal}/{pasta}/publicar")
def publicar_novamente(canal: str, pasta: str):
    p = _projeto(canal, pasta)
    trabalho = _trabalhos.get(p.id)
    interrompido = p.estado == "publicando" and not (trabalho and trabalho.is_alive())
    if p.estado not in ("aprovado", "erro_publicacao") and not interrompido:
        raise HTTPException(409, "Só é possível reenviar projetos aprovados.")
    _iniciar_publicacao(p)
    return {"ok": True, "publicando": True}


# ------------------------------------------------------------------ painel de cortes
_envios: dict[str, dict] = {}  # id -> {canal, url, proc, log, inicio, projeto}
def _canal_cortes(canal_id: str) -> Canal:
    if not _NOME_SEGURO.fullmatch(canal_id or "") or not _eh_cortes(canal_id):
        raise HTTPException(404, {"codigo": "canal", "mensagem": "Escolha um canal de cortes."})
    return Canal.carregar(canal_id)


def _resumo_cortes(p: Projeto) -> dict:
    st = p.status
    meta = p.ler("metadados.json", {}) or {}
    textos = {int(s["indice"]): s for s in meta.get("shorts", [])}
    no_yt = {int(s["indice"]): s for s in (st.get("youtube") or {}).get("shorts", [])}
    shorts = []
    for s in p.ler("shorts/shorts.json", []) or []:
        n = int(s["indice"])
        t = textos.get(n, {})
        do_short = sorted((e for e in _envios.values() if e.get("projeto") == p.id and e.get("indice") == n),
                          key=lambda e: e["inicio"])
        refazendo = bool(do_short) and do_short[-1]["proc"].poll() is None
        erro_refazer = (progresso.ultima_falha(do_short[-1]["log"]) if do_short and do_short[-1]["proc"].poll() not in (None, 0)
                        else "")
        shorts.append({"indice": n, "video": _url(p, s.get("arquivo")), "duracao": s.get("duracao"),
                       "inicio": s.get("inicio"), "fim": round((s.get("inicio") or 0) + (s.get("duracao") or 0), 2),
                       "gancho": s.get("gancho", ""), "trilha": s.get("trilha", ""),
                       "traduzida_de": s.get("legenda_traduzida_de"), "modelo": s.get("modelo") or "padrao",
                       "legenda_editada": bool(s.get("legenda_editada")),
                       "titulo": t.get("titulo", s.get("titulo", "")), "descricao": t.get("descricao", ""),
                       "tags": t.get("tags", []), "selecionado": t.get("selecionado", True),
                       "youtube": no_yt.get(n), "refazendo": refazendo, "erro_refazer": erro_refazer})
    live = st.get("live", {}) or {}
    rodando = sorted((e for e in _envios.values() if e.get("projeto") == p.id and e["proc"].poll() is None),
                     key=lambda e: e["inicio"])
    return {"id": p.id, "canal": p.canal_id, "estado": p.estado, "estado_texto": ESTADOS.get(p.estado, p.estado),
            "detalhe": progresso.mensagem_de_erro(st.get("detalhe_estado")) if p.estado == "erro"
                        else st.get("detalhe_estado", ""), "criado_em": st.get("criado_em", ""),
            "atualizado_em": st.get("atualizado_em", ""),
            "live": {"id": live.get("id", ""), "titulo": live.get("titulo", ""), "url": live.get("url", ""), "canal": live.get("canal", ""),
                     "plataforma": live.get("plataforma", ""), "duracao": live.get("duracao")},
            "fonte": {"nome": (st.get("fonte") or {}).get("nome", ""), "tipo": (st.get("fonte") or {}).get("tipo", "")},
            "trecho": st.get("trecho"), "rodada": st.get("rodada"), "etapa": progresso.etapa_atual(p),
            "progresso": progresso.do_projeto(p, rodando[-1]["log"] if rodando else None),
            "trabalhando": bool(rodando), "opcoes": st.get("opcoes") or {},
            "edicao_desatualizada": bool(st.get("edicao_desatualizada")),
            "alertas": [a.get("mensagem", "") for a in st.get("alertas", [])], "shorts": shorts,
            "publicando": p.id in _trabalhos and _trabalhos[p.id].is_alive()}


def _estado_envio(envio: dict) -> dict:
    codigo = envio["proc"].poll()
    projeto = envio.get("projeto")
    if not projeto:
        try:
            m = re.search(r"^PROJETO=(\S+)", Path(envio["log"]).read_text(encoding="utf-8", errors="replace"), re.M)
            projeto = envio["projeto"] = m.group(1) if m else None
        except OSError:
            pass
        if not projeto:  # o projeto é criado logo no começo; procura o mais novo com a mesma live
            candidatos = [p for p in Projeto.listar(envio["canal"]) if p.estado != "descartado"
                          and (p.status.get("live") or {}).get("url") in (envio["url"], envio.get("url_live"))
                          and p.status.get("criado_em", "") >= envio["inicio"][:19]]
            if candidatos:
                projeto = envio["projeto"] = max(candidatos, key=lambda p: p.status.get("criado_em", "")).id
    estado = "rodando" if codigo is None else "concluido" if codigo == 0 else "aguardando" if codigo == 4 else "erro"
    return {"id": envio["id"], "canal": envio["canal"], "url": envio["url"], "titulo": envio.get("titulo", ""),
            "dono": envio.get("dono", ""), "estado": estado, "projeto": projeto, "inicio": envio["inicio"],
            "progresso": progresso.do_processo(envio["log"], codigo is None, codigo), "etapa": envio.get("etapa", "")}


def _iniciar_processo(canal: str, args: list[str], url: str, **extra) -> dict:
    import subprocess
    import sys
    import time
    import uuid

    from esteira.config import PASTA_LOGS, RAIZ

    usuario = sessao.usuario_atual()
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex[:10]
    caminho_log = PASTA_LOGS / f"cortes_{canal}_{time.strftime('%Y%m%d_%H%M%S')}_{ident}.log"
    saida = open(caminho_log, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(RAIZ / "esteira.py"), *args], cwd=str(RAIZ), stdout=saida,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                            env={**__import__("os").environ, **contas.ambiente_do_usuario(usuario["id"]),
                                 "PYTHONUNBUFFERED": "1"})
    envio = {"id": ident, "canal": canal, "url": url, "proc": proc, "log": str(caminho_log),
             "inicio": util.agora_iso(), "usuario_id": usuario["id"], **extra}
    _envios[ident] = envio
    return envio


_CACHE_AUTOMACAO: dict = {}
TEMPO_CACHE_AUTOMACAO = 30  # segundos; consultar o agendador do sistema custa alguns subprocessos


def _status_automacao() -> dict:
    import time

    from esteira import automacao

    if time.monotonic() - _CACHE_AUTOMACAO.get("em", -1e9) > TEMPO_CACHE_AUTOMACAO:
        try:
            _CACHE_AUTOMACAO.update(dados=automacao.status(), em=time.monotonic())
        except Exception:
            _CACHE_AUTOMACAO.update(dados={}, em=time.monotonic())
    return _CACHE_AUTOMACAO["dados"]


@app.get("/api/painel")
def painel(request: Request):
    """Tudo o que a tela precisa, numa chamada só. Com ETag: se nada mudou, responde 304 sem corpo."""
    import hashlib

    from fastapi.responses import Response

    corpo = json.dumps(dados_do_painel(), ensure_ascii=False, separators=(",", ":"), default=str).encode()
    etag = '"' + hashlib.sha1(corpo).hexdigest()[:20] + '"'
    cabecalhos = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cabecalhos)
    return Response(corpo, media_type="application/json", headers=cabecalhos)


def dados_do_painel() -> dict:
    import datetime as dt

    from esteira.cortes import fontes as fontes_mod

    auto = _status_automacao()
    canais_info = []
    for canal_id in Canal.listar(incluir_ocultos=_TESTES):
        if not _eh_cortes(canal_id):
            continue
        c = Canal.carregar(canal_id)
        info_yt = util.ler_json(c.pasta_credenciais / "canal_youtube.json", {}) or {}
        a = auto.get(canal_id, {})
        canais_info.append({
            "id": c.id, "nome": c.nome,
            "youtube": {"conectado": c.caminho_token_youtube.exists(), "nome": info_yt.get("titulo", ""),
                        "handle": info_yt.get("handle", ""), **_conexoes.get(c.id, {})},
            "automacao": {"ativa": bool(a.get("ativo")), "intervalo": c.automacao.get("intervalo_minutos", 60),
                          "proxima": a.get("proxima_execucao", "")},
            "revisao": bool(c.automacao.get("revisao_humana_obrigatoria", True)),
            "modelo": c.cfg.get("cortes", {}).get("modelo", "padrao"),
            "layout": c.cfg.get("cortes", {}).get("layout", "moldura"),
            "cortes_padrao": {k: c.cfg.get("cortes", {}).get(k) for k in ("max_cortes_por_live", "duracao_min",
                                                                         "duracao_max", "traduzir")},
            "horarios": c.publicacao.get("horarios_shorts", []), "dias": c.publicacao.get("dias_semana", []),
            "avatar": f"/canal/{c.id}/avatar?v={int((c.pasta / 'assets' / 'avatar.jpg').stat().st_mtime)}"
                      if (c.pasta / "assets" / "avatar.jpg").exists() else "",
            "trilhas": _trilhas_info(c),
            "fontes": [{"nome": f.nome, "url": f.url, "plataforma": f.plataforma, "tipo": f.tipo,
                        "autorizacao": f.autorizacao, "monitorar": f.monitorar} for f in fontes_mod.carregar(c)]})
    projetos_info = []
    for p in Projeto.listar():
        if _eh_cortes(p.canal_id) and p.estado != "descartado":
            projetos_info.append(_resumo_cortes(p))
    projetos_info.sort(key=lambda x: x["criado_em"], reverse=True)
    agora = dt.datetime.now(dt.timezone.utc)
    agenda = []
    for proj in projetos_info:
        for s in proj["shorts"]:
            yt = s.get("youtube") or {}
            try:
                quando = dt.datetime.fromisoformat(yt.get("publicar_em", ""))
            except ValueError:
                continue
            if quando > agora:
                agenda.append({"quando": yt["publicar_em"], "titulo": s["titulo"], "url": yt.get("url", ""),
                               "projeto": proj["id"], "indice": s["indice"]})
    agenda.sort(key=lambda x: x["quando"])
    meu_id = sessao.usuario_atual()["id"]
    envios = [_estado_envio(e) for e in sorted(_envios.values(), key=lambda e: e["inicio"], reverse=True)
              if e.get("usuario_id") == meu_id]
    return {"canais": canais_info, "envios": envios, "projetos": projetos_info, "agenda": agenda}


MAX_AUTORIZACAO = 300  # caracteres do texto "como o criador autorizou" guardado em fontes.yaml


def _url_do_pedido(corpo: dict) -> str:
    """Link da live: o colado pelo dono ou, para as lives do canal vinculado, o id do vídeo do YouTube."""
    url = str(corpo.get("url", "")).strip()
    video_id = str(corpo.get("id", "")).strip()
    if video_id:
        if not re.fullmatch(r"[\w-]{6,20}", video_id):
            raise HTTPException(400, {"codigo": "link_invalido", "mensagem": "Live inválida."})
        url = f"https://www.youtube.com/watch?v={video_id}"
    if not re.match(r"^https?://", url) or len(url) > 500:
        raise HTTPException(400, {"codigo": "link_invalido", "mensagem": "Cole o link completo da live (começando com https://)."})
    return url


def _verificar_link(c: Canal, url: str, autorizacao: str | None = None, trecho: str | None = None,
                   so_conferir: bool = False) -> dict:
    """verificar() da esteira com cada problema traduzido numa resposta clara para a tela."""
    from esteira.cortes import ingestao
    from esteira.cortes import orquestrador as cortes_orq

    try:
        return cortes_orq.verificar(c, url, autorizacao=autorizacao, trecho=trecho, so_conferir=so_conferir)
    except cortes_orq.FonteNaoAutorizada as exc:
        raise HTTPException(409, {"codigo": "fonte_nao_autorizada", "dono": getattr(exc, "dono", ""),
                                  "url_canal": getattr(exc, "url_canal", ""),
                                  "mensagem": f"{getattr(exc, 'dono', 'Esse canal')} ainda não está nas suas fontes."})
    except cortes_orq.LinkDeCanal as exc:
        raise HTTPException(400, {"codigo": "link_de_canal", "mensagem": str(exc), "url_canal": url})
    except cortes_orq.LiveRepetida as exc:
        raise HTTPException(409, {"codigo": "repetida", "mensagem": "Essa live já foi cortada. Os Shorts dela estão na lista abaixo.",
                                  "projeto": str(exc).rsplit(" ", 1)[-1].rstrip(".")})
    except cortes_orq.LiveEmAndamento as exc:
        raise HTTPException(409, {"codigo": "ao_vivo", "mensagem": str(exc)})
    except ingestao.ErroIngestao as exc:
        raise HTTPException(400, {"codigo": "nao_abriu", "mensagem": "Não consegui abrir esse link. Confira se é o "
                                  f"vídeo de uma live pública. Detalhe: {str(exc)[-220:]}"})
    except ValueError as exc:
        raise HTTPException(400, {"codigo": "link_invalido", "mensagem": "Esse link não é da Twitch, da Kick nem do "
                                  "YouTube. Cole o link do vídeo da live numa dessas plataformas."
                                  if "Plataforma" in str(exc) else str(exc)})


@app.post("/api/cortes/conferir")
def conferir_link(corpo: dict = Body(...)):
    """Lê o link colado (sem baixar nada) e diz de quem é a live. Com "autorizacao", cadastra o criador como fonte."""
    c = _canal_cortes(str(corpo.get("canal", "")))
    url = _url_do_pedido(corpo)
    autorizacao = str(corpo.get("autorizacao", "") or "").strip()[:MAX_AUTORIZACAO] or None
    chk = _verificar_link(c, url, autorizacao, so_conferir=True)
    dados = chk["dados"]
    from esteira.cortes import fontes as fontes_mod

    return {"ok": True, "no_ar": bool(chk.get("no_ar")), "url": dados.get("webpage_url") or url, "titulo": dados.get("title", ""),
            "dono": dados.get("channel") or dados.get("uploader") or "", "plataforma": fontes_mod.plataforma_da_url(url),
            "duracao": dados.get("duration"), "miniatura": dados.get("thumbnail") or ""}


@app.post("/api/cortes/processar")
def processar_live(corpo: dict = Body(...)):
    """Confere o link na hora (fonte autorizada, live repetida, live no ar) e manda para a equipe."""
    from esteira.cortes import orquestrador as cortes_orq

    c = _canal_cortes(str(corpo.get("canal", "")))
    url = _url_do_pedido(corpo)
    trecho = str(corpo.get("trecho", "") or "").strip()
    if trecho and not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}-\d{1,2}:\d{2}:\d{2}", trecho):
        raise HTTPException(400, {"codigo": "trecho", "mensagem": "Use o trecho no formato 00:30:00 até 01:10:00."})
    try:
        cortes_orq.validar_trecho(trecho)
    except ValueError as exc:
        raise HTTPException(400, {"codigo": "trecho", "mensagem": str(exc)})
    chk = _verificar_link(c, url, trecho=trecho or None)
    dados = chk["dados"]
    try:
        cortes_orq.validar_trecho(trecho, None if dados.get("is_live") and not chk.get("parte_gravada")
                                  else dados.get("duration"))
    except ValueError as exc:
        raise HTTPException(400, {"codigo": "trecho", "mensagem": str(exc)})
    opcoes = rotas_etapas.validar_opcoes(corpo.get("opcoes"))
    args = ["cortes", "processar", "--canal", c.id, "--url", url, "--sem-painel"] + (["--trecho", trecho] if trecho else [])
    if corpo.get("modo") == "etapas":  # para depois do download: o dono escolhe os momentos à mão (ou pede à IA)
        args += ["--ate", "ingestao"]
    if opcoes:
        args += ["--opcoes", json.dumps(opcoes)]
    envio = _iniciar_processo(c.id, args, url, titulo=dados.get("title", ""), dono=chk["dono"],
                              url_live=dados.get("webpage_url") or url)
    return {"ok": True, "envio": envio["id"], "titulo": dados.get("title", ""), "dono": chk["dono"]}


@app.post("/api/fontes")
def adicionar_fonte(corpo: dict = Body(...)):
    """Integra um canal de lives (Twitch, Kick ou YouTube): aceita o link do canal ou de uma live dele."""
    from esteira.cortes import fontes as fontes_mod
    from esteira.cortes import orquestrador as cortes_orq

    c = _canal_cortes(str(corpo.get("canal", "")))
    url = str(corpo.get("url", "")).strip()
    tipo = str(corpo.get("tipo", "")).strip()
    if len(url) > 500 or not re.match(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be|twitch\.tv|kick\.com)/", url):
        raise HTTPException(400, {"mensagem": "Use o link do canal na Twitch, na Kick ou no YouTube (ex.: twitch.tv/nome)."})
    if tipo not in ("proprio", "autorizado"):
        raise HTTPException(400, {"mensagem": "Diga se o canal é seu ou se o criador autorizou os cortes."})
    try:
        fontes_mod.plataforma_da_url(url)
        url_canal, dados = cortes_orq.pagina_do_canal(url)
        fonte = fontes_mod.adicionar(c, url_canal, tipo, autorizacao=str(corpo.get("autorizacao", ""))[:MAX_AUTORIZACAO],
                                     monitorar=bool(corpo.get("monitorar", False)), info=dados)
    except Exception as exc:
        raise HTTPException(400, {"mensagem": str(exc)[:300]})
    _cache_lives.pop((c.id, fonte.url), None)
    return {"ok": True, "fonte": {"nome": fonte.nome, "url": fonte.url, "tipo": fonte.tipo, "plataforma": fonte.plataforma,
                                  "monitorar": fonte.monitorar}}


@app.post("/api/fontes/remover")
def remover_fonte(corpo: dict = Body(...)):
    from esteira.cortes import fontes as fontes_mod

    c = _canal_cortes(str(corpo.get("canal", "")))
    url = str(corpo.get("url", ""))
    if not fontes_mod.remover(c, url):
        raise HTTPException(404, {"mensagem": "Fonte não encontrada."})
    _cache_lives.pop((c.id, url.rstrip("/")), None)
    return {"ok": True}


@app.post("/api/fontes/monitorar")
def vigiar_fonte(corpo: dict = Body(...)):
    """Liga/desliga a vigia automática (Modo automático) de um canal integrado."""
    from esteira.cortes import fontes as fontes_mod

    c = _canal_cortes(str(corpo.get("canal", "")))
    fonte = fontes_mod.definir_monitorar(c, str(corpo.get("url", "")), bool(corpo.get("monitorar")))
    if not fonte:
        raise HTTPException(404, {"mensagem": "Fonte não encontrada."})
    return {"ok": True, "fonte": {"nome": fonte.nome, "url": fonte.url, "monitorar": fonte.monitorar}}


@app.post("/api/canais/{canal}/automacao")
def alternar_automacao(canal: str, corpo: dict = Body(...)):
    from esteira import automacao

    c = _canal_cortes(canal)
    try:
        mensagem = automacao.ativar(c.id) if corpo.get("ativa") else automacao.pausar(c.id)
        _CACHE_AUTOMACAO.clear()
    except Exception as exc:
        raise HTTPException(500, {"mensagem": f"Não consegui mudar a vigilância: {exc}"})
    return {"ok": True, "mensagem": mensagem}


@app.post("/api/canais/{canal}/revisao")
def alternar_revisao(canal: str, corpo: dict = Body(...)):
    c = _canal_cortes(canal)
    caminho = c.pasta / "identidade.yaml"
    valor = "true" if corpo.get("obrigatoria") else "false"
    texto, n = re.subn(r"(?m)^(\s*revisao_humana_obrigatoria:\s*)(true|false)\b", lambda m: m.group(1) + valor,
                       caminho.read_text(encoding="utf-8"))
    if not n:
        raise HTTPException(500, {"mensagem": "Não achei a opção revisao_humana_obrigatoria no identidade.yaml."})
    caminho.write_text(texto, encoding="utf-8")
    return {"ok": True, "revisao": valor == "true"}


@app.post("/api/projetos/{canal}/{pasta}/tentar")
def tentar_de_novo(canal: str, pasta: str):
    p = _projeto(canal, pasta)
    _canal_cortes(canal)
    preso = p.estado == "em_producao" and not any(e["proc"].poll() is None for e in _envios.values()
                                                  if e.get("projeto") == p.id)
    if p.estado not in ("erro", "pendente_revisao", "reprovado", "aguardando_roteiro") and not preso:
        raise HTTPException(409, {"mensagem": "Este projeto não precisa ser refeito agora."})
    etapas = p.status.get("etapas", {}) or {}
    live = p.status.get("live", {}) or {}
    if not any(a.suffix not in (".part", ".ytdl") for a in p.p("fonte").glob("fonte.*")):  # download falhou: refaz
        orquestrador.descartar(p)
        args = ["cortes", "processar", "--canal", canal, "--url", live.get("url", ""), "--sem-painel", "--forcar"]
        if p.status.get("trecho"):
            args += ["--trecho", p.status["trecho"]]
    else:
        de = "edicao" if (etapas.get("analise") or {}).get("concluida") else "analise"
        args = ["cortes", "continuar", "--projeto", p.id, "--a-partir-de", de, "--sem-painel"]
    extra = {} if args[1] == "processar" else {"projeto": p.id}  # reprocessar cria um projeto novo
    envio = _iniciar_processo(canal, args, live.get("url", ""), titulo=live.get("titulo", ""),
                              dono=(p.status.get("fonte") or {}).get("nome", ""), **extra)
    return {"ok": True, "envio": envio["id"]}


@app.post("/api/projetos/{canal}/{pasta}/cancelar")
def cancelar_agendamento(canal: str, pasta: str):
    from esteira.modulos import m6_publicacao

    p = _projeto(canal, pasta)
    try:
        resultado = m6_publicacao.cancelar_agendamento(p, Canal.carregar(canal))
    except Exception as exc:
        raise HTTPException(409, {"mensagem": str(exc)[:300]})
    return {"ok": True, **resultado}


@app.post("/api/projetos/{canal}/{pasta}/descartar")
def descartar_projeto(canal: str, pasta: str):
    p = _projeto(canal, pasta)
    if p.estado in ("publicando", "agendado"):
        raise HTTPException(409, {"mensagem": "Cancele o agendamento antes de descartar."})
    orquestrador.descartar(p)
    return {"ok": True}


_cache_lives: dict[tuple[str, str], tuple[float, list]] = {}  # (canal, link da fonte) -> (quando, lives)
CACHE_LIVES_SEG = 600  # lista de lives de cada fonte guardada por 10 min (o botão "Procurar" força)
LIVES_POR_FONTE = 12
_duracoes: dict[tuple[str, float], int] = {}
LIMITE_TRILHA_MB = 60
_TRILHA_SEGURA = re.compile(r"[\w\-. ]{1,120}\.(mp3|wav|ogg|m4a|flac|opus)", re.I)


def _trilhas_info(c: Canal) -> list[dict]:
    from esteira import ffmpeg
    from esteira.cortes import edicao

    creditos = util.ler_json(c.pasta_trilhas / "creditos.json", {}) or {}

    def duracao(t: Path) -> int:
        chave = (str(t), t.stat().st_mtime)
        if chave not in _duracoes:
            _duracoes[chave] = round(ffmpeg.duracao(t) or 0)
        return _duracoes[chave]

    return [{"nome": t.name, "duracao": duracao(t), "url": f"/trilhas/{c.id}/{t.name}",
             "original": t.name.startswith("synthwave_"), "credito": creditos.get(t.name, "")}
            for t in edicao.trilhas_do_canal(c)]


def _link_da_live(live: dict, fonte) -> str:
    if live.get("url"):
        return live["url"]
    return f"https://www.youtube.com/watch?v={live['id']}" if fonte.plataforma == "youtube" else fonte.url


@app.get("/api/lives")
def lives_da_fonte(canal: str, fonte: str = "", atualizar: bool = False):
    """Lives recentes de um dos canais integrados (aba Canais), com quais já foram cortadas."""
    import time

    from esteira.cortes import fontes as fontes_mod
    from esteira.cortes import ingestao

    c = _canal_cortes(canal)
    f = fontes_mod.buscar(c, fonte) if fonte else None
    if not f:
        raise HTTPException(404, {"codigo": "fonte", "mensagem": "Esse canal não está integrado. Adicione-o na aba Canais."})
    chave = (c.id, f.url)
    agora = time.time()
    em_cache = _cache_lives.get(chave)
    if atualizar or not em_cache or agora - em_cache[0] > CACHE_LIVES_SEG:
        try:
            lista = ingestao.lives_recentes(f.url, f.plataforma, LIVES_POR_FONTE)
        except Exception as exc:
            if not em_cache:
                raise HTTPException(502, {"mensagem": f"Não consegui ler as lives de {f.nome} agora: {str(exc)[-200:]}"})
            lista = em_cache[1]
        _cache_lives[chave] = (agora, lista)
    projetos_por_live = {}
    for p in Projeto.listar(c.id):
        live_id = (p.status.get("live") or {}).get("id")
        if live_id and p.estado != "descartado":
            projetos_por_live[live_id] = {"id": p.id, "estado": p.estado}
    return [{**l, "url": _link_da_live(l, f), "plataforma": f.plataforma, "dono": f.nome,
             "no_ar": l.get("status") == "is_live", "projeto": projetos_por_live.get(l["id"])}
            for l in _cache_lives[chave][1]]


@app.get("/canal/{canal}/avatar")
def avatar_do_canal(canal: str):
    c = _canal_cortes(canal)
    arquivo = c.pasta / "assets" / "avatar.jpg"
    if not arquivo.exists():
        raise HTTPException(404, "Sem foto")
    return FileResponse(arquivo, media_type="image/jpeg")


@app.get("/trilhas/{canal}/{nome}")
def ouvir_trilha(canal: str, nome: str):
    c = _canal_cortes(canal)
    alvo = (c.pasta_trilhas / nome).resolve()
    if not _TRILHA_SEGURA.fullmatch(nome) or not alvo.is_relative_to(c.pasta_trilhas.resolve()) or not alvo.is_file():
        raise HTTPException(404, "Trilha não encontrada")
    return FileResponse(alvo, media_type=mimetypes.guess_type(alvo.name)[0] or "audio/mpeg")


@app.post("/api/canais/{canal}/trilhas")
async def enviar_trilha(canal: str, request: Request, nome: str, credito: str = ""):
    """Recebe o arquivo de música no corpo da requisição (sem multipart)."""
    c = _canal_cortes(canal)
    nome = re.sub(r"[^\w\-. ]", "_", Path(nome).name)[:120]
    if not _TRILHA_SEGURA.fullmatch(nome):
        raise HTTPException(400, {"mensagem": "Envie um arquivo de áudio MP3, WAV, OGG, M4A, FLAC ou OPUS."})
    limite = LIMITE_TRILHA_MB * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limite:
        raise HTTPException(413, {"mensagem": f"Arquivo maior que {LIMITE_TRILHA_MB} MB."})
    partes, total = [], 0
    async for pedaco in request.stream():  # lê em pedaços e para assim que passa do limite
        total += len(pedaco)
        if total > limite:
            raise HTTPException(413, {"mensagem": f"Arquivo maior que {LIMITE_TRILHA_MB} MB."})
        partes.append(pedaco)
    if not total:
        raise HTTPException(400, {"mensagem": "Arquivo vazio."})
    c.pasta_trilhas.mkdir(parents=True, exist_ok=True)
    temporario = c.pasta_trilhas / f"{nome}.tmp"
    temporario.write_bytes(b"".join(partes))
    try:
        from esteira import ffmpeg

        valido = ffmpeg.duracao(temporario) > 1
    except Exception:
        valido = False
    if not valido:
        temporario.unlink(missing_ok=True)
        raise HTTPException(400, {"mensagem": "Esse arquivo não parece ser uma música (não consegui ler o áudio)."})
    temporario.replace(c.pasta_trilhas / nome)
    if credito.strip():
        creditos = util.ler_json(c.pasta_trilhas / "creditos.json", {}) or {}
        creditos[nome] = credito.strip()[:300]
        util.salvar_json(c.pasta_trilhas / "creditos.json", creditos)
    return {"ok": True, "nome": nome}


@app.post("/api/canais/{canal}/trilhas/remover")
def remover_trilha(canal: str, corpo: dict = Body(...)):
    c = _canal_cortes(canal)
    nome = str(corpo.get("nome", ""))
    alvo = (c.pasta_trilhas / nome).resolve()
    if not _TRILHA_SEGURA.fullmatch(nome) or not alvo.is_relative_to(c.pasta_trilhas.resolve()) or not alvo.is_file():
        raise HTTPException(404, {"mensagem": "Trilha não encontrada."})
    alvo.unlink()
    return {"ok": True}


@app.post("/api/canais/{canal}/trilhas/gerar")
def gerar_trilhas(canal: str):
    from esteira.cortes import synthwave

    c = _canal_cortes(canal)
    criadas = synthwave.gerar_pacote(c.pasta_trilhas)
    return {"ok": True, "criadas": [t.name for t in criadas]}


@app.post("/api/projetos/{canal}/{pasta}/shorts/{indice}/refazer")
def refazer_short(canal: str, pasta: str, indice: int, corpo: dict = Body(default={})):
    """Refaz um Short só: novo gancho, ajuste de começo/fim (em segundos) e/ou outra música."""
    p = _projeto(canal, pasta)
    _canal_cortes(canal)
    if p.estado in ("publicando", "agendado", "aprovado"):
        raise HTTPException(409, {"mensagem": "Este Short já foi para o YouTube: cancele o agendamento antes."})
    if any(e.get("projeto") == p.id and e["proc"].poll() is None for e in _envios.values()):
        raise HTTPException(409, {"mensagem": "Já tem um Short desta live sendo refeito; espere terminar."})
    atual = next((s for s in (p.ler("shorts/shorts.json", []) or []) if int(s["indice"]) == int(indice)), None)
    if not atual:
        raise HTTPException(404, {"mensagem": "Short não encontrado."})
    args = ["cortes", "reeditar", "--projeto", p.id, "--indice", str(int(indice))]
    gancho = str(corpo.get("gancho", "") or "").strip()
    if gancho and gancho != atual.get("gancho"):
        args += ["--gancho", gancho[:80]]
    try:
        ajuste_ini = float(corpo.get("ajuste_inicio", 0) or 0)
        ajuste_fim = float(corpo.get("ajuste_fim", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, {"mensagem": "Ajuste de tempo inválido."})
    if ajuste_ini or ajuste_fim:
        inicio = float(atual["inicio"]) + ajuste_ini
        args += ["--inicio", f"{inicio:.2f}", "--fim", f"{float(atual['inicio']) + float(atual['duracao']) + ajuste_fim:.2f}"]
    trilha = str(corpo.get("trilha", "") or "")
    if trilha and trilha != atual.get("trilha"):
        if trilha != "nenhuma" and not _TRILHA_SEGURA.fullmatch(trilha):
            raise HTTPException(400, {"mensagem": "Trilha inválida."})
        args += ["--trilha", trilha]
    modelo = str(corpo.get("modelo", "") or "")
    if modelo and modelo != atual.get("modelo"):
        if modelo not in rotas_etapas.MODELOS:
            raise HTTPException(400, {"mensagem": "Modelo visual desconhecido."})
        args += ["--modelo", modelo]
    if len(args) == 6:
        raise HTTPException(400, {"mensagem": "Nada mudou neste Short."})
    live = p.status.get("live", {}) or {}
    envio = _iniciar_processo(canal, args, live.get("url", ""), titulo=f"Refazendo o Short {indice}",
                              projeto=p.id, indice=int(indice))
    return {"ok": True, "envio": envio["id"]}


# ------------------------------------------------------------------ login do YouTube
class _CapturaLink(webbrowser.BaseBrowser):
    """Guarda o link de autorização do Google para o painel mostrar como botão."""

    def __init__(self, canal: str):
        super().__init__(f"esteira-{canal}")
        self.canal = canal

    def open(self, url, new=0, autoraise=True):
        _conexoes.setdefault(self.canal, {})["url"] = url
        return True


@app.post("/api/canais/{canal}/youtube")
def conectar_youtube(canal: str):
    """Login do canal do YouTube (OAuth2): o painel recebe o link do Google e abre numa aba nova."""
    from esteira.modulos import m6_publicacao

    c = _canal_cortes(canal)
    if not m6_publicacao.caminho_segredo().exists():
        raise HTTPException(409, {"mensagem": "Falta o arquivo credenciais/client_secret.json (veja credenciais/LEIA-ME.md)."})
    if _conexoes.get(canal, {}).get("em_andamento"):
        return {"ok": True, "em_andamento": True}
    nome_navegador = f"esteira-{canal}"
    webbrowser.register(nome_navegador, None, _CapturaLink(canal))

    def fluxo():
        _conexoes[canal] = {"em_andamento": True, "erro": "", "url": ""}
        try:
            info = m6_publicacao.autenticar(c, abrir_navegador=True, navegador=nome_navegador, tempo_limite=900)
            info_yt = util.ler_json(c.pasta_credenciais / "canal_youtube.json", {}) or {}
            _conexoes[canal] = {"em_andamento": False, "erro": "", "url": "", "nome": info.get("titulo", ""),
                                "handle": info_yt.get("handle", "")}
        except Exception as exc:
            _conexoes[canal] = {"em_andamento": False, "erro": str(exc)[:300], "url": ""}

    threading.Thread(target=fluxo, daemon=True, name=f"youtube-{canal}").start()
    return {"ok": True, "em_andamento": True}


# ------------------------------------------------------------------ arquivos
@app.get("/arquivos/{canal}/{pasta}/{caminho:path}")
def arquivo(canal: str, pasta: str, caminho: str, request: Request):
    p = _projeto(canal, pasta)
    alvo = (p.pasta / caminho).resolve()
    if not alvo.is_relative_to(p.pasta) or not alvo.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    tamanho = alvo.stat().st_size
    tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
    if alvo.suffix.lower() == ".srt":
        tipo = "text/plain; charset=utf-8"
    faixa = request.headers.get("range")
    cabecalhos = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    m = re.match(r"bytes=(\d*)-(\d*)", faixa or "")
    if not m or (not m.group(1) and not m.group(2)):
        return FileResponse(alvo, media_type=tipo, headers=cabecalhos)
    if m.group(1):
        inicio = int(m.group(1))
        fim = int(m.group(2)) if m.group(2) else tamanho - 1
    else:  # sufixo: últimos N bytes
        inicio = max(0, tamanho - int(m.group(2)))
        fim = tamanho - 1
    fim = min(fim, tamanho - 1)
    if inicio > fim:
        raise HTTPException(416, "Faixa inválida")

    def ler():
        with open(alvo, "rb") as f:
            f.seek(inicio)
            restante = fim - inicio + 1
            while restante > 0:
                pedaco = f.read(min(1 << 20, restante))
                if not pedaco:
                    break
                restante -= len(pedaco)
                yield pedaco

    cabecalhos.update({"Content-Range": f"bytes {inicio}-{fim}/{tamanho}", "Content-Length": str(fim - inicio + 1)})
    return StreamingResponse(ler(), status_code=206, media_type=tipo, headers=cabecalhos)


def iniciar(porta: int | None = None, abrir_navegador: bool = True) -> None:
    import uvicorn

    porta = porta or env_int("PAINEL_PORTA", 8765)
    url = f"http://127.0.0.1:{porta}/"
    log.ok(f"Painel de revisão em {url} (Ctrl+C para encerrar)")
    if abrir_navegador:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")


if __name__ == "__main__":
    iniciar()
