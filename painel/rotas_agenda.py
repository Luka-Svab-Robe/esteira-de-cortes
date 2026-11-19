"""Aba "Agendados": gestão dos Shorts que já foram para o YouTube (agendados, publicados ou retirados) e dos que
estão prontos esperando o envio, com o dia previsto; tirar um Short da agenda; horários e dias de cada canal."""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Body, HTTPException

from esteira.canal import Canal
from esteira.projeto import Projeto

router = APIRouter()

ESPERANDO_ENVIO = ("aprovado", "publicando", "erro_publicacao")
MOTIVO_DONO = "você tirou da agenda"
DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
MAX_HORARIOS = 12
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@router.put("/api/canais/{canal}/agenda")
def salvar_agenda_do_canal(canal: str, corpo: dict = Body(...)):
    """Quando os Shorts aprovados do canal vão ao ar: horários e dias da semana (seção publicacao do identidade.yaml).
    Vale para os próximos envios; os já agendados no YouTube continuam como estão."""
    from painel.rotas_etapas import gravar_na_secao

    horarios = sorted({str(h).strip() for h in (corpo.get("horarios") or [])})
    escolhidos = {str(d).strip().lower() for d in (corpo.get("dias") or [])}
    dias = [d for d in DIAS_SEMANA if d in escolhidos]
    if not horarios or len(horarios) > MAX_HORARIOS or not all(_HORA.match(h) for h in horarios):
        raise HTTPException(400, {"mensagem": f"Coloque de 1 a {MAX_HORARIOS} horários no formato 20:00."})
    if not dias:
        raise HTTPException(400, {"mensagem": "Escolha pelo menos um dia da semana."})
    c = _srv()._canal_cortes(canal)
    caminho = c.pasta / "identidade.yaml"
    try:
        texto = gravar_na_secao(caminho.read_text(encoding="utf-8"), "publicacao",
                                {"horarios_shorts": horarios, "dias_semana": dias})
    except ValueError as exc:
        raise HTTPException(500, {"mensagem": str(exc)})
    caminho.write_text(texto, encoding="utf-8")
    Canal.carregar(canal)  # confere que o arquivo continua válido
    return {"ok": True, "horarios": horarios, "dias": dias}


def _srv():
    from painel import servidor  # import tardio: o servidor inclui este router

    return servidor


def _origem(p: Projeto) -> dict:
    st = p.status
    live = st.get("live") or {}
    return {"projeto": p.id, "live": live.get("titulo", ""), "dono": (st.get("fonte") or {}).get("nome") or live.get("canal", ""),
            "trecho": st.get("trecho"), "rodada": st.get("rodada") or 1, "estado_projeto": p.estado}


def _situacao(short: dict, agora: dt.datetime) -> str:
    if short.get("restricao"):
        return "retirado"
    try:
        return "publicado" if dt.datetime.fromisoformat(short.get("publicar_em", "")) <= agora else "agendado"
    except ValueError:
        return "agendado"


def _motivo_da_espera(p: Projeto) -> str:
    """Por que os Shorts aprovados ainda não subiram, em palavras simples."""
    if p.estado in ("aprovado", "publicando"):
        return "enviando ao YouTube agora"
    detalhe = str(p.status.get("detalhe_estado") or "")
    if "limite de envios" in detalhe or "quotaExceeded" in detalhe or "cota" in detalhe:
        return "o YouTube limita os envios por dia; sobe sozinho quando liberar"
    if "renovar o acesso" in detalhe or "Unable to find the server" in detalhe or "HTTPSConnectionPool" in detalhe:
        return "a internet caiu no envio; a esteira tenta de novo de hora em hora"
    return f"o envio falhou ({detalhe[:120]}); a esteira tenta de novo" if detalhe else "na fila de envio"


def _shorts_do_projeto(p: Projeto, agora: dt.datetime) -> tuple[list[dict], list[dict]]:
    """(os que já foram ao YouTube, os prontos esperando envio) de um projeto."""
    srv = _srv()
    meta = {int(s["indice"]): s for s in (p.ler("metadados.json", {}) or {}).get("shorts", [])}
    feitos = {int(s["indice"]): s for s in p.ler("shorts/shorts.json", []) or []}
    no_yt = {int(s["indice"]): s for s in (p.status.get("youtube") or {}).get("shorts", []) if s.get("id")}
    origem = _origem(p)

    def item(n: int) -> dict:
        texto, feito = meta.get(n, {}), feitos.get(n, {})
        return {**origem, "indice": n, "titulo": texto.get("titulo") or feito.get("titulo", ""),
                "video": srv._url(p, feito.get("arquivo")), "duracao": feito.get("duracao")}

    enviados = [{**item(n), "id": s["id"], "url": s.get("url", ""), "quando": s.get("publicar_em", ""),
                 "situacao": _situacao(s, agora), "motivo": s.get("restricao", "")} for n, s in sorted(no_yt.items())]
    espera = _motivo_da_espera(p)
    fila = [{**item(n), "espera": espera} for n, t in sorted(meta.items())
            if p.estado in ESPERANDO_ENVIO and t.get("selecionado") and n not in no_yt]
    return enviados, fila


def _previsoes(canal, ocupados: list[dt.datetime], quantidade: int) -> list[str]:
    """Dias em que os Shorts da fila devem sair, na ordem de envio (os horários livres do canal)."""
    from esteira.modulos import m6_publicacao as m6

    cfg = canal.publicacao
    apos = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=float(cfg.get("antecedencia_min_horas", 3)))
    try:
        return [q.isoformat() for q in m6.proximos_horarios(cfg["horarios_shorts"], cfg["dias_semana"], cfg["fuso_horario"],
                                                            ocupados, apos, quantidade, int(cfg.get("intervalo_min_minutos", 60)))]
    except Exception:  # sem horários configurados: a fila aparece sem data
        return [""] * quantidade


@router.get("/api/agenda")
def ver_agenda(canal: str):
    c = _srv()._canal_cortes(canal)
    agora = dt.datetime.now(dt.timezone.utc)
    enviados, fila = [], []
    projetos = sorted((p for p in Projeto.listar(c.id) if p.estado != "descartado"),
                      key=lambda p: p.status.get("criado_em", ""))
    for p in projetos:
        e, f = _shorts_do_projeto(p, agora)
        enviados += e
        fila += f
    ocupados = []
    for s in enviados:
        try:
            if s["situacao"] == "agendado":
                ocupados.append(dt.datetime.fromisoformat(s["quando"]))
        except ValueError:
            pass
    for s, quando in zip(fila, _previsoes(c, ocupados, len(fila)) if fila else []):
        s["previsto"] = quando
    enviados.sort(key=lambda s: s.get("quando") or "")
    return {"agendados": [s for s in enviados if s["situacao"] == "agendado"],
            "publicados": [s for s in enviados if s["situacao"] == "publicado"][::-1],
            "retirados": [s for s in enviados if s["situacao"] == "retirado"], "fila": fila,
            "horarios": c.publicacao.get("horarios_shorts", [])}


@router.post("/api/agenda/{canal}/{pasta}/{indice}/retirar")
def retirar(canal: str, pasta: str, indice: int):
    """Tira um Short da agenda: fica privado no YouTube (nada é apagado) e o horário dele é liberado."""
    from esteira.modulos import m6_publicacao as m6
    from esteira.modulos import restricoes_youtube as restricoes

    srv = _srv()
    p, c = srv._projeto(canal, pasta), srv._canal_cortes(canal)
    short = next((s for s in (p.status.get("youtube") or {}).get("shorts", [])
                  if int(s.get("indice", 0)) == indice and s.get("id") and not s.get("restricao")), None)
    if short is None:
        raise HTTPException(404, {"mensagem": "Este Short não está agendado."})
    try:
        youtube = m6.servico(c)
        itens = youtube.videos().list(part="status", id=short["id"]).execute().get("items", [])
        if itens:
            restricoes.tornar_privado(youtube, itens[0])
    except Exception as exc:
        raise HTTPException(502, {"mensagem": f"O YouTube não respondeu agora: {str(exc)[:200]}"}) from exc
    restricoes.registrar_retirada(p, c, indice, short["id"], MOTIVO_DONO)
    return {"ok": True}
