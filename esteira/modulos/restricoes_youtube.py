"""Nenhum Short com restrição vai ao ar: confere no YouTube cada Short já enviado e tira da agenda o que tiver.

Restrição = o YouTube recusou o envio (direitos autorais, termos, duplicado…), bloqueou o vídeo em algum país
(é o que o Content ID faz com música protegida), deixou só alguns países liberados, pôs restrição de idade ou o
vídeo sumiu. O Short fica PRIVADO (sem data de publicação: nada é apagado), sai da seleção no painel com o motivo
e o horário dele na agenda é liberado.

Os Shorts são enviados privados e agendados com algumas horas de antecedência, tempo em que o Content ID costuma
analisar o vídeo: a esteira confere antes de cada novo envio e de tempos em tempos (`youtube restricoes`).
"""
from __future__ import annotations

import datetime as dt

from .. import log, util
from ..canal import Canal
from ..historico import Historico
from ..projeto import Projeto

LOTE_API = 50  # ids por chamada de videos.list
RECUSADO = ("rejected", "failed", "deleted")
CAMPOS_STATUS = ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")


def motivo(item: dict | None) -> str | None:
    """Motivo da restrição de um vídeo (item de videos.list com status e contentDetails), ou None se está livre."""
    if item is None:
        return "o vídeo não foi encontrado no canal"
    status, detalhes = item.get("status") or {}, item.get("contentDetails") or {}
    if status.get("uploadStatus") in RECUSADO:
        razao = status.get("rejectionReason") or status.get("failureReason") or status["uploadStatus"]
        return f"o YouTube recusou o vídeo ({razao})"
    regiao = detalhes.get("regionRestriction") or {}
    if regiao.get("blocked"):
        return f"bloqueado em {len(regiao['blocked'])} país(es) — em geral direitos autorais (Content ID)"
    if "allowed" in regiao:
        return f"liberado só em {len(regiao['allowed'])} país(es) — em geral direitos autorais (Content ID)"
    if (detalhes.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted":
        return "restrição de idade"
    return None


def _a_conferir(canal: Canal) -> list[tuple[Projeto, dict]]:
    """(projeto, short enviado) de todos os Shorts deste canal que estão no YouTube e ainda não foram retirados."""
    saida = []
    for projeto in Projeto.listar(canal.id):
        for short in (projeto.status.get("youtube") or {}).get("shorts", []):
            if short.get("id") and not short.get("restricao"):
                saida.append((projeto, short))
    return saida


def tornar_privado(youtube, item: dict) -> None:
    novo = {k: v for k, v in (item.get("status") or {}).items() if k in CAMPOS_STATUS}
    novo["privacyStatus"] = "private"  # sem publishAt = sai da agenda
    youtube.videos().update(part="status", body={"id": item["id"], "status": novo}).execute()


def registrar_retirada(projeto: Projeto, canal: Canal, indice: int, video_id: str, razao: str) -> None:
    status_yt = projeto.status.get("youtube") or {}
    shorts = [{**s, "restricao": razao, "retirado_em": util.agora_iso()} if s.get("id") == video_id else s
              for s in status_yt.get("shorts", [])]
    projeto.atualizar_status(youtube={**status_yt, "shorts": shorts})
    meta = projeto.ler("metadados.json", {}) or {}
    if meta.get("shorts"):
        projeto.salvar("metadados.json", {**meta, "shorts": [
            {**s, "selecionado": False, "restricao": razao} if int(s.get("indice", 0)) == indice else s
            for s in meta["shorts"]]})
    projeto.alertar("publicacao", f"Short {indice} retirado da agenda (ficou privado): {razao}.")
    Historico(canal.caminho_db).cancelar_agendamento_video(video_id)


def conferir(canal: Canal, youtube=None) -> list[dict]:
    """Confere todos os Shorts enviados deste canal; os com restrição ficam privados e fora da agenda.
    Devolve [{projeto, indice, id, motivo}] dos retirados agora."""
    pendentes = _a_conferir(canal)
    if not pendentes:
        return []
    if youtube is None:
        from .m6_publicacao import servico

        youtube = servico(canal)
    itens: dict[str, dict] = {}
    ids = [short["id"] for _, short in pendentes]
    for n in range(0, len(ids), LOTE_API):
        resposta = youtube.videos().list(part="status,contentDetails", id=",".join(ids[n:n + LOTE_API])).execute()
        itens.update({i["id"]: i for i in resposta.get("items", [])})
    retirados = []
    for projeto, short in pendentes:
        item = itens.get(short["id"])
        razao = motivo(item)
        if not razao:
            continue
        if item is not None:
            tornar_privado(youtube, item)
        registrar_retirada(projeto, canal, int(short["indice"]), short["id"], razao)
        log.aviso(f"Short {short['indice']} de {projeto.id} retirado da agenda: {razao}")
        retirados.append({"projeto": projeto.id, "indice": short["indice"], "id": short["id"], "motivo": razao})
    if not retirados:
        log.ok(f"{len(pendentes)} Short(s) conferido(s) no YouTube: nenhum com restrição")
    return retirados


def proximo_a_publicar(canal: Canal) -> dt.datetime | None:
    """Quando sai o próximo Short agendado (para saber se ainda vale continuar vigiando)."""
    agora = dt.datetime.now(dt.timezone.utc)
    datas = []
    for _, short in _a_conferir(canal):
        try:
            quando = dt.datetime.fromisoformat(short.get("publicar_em", ""))
        except ValueError:
            continue
        if quando > agora:
            datas.append(quando)
    return min(datas) if datas else None
