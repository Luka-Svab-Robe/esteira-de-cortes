"""Módulo 6 — Publicação oficial via YouTube Data API v3 (OAuth2) com agendamento automático."""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import log, metadados, util
from ..canal import Canal
from ..cortes import hashtags as hashtags_cortes
from ..config import caminho_env
from ..historico import Historico
from ..projeto import Projeto

ESCOPOS = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
DIAS = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "sáb": 5, "dom": 6}
REPETIVEIS = (500, 502, 503, 504)


class ErroPublicacao(RuntimeError):
    pass


# ---------------------------------------------------------------- OAuth2
def caminho_segredo() -> Path:
    return caminho_env("YOUTUBE_CLIENT_SECRETS", "credenciais/client_secret.json")


def _salvar_token(caminho: Path, conteudo: str) -> None:
    """Grava o token do YouTube legível só pelo dono (dá acesso ao canal)."""
    caminho.write_text(conteudo, encoding="utf-8")
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass


def autenticar(canal: Canal, abrir_navegador: bool = True, navegador: str | None = None,
               tempo_limite: int | None = None) -> dict:
    segredo = caminho_segredo()
    if not segredo.exists():
        raise ErroPublicacao(
            f"Arquivo de credenciais OAuth não encontrado em {segredo}. No Google Cloud Console: ative a "
            "'YouTube Data API v3', crie um 'ID do cliente OAuth' do tipo 'App para computador', baixe o JSON e "
            "salve nesse caminho (veja credenciais/LEIA-ME.md).")
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    fluxo = InstalledAppFlow.from_client_secrets_file(str(segredo), ESCOPOS)
    credenciais = fluxo.run_local_server(
        port=0, open_browser=abrir_navegador,
        authorization_prompt_message="Abra este endereço no navegador e escolha a conta do canal: {url}",
        success_message="Autorização concluída! Pode fechar esta aba e voltar à esteira.",
        browser=navegador, timeout_seconds=tempo_limite,
        prompt="consent")  # garante o refresh_token mesmo se a conta já tiver autorizado antes
    if not getattr(credenciais, "token", None):
        raise ErroPublicacao("A autorização não foi concluída a tempo. Tente conectar de novo.")
    canal.pasta_credenciais.mkdir(parents=True, exist_ok=True)
    _salvar_token(canal.caminho_token_youtube, credenciais.to_json())
    youtube = build("youtube", "v3", credentials=credenciais, cache_discovery=False)
    return atualizar_perfil(canal, youtube)


def atualizar_perfil(canal: Canal, youtube=None) -> dict:
    """Lê nome, @ e foto do canal no YouTube; a foto vai para canais/<c>/assets/avatar.jpg (usada no modelo dos Shorts)."""
    import os

    import requests

    youtube = youtube or servico(canal)
    itens = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not itens:
        return {}
    snippet = itens[0]["snippet"]
    info = {"id": itens[0]["id"], "titulo": snippet.get("title", ""), "handle": snippet.get("customUrl", "")}
    if info["handle"] and not info["handle"].startswith("@"):
        info["handle"] = "@" + info["handle"]
    miniaturas = snippet.get("thumbnails", {})
    url_foto = next((miniaturas[k]["url"] for k in ("high", "medium", "default") if k in miniaturas), "")
    if url_foto:
        try:
            url_foto = re.sub(r"=s\d+-", "=s800-", url_foto)  # versão maior da foto
            resposta = requests.get(url_foto, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resposta.raise_for_status()
            destino = canal.pasta / "assets" / "avatar.jpg"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(resposta.content)
            info["avatar"] = str(destino.relative_to(canal.pasta))
        except Exception as exc:
            log.aviso(f"Não consegui baixar a foto do canal: {exc}")
    anterior = util.ler_json(canal.pasta_credenciais / "canal_youtube.json", {}) or {}
    util.salvar_json(canal.pasta_credenciais / "canal_youtube.json",
                     {**anterior, **info, "autorizado_em": anterior.get("autorizado_em") or util.agora_iso(),
                      "perfil_atualizado_em": util.agora_iso()})
    try:
        os.chmod(canal.pasta_credenciais / "canal_youtube.json", 0o600)
    except OSError:
        pass
    return info


def _credenciais(canal: Canal):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = canal.caminho_token_youtube
    if not token.exists():
        raise ErroPublicacao(f"O canal '{canal.id}' ainda não foi conectado ao YouTube. Conecte no painel "
                             f"(Agendados → Seus canais de publicação → Conectar YouTube) ou rode: "
                             f"python esteira.py youtube autenticar --canal {canal.id}")
    credenciais = Credentials.from_authorized_user_file(str(token), ESCOPOS)
    if not credenciais.valid:
        if credenciais.expired and credenciais.refresh_token:
            try:
                credenciais.refresh(Request())
            except Exception as exc:
                raise ErroPublicacao(f"Não foi possível renovar o acesso ao YouTube ({exc}). "
                                     f"Rode novamente: python esteira.py youtube autenticar --canal {canal.id}") from exc
            _salvar_token(token, credenciais.to_json())
        else:
            raise ErroPublicacao(f"Autorização do YouTube inválida. Rode: python esteira.py youtube autenticar --canal {canal.id}")
    return credenciais


def servico(canal: Canal):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credenciais(canal), cache_discovery=False)


def _mensagem_http(erro) -> str:
    try:
        corpo = json.loads(erro.content.decode("utf-8"))
        detalhe = corpo.get("error", {})
        motivo = (detalhe.get("errors") or [{}])[0].get("reason", "")
        mensagem = detalhe.get("message", "")
    except Exception:
        motivo, mensagem = "", str(erro)
    explicacoes = {
        "quotaExceeded": "A cota diária da YouTube Data API acabou. Tente amanhã ou peça aumento de cota no Google Cloud.",
        "uploadLimitExceeded": "O canal atingiu o limite de envios do dia.",
        "forbidden": "A conta autorizada não tem permissão neste canal.",
        "invalidPublishAt": "Data de publicação inválida (precisa ser futura).",
        "youtubeSignupRequired": "A conta Google autorizada não tem canal no YouTube.",
    }
    return explicacoes.get(motivo, f"YouTube recusou a operação ({motivo or erro.resp.status}): {mensagem}")


# ------------------------------------------------------------- agenda
def _parse_data(texto: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def agenda_youtube(youtube) -> list[dt.datetime]:
    """Horários já agendados (vídeos privados com publishAt futuro) no próprio canal."""
    canais = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if not canais:
        return []
    uploads = canais[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, pagina = [], None
    for _ in range(4):  # até os 200 envios mais recentes
        resposta = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                                pageToken=pagina).execute()
        ids += [item["contentDetails"]["videoId"] for item in resposta.get("items", [])]
        pagina = resposta.get("nextPageToken")
        if not pagina:
            break
    agora = dt.datetime.now(dt.timezone.utc)
    ocupados = []
    for lote in util.lotes(ids, 50):
        for video in youtube.videos().list(part="status", id=",".join(lote)).execute().get("items", []):
            quando = _parse_data(video.get("status", {}).get("publishAt", ""))
            if quando and quando > agora:
                ocupados.append(quando)
    return ocupados


def proximos_horarios(horarios: list[str], dias: list[str], fuso: str, ocupados: list[dt.datetime],
                      apos: dt.datetime, quantidade: int = 1, intervalo_min: int = 60) -> list[dt.datetime]:
    zona = ZoneInfo(fuso)
    dias_validos = {DIAS.get(str(d).lower()[:3], -1) for d in dias} or set(range(7))
    horas = sorted({tuple(int(x) for x in h.split(":")[:2]) for h in horarios})
    if not horas:
        raise ErroPublicacao("Nenhum horário de publicação configurado no canal.")
    margem = dt.timedelta(minutes=intervalo_min)
    ocupados = list(ocupados)
    resultado = []
    data = apos.astimezone(zona).date()
    for _ in range(400):
        if data.weekday() in dias_validos:
            for hora, minuto in horas:
                candidato = dt.datetime(data.year, data.month, data.day, hora, minuto, tzinfo=zona)
                if candidato <= apos:
                    continue
                if all(abs(candidato - o) >= margem for o in ocupados):
                    resultado.append(candidato)
                    ocupados.append(candidato)
                    if len(resultado) == quantidade:
                        return resultado
        data += dt.timedelta(days=1)
    raise ErroPublicacao("Não foi possível encontrar horários livres nos próximos 400 dias.")


def _iso_utc(quando: dt.datetime) -> str:
    return quando.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------- envio
def _enviar_video(youtube, arquivo: Path, corpo: dict, rotulo: str) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    midia = MediaFileUpload(str(arquivo), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    pedido = youtube.videos().insert(part="snippet,status", body=corpo, media_body=midia)
    resposta, erros, ultimo = None, 0, -10
    while resposta is None:
        try:
            estado, resposta = pedido.next_chunk()
            if estado:
                progresso = int(estado.progress() * 100)
                if progresso - ultimo >= 10:
                    log.info(f"{rotulo}: {progresso}% enviado")
                    ultimo = progresso
            continue
        except HttpError as exc:
            if exc.resp.status in REPETIVEIS:
                erros += 1
            elif exc.resp.status == 400 and b"containsSyntheticMedia" in (exc.content or b"") \
                    and "containsSyntheticMedia" in corpo.get("status", {}):
                corpo["status"].pop("containsSyntheticMedia")
                log.aviso("A API não aceitou o campo de mídia sintética; enviando sem ele.")
                return _enviar_video(youtube, arquivo, corpo, rotulo)
            else:
                raise ErroPublicacao(_mensagem_http(exc)) from exc
        except (OSError, ConnectionError) as exc:
            erros += 1
            log.aviso(f"{rotulo}: falha de rede ({exc}); tentando novamente…")
        if erros > 8:
            raise ErroPublicacao(f"{rotulo}: envio falhou depois de várias tentativas.")
        time.sleep(min(60, 2 ** erros))
    return resposta["id"]


def _definir_thumbnail(youtube, video_id: str, arquivo: Path) -> str | None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(arquivo), mimetype="image/jpeg")).execute()
        return None
    except HttpError as exc:
        return "Capa não enviada (o canal precisa estar verificado por telefone para capas personalizadas): " + _mensagem_http(exc)


def _enviar_legenda(youtube, video_id: str, arquivo: Path) -> str | None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        youtube.captions().insert(part="snippet", body={"snippet": {"videoId": video_id, "language": "pt-BR",
                                                                   "name": "Português", "isDraft": False}},
                                  media_body=MediaFileUpload(str(arquivo), mimetype="application/octet-stream")).execute()
        return None
    except HttpError as exc:
        return "Legenda não enviada: " + _mensagem_http(exc)


def _corpo(titulo: str, descricao: str, tags: list[str], categoria, publicar_em: dt.datetime, cfg: dict) -> dict:
    status = {"privacyStatus": "private", "publishAt": _iso_utc(publicar_em),
              "selfDeclaredMadeForKids": bool(cfg.get("feito_para_criancas", False)),
              "license": cfg.get("licenca", "youtube"), "embeddable": True}
    if cfg.get("declarar_conteudo_sintetico", True):
        status["containsSyntheticMedia"] = True
    return {"snippet": {"title": titulo[:100], "description": descricao[:5000], "tags": tags,
                        "categoryId": str(categoria), "defaultLanguage": "pt-BR", "defaultAudioLanguage": "pt-BR"},
            "status": status}


def planejar(projeto: Projeto, canal: Canal, youtube=None) -> dict:
    cfg = canal.publicacao
    meta = projeto.ler("metadados.json", {}) or {}
    historico = Historico(canal.caminho_db)
    ocupados = [a["quando"] for a in historico.agendamentos_futuros()]
    if youtube is not None:
        try:
            ocupados += agenda_youtube(youtube)
        except Exception as exc:
            log.aviso(f"Não foi possível ler a agenda do YouTube ({exc}); usando só a agenda local.")
    apos = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=float(cfg.get("antecedencia_min_horas", 3)))
    intervalo = int(cfg.get("intervalo_min_minutos", 60))
    video = proximos_horarios(cfg["horarios_video"], cfg["dias_semana"], cfg["fuso_horario"], ocupados, apos, 1, intervalo)[0]
    shorts_sel = [s for s in meta.get("shorts", []) if s.get("selecionado")] if cfg.get("publicar_shorts", True) else []
    horarios_shorts = proximos_horarios(cfg["horarios_shorts"], cfg["dias_semana"], cfg["fuso_horario"],
                                        ocupados + [video], video, len(shorts_sel), intervalo) if shorts_sel else []
    return {"video": video, "shorts": list(zip(shorts_sel, horarios_shorts)), "ocupados": ocupados}


def cancelar_agendamento(projeto: Projeto, canal: Canal) -> dict:
    """Tira do agendamento os vídeos/Shorts já enviados de um projeto: ficam PRIVADOS no canal (nada é apagado).

    O projeto volta para "pendente_revisao" sem IDs do YouTube, então uma nova aprovação envia de novo
    (para o canal que estiver conectado). Os IDs antigos ficam em status.json -> youtube_cancelado.
    """
    status_yt = projeto.status.get("youtube", {}) or {}
    ids = ([status_yt["video"]["id"]] if status_yt.get("video", {}).get("id") else []) + \
          [s["id"] for s in status_yt.get("shorts", []) if s.get("id")]
    if not ids:
        raise ErroPublicacao("Este projeto não tem vídeos agendados no YouTube.")
    youtube = servico(canal)
    atuais = {i["id"]: i["status"] for i in youtube.videos().list(part="status", id=",".join(ids)).execute().get("items", [])}
    resultado = {"privados": [], "nao_encontrados": []}
    for vid in ids:
        if vid not in atuais:
            resultado["nao_encontrados"].append(vid)
            continue
        novo = {k: v for k, v in atuais[vid].items()
                if k in ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")}
        novo["privacyStatus"] = "private"  # sem publishAt = sai da agenda
        youtube.videos().update(part="status", body={"id": vid, "status": novo}).execute()
        resultado["privados"].append(vid)
        log.ok(f"{vid}: fora da agenda (privado)")
    historico = Historico(canal.caminho_db)
    historico.cancelar_agendamentos(projeto.id)
    historico.atualizar(projeto.id, estado="pendente_revisao")
    projeto.atualizar_status(youtube={}, youtube_cancelado={**status_yt, "cancelado_em": util.agora_iso(), **resultado})
    projeto.definir_estado("pendente_revisao", f"agendamento cancelado; {len(resultado['privados'])} envio(s) "
                                               "ficaram privados no YouTube")
    return resultado


def publicar(projeto: Projeto, canal: Canal, simular: bool = False, forcar: bool = False) -> dict:
    log.etapa(f"Módulo 6 — Publicação no YouTube ({'simulação' if simular else 'envio real'})")
    if not simular and not forcar and projeto.estado not in ("aprovado", "erro_publicacao", "publicando"):
        raise ErroPublicacao(f"O projeto está '{projeto.estado}'. Só projetos aprovados no painel são publicados.")
    if projeto.status.get("tipo") == "cortes":
        return publicar_cortes(projeto, canal, simular)
    if not projeto.video_final.exists():
        raise ErroPublicacao("Vídeo final não encontrado.")
    meta = metadados.atualizar(projeto, canal)
    if simular:
        youtube = servico(canal) if canal.caminho_token_youtube.exists() else None
        plano = planejar(projeto, canal, youtube)
        fuso = ZoneInfo(canal.publicacao["fuso_horario"])
        log.ok(f"Vídeo principal seria publicado em {plano['video'].astimezone(fuso):%d/%m/%Y %H:%M}")
        for short, quando in plano["shorts"]:
            log.ok(f"Short {short['indice']} seria publicado em {quando.astimezone(fuso):%d/%m/%Y %H:%M}")
        return {"simulado": True, "video": plano["video"].isoformat(),
                "shorts": [{"indice": s["indice"], "publicar_em": q.isoformat()} for s, q in plano["shorts"]]}

    projeto.definir_estado("publicando")
    status_yt = projeto.status.get("youtube", {}) or {}
    historico = Historico(canal.caminho_db)
    cfg = canal.publicacao
    avisos = []
    try:
        youtube = servico(canal)  # dentro do try: sem token, o estado vira "erro_publicacao" (não fica preso)
        plano = planejar(projeto, canal, youtube)
        if status_yt.get("video", {}).get("id"):
            video_id = status_yt["video"]["id"]
            publicar_video = _parse_data(status_yt["video"]["publicar_em"]) or plano["video"]
            log.info(f"Vídeo principal já enviado anteriormente ({video_id}); continuando com os Shorts.")
        else:
            publicar_video = plano["video"]
            corpo = _corpo(meta["titulo"], meta["descricao_final"], meta.get("tags", []), meta.get("categoria", 27),
                           publicar_video, cfg)
            video_id = _enviar_video(youtube, projeto.video_final, corpo, "Vídeo principal")
            status_yt["video"] = {"id": video_id, "publicar_em": publicar_video.isoformat(),
                                  "url": f"https://youtu.be/{video_id}"}
            projeto.atualizar_status(youtube=status_yt)
            historico.registrar_agendamento(projeto.id, "video", video_id, meta["titulo"], _iso_utc(publicar_video))
            escolhida = int(meta.get("thumbnail_escolhida", 1))
            capa = projeto.p("thumbnails", f"thumb_{escolhida}.jpg")
            if capa.exists():
                aviso = _definir_thumbnail(youtube, video_id, capa)
                if aviso:
                    avisos.append(aviso)
            legenda = projeto.p("legendas", "video.srt")
            if cfg.get("enviar_legendas", True) and legenda.exists():
                aviso = _enviar_legenda(youtube, video_id, legenda)
                if aviso:
                    avisos.append(aviso)
        enviados = {s["indice"] for s in status_yt.get("shorts", [])}
        status_yt.setdefault("shorts", [])
        # recalcula a partir do horário REAL do vídeo principal (importa em reenvios parciais)
        horarios = proximos_horarios(cfg["horarios_shorts"], cfg["dias_semana"], cfg["fuso_horario"],
                                     plano["ocupados"] + [publicar_video], publicar_video, len(plano["shorts"]),
                                     int(cfg.get("intervalo_min_minutos", 60))) if plano["shorts"] else []
        for (short, _), quando in zip(plano["shorts"], horarios):
            if short["indice"] in enviados:
                continue
            arquivo = projeto.p("shorts", f"short_{short['indice']}.mp4")
            if not arquivo.exists():
                continue
            descricao = short.get("descricao", "").replace("{link_video}", f"https://youtu.be/{video_id}")
            corpo = _corpo(short.get("titulo", meta["titulo"]), descricao, meta.get("tags", [])[:10],
                           meta.get("categoria", 27), quando, cfg)
            sid = _enviar_video(youtube, arquivo, corpo, f"Short {short['indice']}")
            status_yt["shorts"].append({"indice": short["indice"], "id": sid, "publicar_em": quando.isoformat(),
                                        "url": f"https://youtube.com/shorts/{sid}"})
            projeto.atualizar_status(youtube=status_yt)
            historico.registrar_agendamento(projeto.id, "short", sid, short.get("titulo", ""), _iso_utc(quando))
    except ErroPublicacao as exc:
        projeto.atualizar_status(youtube=status_yt)
        projeto.definir_estado("erro_publicacao", str(exc))
        raise
    except Exception as exc:
        projeto.atualizar_status(youtube=status_yt)
        projeto.definir_estado("erro_publicacao", str(exc))
        raise ErroPublicacao(f"Falha inesperada na publicação: {exc}") from exc
    for aviso in avisos:
        projeto.alertar("publicacao", aviso)
        log.aviso(aviso)
    historico.atualizar(projeto.id, estado="agendado", youtube_id=video_id, publicar_em=_iso_utc(publicar_video),
                        titulo=meta["titulo"])
    projeto.marcar_etapa("publicacao", True, video=video_id, shorts=len(status_yt.get("shorts", [])))
    projeto.definir_estado("agendado", f"publica em {publicar_video.astimezone(ZoneInfo(cfg['fuso_horario'])):%d/%m %H:%M}")
    log.ok(f"Agendado: https://youtu.be/{video_id} para {publicar_video.astimezone(ZoneInfo(cfg['fuso_horario'])):%d/%m/%Y %H:%M}")
    return status_yt


def reagendar_cortes(canal: Canal, youtube=None) -> list[dict]:
    """Espalha de novo, na ordem, os Shorts já agendados (ainda não publicados) pelos horários atuais do canal —
    ex.: depois de passar para um Short por dia. Só muda a data no YouTube (nada é reenviado)."""
    cfg = canal.publicacao
    agora = dt.datetime.now(dt.timezone.utc)
    fila = []
    for projeto in Projeto.listar(canal.id):
        for short in (projeto.status.get("youtube") or {}).get("shorts", []):
            try:
                quando = dt.datetime.fromisoformat(short.get("publicar_em", ""))
            except ValueError:
                continue
            if short.get("id") and not short.get("restricao") and quando > agora + dt.timedelta(minutes=15):
                fila.append((quando, projeto, short))
    if not fila:
        return []
    fila.sort(key=lambda x: x[0])
    apos = fila[0][0] - dt.timedelta(seconds=1)  # a partir do primeiro já agendado: nada é adiantado
    novos = proximos_horarios(cfg["horarios_shorts"], cfg["dias_semana"], cfg["fuso_horario"], [], apos, len(fila),
                              int(cfg.get("intervalo_min_minutos", 60)))
    youtube = youtube or servico(canal)
    mudar = [(velho, novo, projeto, short) for (velho, projeto, short), novo in zip(fila, novos) if velho != novo]
    atuais = {}
    ids = [short["id"] for *_, short in mudar]
    for n in range(0, len(ids), 50):
        resposta = youtube.videos().list(part="status", id=",".join(ids[n:n + 50])).execute()
        atuais.update({i["id"]: i["status"] for i in resposta.get("items", [])})
    historico, fuso, feitos = Historico(canal.caminho_db), ZoneInfo(cfg["fuso_horario"]), []
    for velho, novo, projeto, short in mudar:
        if short["id"] not in atuais:
            log.aviso(f"Short {short['indice']} de {projeto.id}: não achei no YouTube; ficou como estava.")
            continue
        status = {k: v for k, v in atuais[short["id"]].items()
                  if k in ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")}
        youtube.videos().update(part="status", body={"id": short["id"], "status": {
            **status, "privacyStatus": "private", "publishAt": _iso_utc(novo)}}).execute()
        status_yt = projeto.status.get("youtube") or {}
        projeto.atualizar_status(youtube={**status_yt, "shorts": [
            {**s, "publicar_em": novo.isoformat()} if s.get("id") == short["id"] else s for s in status_yt.get("shorts", [])]})
        historico.reagendar_video(short["id"], _iso_utc(novo))
        log.ok(f"Short {short['indice']} ({projeto.id[-5:]}): {velho.astimezone(fuso):%d/%m %H:%M} → {novo.astimezone(fuso):%d/%m %H:%M}")
        feitos.append({"projeto": projeto.id, "indice": short["indice"], "de": velho.isoformat(), "para": novo.isoformat()})
    return feitos


def _sem_shorts_repetidos(projeto: Projeto, meta: dict, selecionados: list[dict], simular: bool) -> list[dict]:
    """Não agenda o Short que repete o trecho de outro (deste projeto ou já enviado por outro da mesma live):
    ele sai da seleção no painel, com um alerta dizendo o porquê."""
    from ..cortes import orquestrador as cortes_orq

    ficam, tirados = cortes_orq.shorts_sem_repeticao(projeto, selecionados)
    if not tirados:
        return selecionados
    numeros = ", ".join(str(s["indice"]) for s in tirados)
    log.aviso(f"Short(s) {numeros} fora do agendamento: mesmo trecho da live de outro Short.")
    if not simular:
        repetidos = {int(s["indice"]) for s in tirados}
        projeto.salvar("metadados.json", {**meta, "shorts": [
            {**s, "selecionado": False, "repetido": True} if int(s["indice"]) in repetidos else s
            for s in meta.get("shorts", [])]})
        projeto.alertar("publicacao", f"Short(s) {numeros} não foram agendados: repetem o trecho de outro Short.")
    if not ficam:
        raise ErroPublicacao("Todos os Shorts selecionados repetem trechos que já foram para o YouTube.")
    return ficam


def publicar_cortes(projeto: Projeto, canal: Canal, simular: bool = False) -> dict:
    """Canal de cortes: publica só os Shorts selecionados no painel, cada um com seus próprios textos."""
    cfg = canal.publicacao
    meta = projeto.ler("metadados.json", {}) or {}
    selecionados = [s for s in meta.get("shorts", []) if s.get("selecionado")]
    if not selecionados:
        raise ErroPublicacao("Nenhum Short selecionado para publicar.")
    selecionados = _sem_shorts_repetidos(projeto, meta, selecionados, simular)
    historico = Historico(canal.caminho_db)
    youtube = servico(canal) if (not simular or canal.caminho_token_youtube.exists()) else None
    if youtube is not None and not simular:  # antes de mandar mais, tira da agenda o que o YouTube restringiu
        from . import restricoes_youtube

        try:
            restricoes_youtube.conferir(canal, youtube)
        except Exception as exc:  # a checagem não impede o envio; ela roda de novo depois
            log.aviso(f"Não consegui conferir as restrições no YouTube agora ({exc}).")
    ocupados = [a["quando"] for a in historico.agendamentos_futuros()]
    if youtube is not None:
        try:
            ocupados += agenda_youtube(youtube)
        except Exception as exc:
            log.aviso(f"Não foi possível ler a agenda do YouTube ({exc}); usando só a agenda local.")
    apos = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=float(cfg.get("antecedencia_min_horas", 3)))
    horarios = proximos_horarios(cfg["horarios_shorts"], cfg["dias_semana"], cfg["fuso_horario"], ocupados, apos,
                                 len(selecionados), int(cfg.get("intervalo_min_minutos", 60)))
    fuso = ZoneInfo(cfg["fuso_horario"])
    if simular:
        for short, quando in zip(selecionados, horarios):
            log.ok(f"Short {short['indice']} seria publicado em {quando.astimezone(fuso):%d/%m/%Y %H:%M}: {short['titulo']}")
        return {"simulado": True, "shorts": [{"indice": s["indice"], "publicar_em": q.isoformat()}
                                             for s, q in zip(selecionados, horarios)]}
    projeto.definir_estado("publicando")
    status_yt = projeto.status.get("youtube", {}) or {}
    status_yt.setdefault("shorts", [])
    enviados = {s["indice"] for s in status_yt["shorts"]}
    try:
        for short, quando in zip(selecionados, horarios):
            if short["indice"] in enviados:
                continue
            arquivo = projeto.p("shorts", f"corte_{short['indice']}.mp4")
            # hashtags/tags fixas do canal entram mesmo se o texto foi reescrito à mão
            titulo, descricao, tags = hashtags_cortes.finalizar(short["titulo"], short.get("descricao", ""),
                                                                short.get("tags", []), canal.cfg.get("cortes", {}))
            corpo = _corpo(titulo, descricao, metadados.limitar_tags(tags),
                           meta.get("categoria", 24), quando, {**cfg, "declarar_conteudo_sintetico": False})
            sid = _enviar_video(youtube, arquivo, corpo, f"Short {short['indice']}")
            status_yt["shorts"].append({"indice": short["indice"], "id": sid, "publicar_em": quando.isoformat(),
                                        "url": f"https://youtube.com/shorts/{sid}"})
            projeto.atualizar_status(youtube=status_yt)
            historico.registrar_agendamento(projeto.id, "short", sid, short["titulo"], _iso_utc(quando))
    except Exception as exc:
        projeto.atualizar_status(youtube=status_yt)
        projeto.definir_estado("erro_publicacao", str(exc))
        raise exc if isinstance(exc, ErroPublicacao) else ErroPublicacao(f"Falha na publicação: {exc}") from exc
    historico.atualizar(projeto.id, estado="agendado")
    projeto.marcar_etapa("publicacao", True, shorts=len(status_yt["shorts"]))
    primeiro = min(dt.datetime.fromisoformat(s["publicar_em"]) for s in status_yt["shorts"])
    projeto.definir_estado("agendado", f"{len(status_yt['shorts'])} Shorts a partir de {primeiro.astimezone(fuso):%d/%m %H:%M}")
    log.ok(f"{len(status_yt['shorts'])} Short(s) agendado(s) no YouTube")
    return status_yt
