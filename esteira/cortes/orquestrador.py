"""Encadeia olheiro -> analista -> editor -> redator para transformar uma live em Shorts prontos."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path

from .. import log, metadados, util
from ..canal import Canal
from ..historico import Historico
from ..llm import ErroLLM, obter_cliente
from ..navegador import Navegador
from ..projeto import Projeto
from . import analise, camera, edicao, fontes, hashtags, ingestao

ETAPAS = ("ingestao", "analise", "edicao")
CAMPOS_INFO = ("id", "title", "channel", "channel_id", "uploader", "uploader_id", "duration", "live_status", "is_live",
               "heatmap", "language", "webpage_url", "timestamp", "release_timestamp")


class FonteNaoAutorizada(PermissionError):
    pass


class LiveRepetida(RuntimeError):
    pass


class LiveEmAndamento(RuntimeError):
    """A live ainda está no ar (os cortes saem quando ela terminar)."""


class LinkDeCanal(ValueError):
    """O link é da página do canal, não de um vídeo/live específico."""


_LINK_CANAL = re.compile(r"^https?://(www\.|m\.)?(twitch\.tv/(?!videos/)[\w-]+(/(videos|clips|about|schedule))?/?$|"
                         r"kick\.com/[\w-]+(/(videos|clips|about))?/?$|"
                         r"youtube\.com/(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)(/(streams|videos|live|featured))?/?$)",
                         re.I)


def url_do_canal(dados: dict, url: str) -> str:
    """Link da página do canal dono de uma live (para cadastrar como fonte)."""
    plataforma = fontes.plataforma_da_url(url)
    if plataforma == "twitch":
        login = dados.get("uploader_id") or dados.get("channel_id") or ""
        return f"https://www.twitch.tv/{login}" if login else ""
    if plataforma == "kick":  # na Kick o channel_id é numérico; o login (slug) vem em uploader_id
        login = dados.get("uploader_id") or dados.get("channel") or ""
        return f"https://kick.com/{str(login).lower()}" if login else ""
    return dados.get("uploader_url") or dados.get("channel_url") or ""


_ABA_DO_CANAL = re.compile(r"/(videos|clips|about|schedule|streams|live|featured)$", re.I)


def pagina_do_canal(url: str) -> tuple[str, dict | None]:
    """Link da página do canal a partir do link do canal (qualquer aba dele) ou de uma live/vídeo dele.
    Devolve também os dados da live quando precisou lê-la (evita ler de novo ao cadastrar a fonte)."""
    url = url.strip().rstrip("/")
    if _LINK_CANAL.match(url):
        return _ABA_DO_CANAL.sub("", url), None
    dados = ingestao.info(url)
    return (url_do_canal(dados, url) or url).rstrip("/"), dados


def parte_ja_gravada(plataforma: str, dados: dict, trecho: str | None) -> bool:
    """Live da Twitch/Kick ainda no ar, mas o trecho pedido já passou: dá para cortar agora (o VOD já guarda)."""
    if not (dados.get("is_live") and plataforma in fontes.PLATAFORMAS_HLS and trecho):
        return False
    _, fim = validar_trecho(trecho)
    return fim <= float(dados.get("duration") or 0) - 30


def verificar(canal: Canal, url: str, forcar: bool = False, autorizacao: str | None = None,
              trecho: str | None = None, so_conferir: bool = False) -> dict:
    """Checagens antes de cortar (rápidas: só lê as informações da live). Levanta erros com mensagem clara.

    autorizacao: se o canal da live ainda não é uma fonte, cadastra-o como "autorizado" com esse texto (o dono
    confirmou que o criador permite cortes). Sem ela, um canal novo levanta FonteNaoAutorizada.
    """
    if canal.editorial.get("tipo_canal", "dark") != "cortes":
        raise ValueError(f"'{canal.nome}' não é um canal de cortes (tipo_canal: cortes no identidade.yaml). "
                         "Crie um com: python esteira.py canal criar … --tipo cortes")
    url = url.strip()
    fontes.plataforma_da_url(url)  # ValueError para sites que não são Twitch, Kick nem YouTube
    if _LINK_CANAL.match(url):
        raise LinkDeCanal("Esse é o link do canal, não de uma live. Cole o link do vídeo da live "
                          "(ex.: twitch.tv/videos/123…, kick.com/<canal>/videos/… ou youtube.com/watch?v=…). "
                          "Se a live ainda está no ar, espere ela terminar e use o link do vídeo salvo.")
    dados = ingestao.info(url)
    dono = dados.get("channel") or dados.get("uploader") or "desconhecido"
    fonte = fontes.localizar(canal, dados)
    if not fonte and autorizacao and autorizacao.strip():
        fonte = fontes.adicionar(canal, url_do_canal(dados, url) or url, "autorizado", nome=dono,
                                 autorizacao=autorizacao, monitorar=False, info=dados)
        log.ok(f"{dono} cadastrado como fonte autorizada ({fonte.plataforma})")
    if not fonte:
        erro = FonteNaoAutorizada(
            f"O canal '{dono}' ainda não é uma fonte de '{canal.id}'. Corte só lives suas ou de criadores que "
            f"permitem cortes. Para cadastrar e cortar de uma vez: python esteira.py cortes processar --canal "
            f"{canal.id} --url <link> --autorizacao \"<como o criador autorizou>\"")
        erro.dono, erro.url_canal = dono, url_do_canal(dados, url)
        raise erro
    existente = ja_processada(canal.id, dados.get("id"), trecho)
    if existente and not forcar:
        raise LiveRepetida(f"Esta live já foi processada no projeto {existente}.")
    gravada = parte_ja_gravada(fontes.plataforma_da_url(url), dados, trecho)
    no_ar_hls = bool(dados.get("is_live")) and fontes.plataforma_da_url(url) in fontes.PLATAFORMAS_HLS
    if so_conferir and no_ar_hls and not gravada:  # a tela só lê o link: o trecho que já passou vem depois
        return {"dados": dados, "fonte": fonte, "dono": dono, "parte_gravada": False, "no_ar": True}
    if dados.get("is_live") and not gravada:
        no_ar = time.time() - float(dados.get("release_timestamp") or dados.get("timestamp") or time.time())
        if no_ar > 12 * 3600:
            raise LiveEmAndamento(f"'{dados.get('title')}' está no ar há {no_ar / 3600:.0f} h (transmissão contínua); "
                                  "escolha uma live com começo e fim.")
        if not canal.cfg.get("cortes", {}).get("gravar_ao_vivo", False):
            dica = (" Na Twitch/Kick dá para cortar agora a parte que já passou: informe um trecho (ex.: 01:00:00-01:40:00)."
                    if fontes.plataforma_da_url(url) in fontes.PLATAFORMAS_HLS else "")
            raise LiveEmAndamento("A live ainda está no ar. Os cortes são feitos quando ela terminar: mande o link de "
                                  "novo depois (ou ative 'cortes.gravar_ao_vivo' no identidade.yaml)." + dica)
    return {"dados": dados, "fonte": fonte, "dono": dono, "parte_gravada": gravada}


def _segundos(texto: str) -> float:
    partes = [float(p) for p in str(texto).strip().split(":")]
    while len(partes) < 3:
        partes.insert(0, 0.0)
    return partes[0] * 3600 + partes[1] * 60 + partes[2]


def _sobrepoe(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool:
    """Dois trechos da mesma live se cruzam? Sem trecho = a live inteira (cruza com qualquer um)."""
    if a is None or b is None:
        return True
    return a[0] < b[1] and b[0] < a[1]


def ja_processada(canal_id: str, id_live: str, trecho: str | None = None) -> str | None:
    """Projeto que já cortou esta live NESTE trecho. Partes diferentes da mesma live (ex.: 1:00–1:45 e 1:45–2:30)
    não contam como repetição: dá para cortar uma live longa em vários blocos."""
    pedido = validar_trecho(trecho) if trecho else None
    for projeto in Projeto.listar(canal_id):
        status = projeto.status
        if status.get("live", {}).get("id") != id_live or status.get("estado") == "descartado":
            continue
        feito = validar_trecho(status["trecho"]) if status.get("trecho") else None
        if _sobrepoe(pedido, feito):
            return projeto.id
    return None


def validar_trecho(trecho: str | None, duracao: float | None = None) -> tuple[float, float] | None:
    """(início, fim) em segundos do trecho "HH:MM:SS-HH:MM:SS"; ValueError se estiver invertido ou fora da live."""
    if not trecho:
        return None
    try:
        inicio, fim = (_segundos(x) for x in str(trecho).split("-"))
    except ValueError as exc:
        raise ValueError("Use o trecho no formato 00:30:00-01:10:00.") from exc
    if fim - inicio < 30:
        raise ValueError("O fim do trecho precisa vir pelo menos 30 s depois do começo.")
    if duracao and inicio >= float(duracao) - 10:
        raise ValueError(f"O trecho começa depois do fim da live (ela tem {util.formatar_tempo(float(duracao))}).")
    return inicio, fim


def plano_download(cfg: dict, plataforma: str, ao_vivo: bool, trecho: str | None) -> dict:
    """Como baixar a live. Twitch/Kick (HLS): só o trecho pedido, numa cópia leve para a análise; cada corte
    escolhido é baixado depois em HD (hd_por_trecho). YouTube: a live inteira, já na resolução final."""
    hls = plataforma in fontes.PLATAFORMAS_HLS and not ao_vivo
    secao = validar_trecho(trecho) if hls else None
    hd_por_trecho = hls and bool(cfg.get("hd_por_trecho", True))
    if hd_por_trecho:
        altura = int(cfg.get("altura_analise", 480))
    else:  # no modelo do canal o vídeo ocupa 1000 px de largura: 720p basta e baixa ~4x mais rápido que 1080p60
        altura = int(cfg.get("altura_download", 720 if cfg.get("layout", "moldura") == "moldura" else 1080))
    return {"secao": secao, "altura": altura, "hd_por_trecho": hd_por_trecho, "arquivo_inicio": secao[0] if secao else 0,
            "altura_hd": int(cfg.get("altura_hd", 1080))}


def processar(canal_id: str, url: str, trecho: str | None = None, modo_agente: bool = False, sem_llm: bool = False,
              abrir_painel: bool = True, forcar: bool = False, ate: str | None = None,
              opcoes: dict | None = None, autorizacao: str | None = None) -> Projeto:
    canal = Canal.carregar(canal_id)
    log.etapa("Olheiro — lendo a live")
    checagem = verificar(canal, url, forcar, autorizacao, trecho)
    dados, fonte, dono = checagem["dados"], checagem["fonte"], checagem["dono"]
    cfg = canal.cfg.get("cortes", {})
    ao_vivo = bool(dados.get("is_live")) and not checagem.get("parte_gravada")
    if checagem.get("parte_gravada"):
        log.info("A live ainda está no ar: cortando a parte que já foi gravada.")
    plataforma = fontes.plataforma_da_url(url)
    validar_trecho(trecho, None if dados.get("is_live") and not checagem.get("parte_gravada")
                   else dados.get("duration"))  # antes de criar o projeto
    plano = plano_download(cfg, plataforma, ao_vivo, trecho)
    log.ok(f"Live de {fonte.nome} ({fonte.tipo}, {plataforma}): {dados.get('title')}")
    projeto = Projeto.criar(canal.id, f"cortes {dados.get('title', 'live')}")
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    # arquivo_inicio: em que segundo da live o arquivo baixado começa (0 = live inteira)
    projeto.atualizar_status(tipo="cortes", trecho=trecho, arquivo_inicio=plano["arquivo_inicio"], fonte=asdict(fonte),
                             hd_por_trecho=plano["hd_por_trecho"], altura_hd=plano["altura_hd"],
                             opcoes=opcoes_da_live(opcoes), live={
        "id": dados.get("id"), "url": dados.get("webpage_url") or url, "titulo": dados.get("title", ""), "canal": dono,
        "plataforma": plataforma, "duracao": dados.get("duration"),
        "status": dados.get("live_status"), "idioma": (dados.get("language") or "")[:2] or None})
    pasta = projeto.p("fonte")
    util.salvar_json(pasta / "info.json", {k: dados.get(k) for k in CAMPOS_INFO})
    projeto.progresso(PCT_DOWNLOAD[0], ROTULO_DOWNLOAD)
    try:
        if plano["secao"]:  # Twitch/Kick com trecho: só os pedaços HLS do trecho (vale até para live no ar)
            ingestao.baixar_trecho(dados.get("webpage_url") or url, pasta, *plano["secao"], plano["altura"], "fonte")
        else:
            ingestao.baixar_video(dados.get("webpage_url") or url, pasta, ao_vivo=ao_vivo, altura=plano["altura"],
                                  limite_seg=int(float(cfg.get("max_horas_ao_vivo", 6)) * 3600) if ao_vivo else None)
    except Exception as exc:  # sem isso o projeto ficaria preso em "em produção"
        projeto.marcar_etapa("ingestao", False, erro=str(exc)[:500])
        projeto.definir_estado("erro", f"Não consegui baixar a live: {str(exc)[-300:]}")
        raise
    if plataforma == "youtube":  # Twitch e Kick não guardam chat nem legendas no VOD: a análise ouve o áudio
        projeto.progresso(PCT_DOWNLOAD[1], "Baixando o chat e as legendas")
        if ingestao.baixar_chat(url, pasta):
            log.ok("Chat da live baixado")
        if ingestao.baixar_legendas(url, pasta, (dados.get("language") or "")[:2]):
            log.ok("Legendas da plataforma baixadas")
    projeto.marcar_etapa("ingestao", True)
    projeto.progresso(40, "Live baixada")
    Historico(canal.caminho_db).registrar(dados.get("title", ""), projeto.id, f"Cortes: {dados.get('title', '')}",
                                          f"Live de {dono}", [f"live:{dados.get('id')}"], "em_producao")
    if ate == "ingestao":
        projeto.definir_estado("aguardando_etapa", "live baixada: escolha os momentos")
        return projeto
    return continuar(projeto, "analise", modo_agente=modo_agente, sem_llm=sem_llm, abrir_painel=abrir_painel, ate=ate)


PCT_DOWNLOAD = (5, 38)  # faixa da barra de progresso ocupada pelo download (o painel lê o % do yt-dlp)
ROTULO_DOWNLOAD = "Baixando a live"
OPCOES_PERMITIDAS = ("max_cortes_por_live", "duracao_min", "duracao_max", "modelo", "traduzir", "musica_volume_db")


def opcoes_da_live(opcoes: dict | None) -> dict:
    """Opções guardadas no status: as que se sobrepõem ao canal + o layout (lido à parte por edicao.layout_pedido,
    só se for um layout válido; nunca entra na configuração mesclada)."""
    saida = {k: v for k, v in (opcoes or {}).items() if k in OPCOES_PERMITIDAS}
    if (opcoes or {}).get("layout") in camera.LAYOUTS:
        saida["layout"] = opcoes["layout"]
    return saida


def cfg_cortes(canal: Canal, projeto: Projeto | None = None) -> dict:
    """Configuração de cortes do canal com as opções escolhidas para esta live por cima."""
    cfg = dict(canal.cfg.get("cortes", {}))
    if projeto is not None:
        cfg.update({k: v for k, v in (projeto.status.get("opcoes") or {}).items() if k in OPCOES_PERMITIDAS})
    return cfg


def _arquivo_inicio(status: dict) -> float:
    """Segundo da live em que o arquivo baixado começa (projetos antigos baixavam só o trecho)."""
    if "arquivo_inicio" in status:
        return float(status["arquivo_inicio"] or 0)
    return _segundos(status["trecho"].split("-")[0]) if status.get("trecho") else 0.0


def _trecho_na_live(short: dict, base: float) -> dict | None:
    """{inicio, fim} do Short em segundos da live (não do arquivo baixado), ou None sem tempo."""
    try:
        inicio = base + float(short["inicio"])
        return {"inicio": inicio, "fim": inicio + float(short["duracao"])}
    except (KeyError, TypeError, ValueError):
        return None


def _ja_enviados_da_live(projeto: Projeto) -> list[dict]:
    """Trechos (em segundos da live) dos Shorts que OUTROS projetos desta mesma live já mandaram ao YouTube."""
    id_live = (projeto.status.get("live") or {}).get("id")
    if not id_live:
        return []
    trechos = []
    for outro in Projeto.listar(projeto.canal_id):
        status = outro.status
        if outro.id == projeto.id or (status.get("live") or {}).get("id") != id_live:
            continue
        enviados = {int(s["indice"]) for s in (status.get("youtube") or {}).get("shorts", []) if s.get("id")}
        base = _arquivo_inicio(status)
        for short in outro.ler("shorts/shorts.json", []) or []:
            trecho = _trecho_na_live(short, base) if int(short["indice"]) in enviados else None
            if trecho:
                trechos.append(trecho)
    return trechos


def shorts_sem_repeticao(projeto: Projeto, selecionados: list[dict]) -> tuple[list[dict], list[dict]]:
    """Última barreira antes de agendar: tira o Short que mostra o mesmo trecho da live que outro já enviado ao
    YouTube (por qualquer projeto desta live) ou que outro Short desta lista. Devolve (ficam, tirados)."""
    vistos = _ja_enviados_da_live(projeto)
    feitos = {int(s["indice"]): s for s in projeto.ler("shorts/shorts.json", []) or []}
    base = _arquivo_inicio(projeto.status)
    ficam, tirados = [], []
    for short in selecionados:
        trecho = _trecho_na_live(feitos.get(int(short["indice"]), {}), base)
        if trecho and any(analise.repete(trecho, visto) for visto in vistos):
            tirados.append(short)
            continue
        ficam.append(short)
        if trecho:
            vistos.append(trecho)
    return ficam, tirados


# ------------------------------------------------------------------ rodadas: mais Shorts do mesmo trecho baixado
CAMPOS_DA_RODADA = ("tipo", "trecho", "arquivo_inicio", "fonte", "hd_por_trecho", "altura_hd", "opcoes", "live")


def _mesma_live(projeto: Projeto) -> list[Projeto]:
    """Os outros projetos (não descartados) desta live: outros trechos e outras rodadas do mesmo trecho."""
    id_live = (projeto.status.get("live") or {}).get("id")
    if not id_live:
        return []
    return [p for p in Projeto.listar(projeto.canal_id) if p.id != projeto.id and p.estado != "descartado"
            and (p.status.get("live") or {}).get("id") == id_live]


def usados_em_outros(projeto: Projeto) -> list[dict]:
    """Trechos (em segundos da live) que outros projetos desta live já escolheram para virar Short."""
    trechos = []
    for outro in _mesma_live(projeto):
        base = _arquivo_inicio(outro.status)
        trechos += [{"inicio": base + float(c["inicio"]), "fim": base + float(c["fim"])}
                    for c in outro.ler("analise/cortes.json", []) or []]
    return trechos


def sem_os_usados(projeto: Projeto, candidatos_: list[dict]) -> list[dict]:
    """Candidatos (tempo do arquivo deste projeto) que nenhum outro projeto desta live já escolheu."""
    usados = usados_em_outros(projeto)
    base = _arquivo_inicio(projeto.status)
    return [c for c in candidatos_ if not any(
        analise.repete({"inicio": base + float(c["inicio"]), "fim": base + float(c["fim"])}, u) for u in usados)]


def sugestoes_livres(projeto: Projeto) -> list[dict]:
    """Sugestões do analista deste trecho que ainda não viraram Short (nem aqui, nem em outra rodada)."""
    proprios = projeto.ler("analise/cortes.json", []) or []
    return [c for c in sem_os_usados(projeto, projeto.ler("analise/candidatos.json", []) or [])
            if not any(analise.repete(c, k) for k in proprios)]


def _vincular(origem: Path, destino: Path) -> None:
    """Mesmo arquivo nas duas rodadas: link físico (não ocupa espaço de novo); cópia se o disco não deixar."""
    import os
    import shutil

    destino.parent.mkdir(parents=True, exist_ok=True)  # "fonte/" não é criada junto com o projeto
    try:
        os.link(origem, destino)
    except OSError:
        shutil.copy2(origem, destino)


def _criar_rodada(origem: Projeto, canal: Canal, livres: list[dict]) -> Projeto:
    irmas = [p for p in _mesma_live(origem) if p.status.get("trecho") == origem.status.get("trecho")]
    rodada = 1 + max(int(p.status.get("rodada") or 1) for p in [origem, *irmas])
    live = origem.status.get("live") or {}
    novo = Projeto.criar(canal.id, f"cortes rodada {rodada} {live.get('titulo', 'live')}")
    novo.atualizar_status(**{k: origem.status[k] for k in CAMPOS_DA_RODADA if k in origem.status},
                          rodada=rodada, rodada_de=origem.id)
    for arquivo in origem.p("fonte").iterdir():  # o mesmo vídeo baixado (sem baixar de novo)
        if arquivo.is_file() and arquivo.suffix not in (".part", ".ytdl"):
            _vincular(arquivo, novo.p("fonte", arquivo.name))
    for nome in ("sinais.json", "cortes_agente.json"):  # cortes_agente: textos do agente para as próximas rodadas
        if origem.p("analise", nome).exists():
            _vincular(origem.p("analise", nome), novo.p("analise", nome))
    novo.salvar("analise/candidatos.json", livres)
    novo.marcar_etapa("ingestao", True, rodada_de=origem.id)
    Historico(canal.caminho_db).registrar(live.get("titulo", ""), novo.id, f"Cortes: {live.get('titulo', '')} (rodada {rodada})",
                                          f"Live de {live.get('canal', '')}", [f"live:{live.get('id')}"], "em_producao")
    return novo


def _tem_textos_para(arquivo: Path, livres: list[dict]) -> bool:
    ids = {int(c["id"]) for c in livres}
    try:
        pedido = json.loads(Path(arquivo).read_text(encoding="utf-8")).get("cortes", [])
    except (OSError, ValueError, AttributeError):
        return False
    return any(int(c.get("candidato", 0) or 0) in ids for c in pedido if isinstance(c, dict))


def nova_rodada(origem: Projeto, modo_agente: bool = False, sem_llm: bool = False, arquivo: Path | None = None,
                abrir_painel: bool = False, ate: str | None = None) -> Projeto | None:
    """Mais Shorts do mesmo trecho já baixado: as próximas sugestões do analista que ainda não viraram Short, de
    max_cortes_por_live em max_cortes_por_live. Textos: `arquivo` (ou analise/cortes_agente.json da origem), senão
    modelo de linguagem / agente / pontuação. None quando todas as sugestões já foram usadas."""
    canal = Canal.carregar(origem.canal_id)
    livres = sugestoes_livres(origem)
    if not livres:
        log.ok("Todas as sugestões deste trecho já viraram Shorts.")
        return None
    textos = Path(arquivo) if arquivo else origem.p("analise", "cortes_agente.json")
    novo = _criar_rodada(origem, canal, livres)
    log.anexar_arquivo(novo.p("logs", "execucao.log"))
    log.etapa(f"Rodada {novo.status['rodada']} — mais Shorts do trecho {origem.status.get('trecho') or 'inteiro'}")
    log.info(f"{len(livres)} sugestão(ões) do analista ainda não viraram Short")
    if textos.exists() and _tem_textos_para(textos, livres):
        return importar_selecao(novo, textos, abrir_painel)
    novo.definir_estado("em_producao", "cortes a partir de analise")
    sinais = (novo.ler("analise/sinais.json", {}) or {}).get("disponiveis", [])
    if escolher_momentos(novo, canal, livres, modo_agente, sem_llm, sinais) is None:
        return novo  # esperando o agente
    if ate == "analise":
        novo.atualizar_status(edicao_desatualizada=True)
        novo.definir_estado("aguardando_etapa", "momentos escolhidos: revise e mande editar os Shorts")
        return novo
    return continuar(novo, "edicao", abrir_painel=abrir_painel)


def _proxima_rodada_automatica(projeto: Projeto, canal: Canal) -> None:
    """Depois de agendar (canal sem revisão e com cortes.rodadas_automaticas): já começa as próximas sugestões."""
    if not bool(canal.cfg.get("cortes", {}).get("rodadas_automaticas", False)):
        return
    try:
        proxima = nova_rodada(projeto)
        if proxima is not None:
            log.ok(f"Rodada {proxima.status.get('rodada')} terminou em: {proxima.estado}")
    except Exception as exc:  # a rodada nova marca o próprio erro; esta live já está agendada
        log.aviso(f"A próxima rodada de Shorts não começou: {str(exc)[-300:]}")


def _video_fonte(projeto: Projeto) -> Path:
    arquivos = sorted((a for a in projeto.p("fonte").glob("fonte.*") if a.suffix not in (".part", ".ytdl")),
                      key=lambda p: p.stat().st_size, reverse=True)
    if not arquivos:
        raise FileNotFoundError("O vídeo da live não foi baixado; rode a etapa de ingestão.")
    return arquivos[0]


def analisar(projeto: Projeto, canal: Canal, modo_agente: bool = False, sem_llm: bool = False) -> list[dict] | None:
    log.etapa("Analista — procurando os momentos de interesse")
    from .. import ffmpeg

    projeto.progresso(42, "Ouvindo a live")
    status = projeto.status
    video = _video_fonte(projeto)
    total = ffmpeg.duracao(video)
    # Tempos: "live" (VTT, chat, mapa de calor) -> "arquivo" (live - arquivo_inicio) -> "janela" (arquivo - ja).
    # Projetos antigos baixavam só o trecho (arquivo começa no trecho); os novos baixam a live inteira.
    trecho_ini = trecho_fim = None
    if status.get("trecho"):
        trecho_ini, trecho_fim = (_segundos(x) for x in status["trecho"].split("-"))
    arquivo_inicio = _arquivo_inicio(status)
    ja = max(0.0, (trecho_ini or 0.0) - arquivo_inicio)
    jb = min(total, (trecho_fim - arquivo_inicio) if trecho_fim else total)
    if jb - ja < 10:
        raise ValueError(f"O trecho {status.get('trecho')} está fora do vídeo baixado ({util.formatar_tempo(total)}).")
    duracao = int(jb - ja)
    deslocamento = arquivo_inicio + ja
    if trecho_ini is not None:
        log.info(f"Analisando só o trecho {util.formatar_tempo(ja + arquivo_inicio)}–{util.formatar_tempo(jb + arquivo_inicio)}")

    def ajustar(lista: list[dict]) -> list[dict]:
        saida = []
        for item in lista:
            ini, fim = item["inicio"] - deslocamento, item["fim"] - deslocamento
            if fim > 0 and ini < duracao:
                saida.append({**item, "inicio": max(0.0, ini), "fim": min(float(duracao), fim)})
        return saida

    pasta = projeto.p("fonte")
    legenda = next(iter(sorted(pasta.glob("legenda*.vtt"))), None)
    segmentos = ajustar(analise.ler_vtt(legenda))
    if segmentos:
        log.info(f"Transcrição da plataforma: {len(segmentos)} trechos")
    else:
        segmentos = analise.transcrever(video, status.get("live", {}).get("idioma"), ja, jb)
    chat = [t - deslocamento for t in analise.ler_chat(next(iter(pasta.glob("chat*.json")), None))]
    chat = [t for t in chat if 0 <= t < duracao]
    info = util.ler_json(pasta / "info.json", {}) or {}
    heatmap = [{**h, "start_time": h["start_time"] - deslocamento, "end_time": h["end_time"] - deslocamento}
               for h in info.get("heatmap") or []]
    energia = analise.energia_por_segundo(video, ja, jb)
    sinais = analise.montar_sinais(duracao, chat, heatmap, energia)
    projeto.progresso(47, "Escolhendo os melhores momentos")
    log.info("Sinais usados: " + (", ".join(sinais["disponiveis"]) or "só a transcrição") + f" | chat: {len(chat)} mensagens")
    cfg = cfg_cortes(canal, projeto)
    dmin, dmax = float(cfg.get("duracao_min", 20)), float(cfg.get("duracao_max", 58))
    maximo = int(cfg.get("max_cortes_por_live", 5))
    cands = analise.candidatos(segmentos, sinais["combinado"], dmin, dmax, max(10, 3 * maximo))
    for c in cands:  # janela -> tempo do arquivo (é nele que o editor corta)
        c["inicio"], c["fim"] = round(c["inicio"] + ja, 2), round(c["fim"] + ja, 2)
    total_cands = len(cands)
    cands = sem_os_usados(projeto, cands)  # o que outra rodada desta live já escolheu não volta como sugestão
    projeto.salvar("analise/candidatos.json", cands)
    projeto.salvar("analise/sinais.json", {"disponiveis": sinais["disponiveis"], "mensagens_chat": len(chat),
                                           "segmentos": len(segmentos), "duracao": duracao,
                                           "janela": [round(ja, 2), round(jb, 2)], "arquivo_inicio": arquivo_inicio})
    if not cands:
        raise RuntimeError("Todas as sugestões deste trecho já viraram Shorts em outras rodadas." if total_cands
                           else "Nenhum trecho candidato encontrado (live curta demais ou sem fala).")
    return escolher_momentos(projeto, canal, cands, modo_agente, sem_llm, sinais["disponiveis"])


def escolher_momentos(projeto: Projeto, canal: Canal, cands: list[dict], modo_agente: bool = False,
                      sem_llm: bool = False, sinais_usados: list | tuple = ()) -> list[dict] | None:
    """Escolhe entre os candidatos (modelo de linguagem, agente de IA ou pontuação) e grava analise/cortes.json.
    None = esperando o agente escrever a seleção (pedido_cortes.md)."""
    status = projeto.status
    cfg = cfg_cortes(canal, projeto)
    dmin, dmax = float(cfg.get("duracao_min", 20)), float(cfg.get("duracao_max", 58))
    maximo = int(cfg.get("max_cortes_por_live", 5))
    fonte_nome = status.get("fonte", {}).get("nome", "")
    llm = None if (sem_llm or modo_agente) else obter_cliente()
    escolhidos = None
    if llm:
        try:
            resposta = llm.gerar_json(analise.instrucoes(canal, fonte_nome, maximo, dmin, dmax),
                                      analise.mensagem(cands), analise.ESQUEMA_CORTES, max_tokens=16000)
            escolhidos = resposta.get("cortes", [])
            log.ok(f"Momentos escolhidos por {llm.descricao}")
        except ErroLLM as exc:
            log.aviso(f"Falha no modelo de linguagem: {exc}; usando a pontuação automática.")
    if escolhidos is None and modo_agente:
        caminho = projeto.p("analise", "pedido_cortes.md")
        util.salvar_texto(caminho, f"# Pedido de seleção de cortes\n\n{analise.instrucoes(canal, fonte_nome, maximo, dmin, dmax)}\n\n"
                                   f"Escreva o JSON em `{projeto.p('analise', 'cortes_agente.json')}` seguindo o esquema e rode:\n"
                                   f"`python esteira.py cortes importar --projeto {projeto.id} --arquivo "
                                   f"{projeto.p('analise', 'cortes_agente.json')}`\n\n## Esquema\n```json\n"
                                   f"{json.dumps(analise.ESQUEMA_CORTES, ensure_ascii=False, indent=2)}\n```\n\n"
                                   f"## {analise.mensagem(cands)}\n")
        projeto.definir_estado("aguardando_roteiro", "o agente de IA deve escolher os cortes")
        log.aviso(f"AGUARDANDO_SELECAO_DO_AGENTE: leia {caminho} e importe o JSON com 'cortes importar'.")
        return None
    if escolhidos is None:
        escolhidos = analise.heuristica(cands, maximo)
        projeto.alertar("analise", "Cortes escolhidos sem modelo de linguagem (pela pontuação): revise títulos e ganchos.")
    cortes = analise.validar(escolhidos, cands, dmin, dmax, maximo)
    projeto.salvar("analise/cortes.json", cortes)
    projeto.marcar_etapa("analise", True, cortes=len(cortes), sinais=list(sinais_usados))
    projeto.progresso(50, f"{len(cortes)} momento(s) escolhido(s)")
    log.ok(f"{len(cortes)} momento(s) escolhido(s)")
    return cortes


def importar_selecao(projeto: Projeto, arquivo: Path, abrir_painel: bool = True) -> Projeto:
    canal = Canal.carregar(projeto.canal_id)
    dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    cfg = cfg_cortes(canal, projeto)
    cortes = analise.validar(dados.get("cortes", []), projeto.ler("analise/candidatos.json", []) or [],
                             float(cfg.get("duracao_min", 20)), float(cfg.get("duracao_max", 58)),
                             int(cfg.get("max_cortes_por_live", 5)))
    if not cortes:
        raise ValueError("Nenhum corte válido no arquivo (confira os números dos candidatos).")
    projeto.salvar("analise/cortes.json", cortes)
    projeto.limpar_alertas("analise")
    projeto.marcar_etapa("analise", True, cortes=len(cortes), origem="agente")
    return continuar(projeto, "edicao", abrir_painel=abrir_painel)


def link_do_momento(url: str, segundos: float | None) -> str:
    """Link da live já no segundo do corte (YouTube: t=123s; Twitch: t=1h02m03s)."""
    if not url or not segundos or segundos < 1:
        return url
    s = int(segundos)
    sep = "&" if "?" in url else "?"
    if "twitch.tv" in url:
        return f"{url}{sep}t={s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"
    return f"{url}{sep}t={s}s"


def _descricao(corte: dict, credito: str, url: str, canal: Canal, inicio_live: float | None = None) -> str:
    cfg = (getattr(canal, "cfg", None) or {}).get("cortes", {})
    linha = hashtags.linha(cfg, corte.get("tags", []))
    link = f"Live completa (a partir deste momento): {link_do_momento(url, inicio_live)}"
    partes = [corte.get("descricao", "").strip(), f"{credito}\n{link}" if credito else link]
    if canal.editorial.get("chamada_final"):
        partes.append(canal.editorial["chamada_final"])
    partes += [hashtags.convite(cfg), linha]
    return metadados._limpar_youtube("\n\n".join(p for p in partes if p))[:4900]


def _credito_musica(canal: Canal, trilha: str | None) -> str:
    """Crédito da trilha (só para músicas de terceiros listadas em trilhas/creditos.json)."""
    if not trilha or trilha == "nenhuma":
        return ""
    creditos = util.ler_json(canal.pasta_trilhas / "creditos.json", {}) or {}
    return str(creditos.get(trilha, "")).strip()


def _meta_short(r: dict, velho: dict, credito: str, live: dict, canal: Canal, status: dict) -> dict:
    """Textos de um Short para o YouTube; o que o dono editou no painel (editado=True) é mantido."""
    if not velho.get("editado"):
        velho = {}
    cfg = (getattr(canal, "cfg", None) or {}).get("cortes", {})
    descricao = velho.get("descricao") or _descricao(r, credito, live.get("url", ""), canal,
                                                     r["inicio"] + _arquivo_inicio(status))
    musica = _credito_musica(canal, r.get("trilha"))
    if musica and not velho.get("descricao") and musica not in descricao:
        descricao = metadados._limpar_youtube(f"{descricao}\n\nMúsica: {musica}")[:4900]
    entrada = {"indice": r["indice"], "blocos": [], "inicio": r["inicio"],
               "selecionado": velho.get("selecionado", True),
               "titulo": velho.get("titulo") or hashtags.titulo(r["titulo"], cfg),
               "descricao": descricao,
               "tags": velho.get("tags") or metadados.limitar_tags(hashtags.tags(cfg, r.get("tags", [])))}
    if velho.get("editado"):
        entrada["editado"] = True
    return entrada


def _fixar_textos(projeto: Projeto, cortes: list[dict], resultados: list[dict]) -> None:
    """Grava em cortes.json os textos finais de cada Short (já traduzidos), para refazer sem traduzir de novo."""
    por_indice = {int(r["indice"]): r for r in resultados}
    for corte in cortes:
        r = por_indice.get(int(corte.get("indice", 0)))
        if r:
            corte.update({"gancho": r.get("gancho", corte.get("gancho", "")), "titulo": r.get("titulo", corte.get("titulo", "")),
                          "descricao": r.get("descricao", corte.get("descricao", "")), "automatico": False})
    projeto.salvar("analise/cortes.json", cortes)


def reeditar_corte(projeto: Projeto, indice: int, gancho: str | None = None, inicio: float | None = None,
                   fim: float | None = None, trilha: str | None = None, modelo: str | None = None,
                   layout: str | None = None) -> dict:
    """Refaz só um Short (gancho, começo/fim, música, modelo visual ou layout), mantendo os outros e os textos do dono."""
    if projeto.estado in ("publicando", "agendado", "aprovado"):
        raise ValueError("Este Short já foi para o YouTube: cancele o agendamento antes de refazer.")
    canal = Canal.carregar(projeto.canal_id)
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    cortes = projeto.ler("analise/cortes.json", []) or []
    corte = next((c for c in cortes if int(c.get("indice", 0)) == int(indice)), None)
    if not corte:
        raise ValueError(f"Short {indice} não encontrado.")
    cfg = cfg_cortes(canal, projeto)
    dmin, dmax = float(cfg.get("duracao_min", 20)), float(cfg.get("duracao_max", 58))
    if gancho is not None and gancho.strip():
        corte["gancho"] = gancho.strip()[:80]
        corte["automatico"] = False  # texto escrito pelo dono: não traduz por cima
    novo_ini = float(inicio) if inicio is not None else float(corte["inicio"])
    novo_fim = float(fim) if fim is not None else float(corte["fim"])
    if novo_fim - novo_ini < 5 or novo_fim - novo_ini > max(dmax, 60) + 0.5 or novo_ini < 0:
        raise ValueError(f"Duração inválida ({novo_fim - novo_ini:.0f} s): use entre 5 e {max(dmax, 60):.0f} s.")
    corte["inicio"], corte["fim"] = round(novo_ini, 2), round(novo_fim, 2)
    if trilha is not None:
        corte["trilha"] = trilha
    if modelo is not None:
        if modelo not in edicao.MODELOS:
            raise ValueError(f"Modelo visual desconhecido: {modelo}")
        corte["modelo"] = modelo
    if layout is not None:
        if layout not in camera.LAYOUTS:
            raise ValueError(f"Layout desconhecido: {layout} (use {', '.join(camera.LAYOUTS)})")
        corte["layout"] = layout
    projeto.salvar("analise/cortes.json", cortes)
    status = projeto.status
    fonte = fontes.Fonte(**status["fonte"])
    live = status.get("live", {})
    projeto.progresso(10, f"Refazendo o Short {indice}")
    try:
        with Navegador() as nav:
            r = edicao.renderizar(projeto, canal, corte, _video_fonte(projeto), fonte.texto_credito,
                                  live.get("idioma"), nav)
    except Exception as exc:
        projeto.definir_estado("pendente_revisao", f"não deu para refazer o Short {indice}: {str(exc)[:200]}")
        raise
    _fixar_textos(projeto, cortes, [r])
    resultados = [x for x in (projeto.ler("shorts/shorts.json", []) or []) if int(x["indice"]) != int(indice)]
    resultados = sorted(resultados + [r], key=lambda x: int(x["indice"]))
    projeto.salvar("shorts/shorts.json", resultados)
    meta = projeto.ler("metadados.json", {}) or {}
    velhos = {int(x["indice"]): x for x in meta.get("shorts", [])}
    novos = []
    for x in resultados:
        n = int(x["indice"])
        if n == int(indice):
            velho = velhos.get(n, {})
            manter = gancho is None and bool(velho)  # só mudou tempo/música: os textos continuam os mesmos
            entrada = _meta_short(r, {**velho, "editado": True} if manter else velho, fonte.texto_credito, live,
                                  canal, status)
            if manter and not velho.get("editado"):
                entrada.pop("editado", None)
            novos.append(entrada)
        else:
            novos.append(velhos.get(n) or _meta_short(x, {}, fonte.texto_credito, live, canal, status))
    meta["shorts"] = novos
    projeto.salvar("metadados.json", meta)
    projeto.definir_estado("pendente_revisao", f"Short {indice} refeito")
    projeto.progresso(100, f"Short {indice} refeito")
    log.ok(f"Short {indice} refeito")
    return r


def editar(projeto: Projeto, canal: Canal) -> list[dict]:
    log.etapa("Editor — cortando, enquadrando, legendando e colocando música")
    status = projeto.status
    cortes, repetidos = analise.sem_repetidos(projeto.ler("analise/cortes.json", []) or [])
    if repetidos:  # o mesmo momento escolhido duas vezes (à mão ou pela IA) viraria dois Shorts iguais
        cortes = [{**corte, "indice": n} for n, corte in enumerate(cortes, 1)]
        projeto.salvar("analise/cortes.json", cortes)
        log.aviso(f"{len(repetidos)} corte(s) repetido(s) tirado(s): mesmo trecho de outro Short.")
    fonte = fontes.Fonte(**status["fonte"])
    credito = fonte.texto_credito
    live = status.get("live", {})
    video = _video_fonte(projeto)
    for antigo in projeto.p("shorts").glob("corte_*"):
        antigo.unlink(missing_ok=True)
    resultados = []
    with Navegador() as nav:
        for k, corte in enumerate(cortes):
            projeto.progresso(50 + 45 * k / max(1, len(cortes)), f"Editando o Short {k + 1} de {len(cortes)}")
            resultados.append(edicao.renderizar(projeto, canal, corte, video, credito, live.get("idioma"), nav))
    projeto.progresso(95, "Escrevendo títulos e descrições")
    projeto.salvar("shorts/shorts.json", resultados)
    _fixar_textos(projeto, cortes, resultados)
    anterior = {(s.get("inicio"), s.get("indice")): s for s in (projeto.ler("metadados.json", {}) or {}).get("shorts", [])}
    meta = {"titulo": f"Cortes: {live.get('titulo', '')}"[:100], "descricao_base": "", "tags": [], "hashtags": [],
            "capitulos": [], "frases_thumbnail": [], "thumbnail_escolhida": 1,
            "categoria": canal.editorial.get("categoria_youtube", 24), "idioma": "pt-BR", "shorts": []}
    for r in resultados:
        velho = anterior.get((r["inicio"], r["indice"]), {})
        meta["shorts"].append(_meta_short(r, velho, credito, live, canal, status))
    projeto.salvar("seo.json", {"titulo": meta["titulo"], "descricao": "", "tags": [], "hashtags": [],
                                "frases_thumbnail": [], "capitulos": []})
    projeto.salvar("fontes.json", [{"id": 1, "titulo": f"{live.get('titulo', '')} (live original)",
                                    "url": live.get("url", ""), "origem": live.get("plataforma", ""), "citada": True}])
    projeto.salvar("metadados.json", meta)
    projeto.marcar_etapa("edicao", True, shorts=len(resultados))
    projeto.progresso(100, "Shorts prontos para você revisar")
    log.ok(f"{len(resultados)} Short(s) pronto(s) para revisão")
    return resultados


def continuar(projeto: Projeto, a_partir_de: str = "analise", modo_agente: bool = False, sem_llm: bool = False,
              abrir_painel: bool = True, ate: str | None = None) -> Projeto:
    """Roda a partir de uma etapa; com ate="analise" para depois de escolher os momentos (o dono revisa e manda editar)."""
    canal = Canal.carregar(projeto.canal_id)
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    if a_partir_de not in ETAPAS[1:]:
        raise ValueError("Use --a-partir-de analise ou edicao (para baixar de novo, processe o link outra vez).")
    tinha_reprovacao = bool(projeto.reprovacoes_pendentes())
    projeto.definir_estado("em_producao", f"cortes a partir de {a_partir_de}")
    etapa = a_partir_de
    try:
        if a_partir_de == "analise":
            if analisar(projeto, canal, modo_agente, sem_llm) is None:
                return projeto
            if ate == "analise":
                projeto.atualizar_status(edicao_desatualizada=True)
                projeto.definir_estado("aguardando_etapa", "momentos escolhidos: revise e mande editar os Shorts")
                return projeto
        etapa = "edicao"
        editar(projeto, canal)
        projeto.atualizar_status(edicao_desatualizada=False)
    except Exception as exc:
        projeto.marcar_etapa(etapa, False, erro=str(exc)[:500])
        projeto.definir_estado("erro", f"{etapa}: {str(exc)[:300]}")
        raise
    if tinha_reprovacao:
        projeto.resolver_reprovacoes(f"reprocessado a partir de '{a_partir_de}'")
    projeto.definir_estado("pendente_revisao")
    Historico(canal.caminho_db).atualizar(projeto.id, estado="pendente_revisao")
    if not bool(canal.automacao.get("revisao_humana_obrigatoria", True)):
        # o dono pediu para não revisar: aprova e agenda os Shorts na hora
        from ..orquestrador import aprovar

        log.info("Revisão humana desativada neste canal: aprovando e agendando os Shorts automaticamente.")
        resultado = aprovar(projeto)
        if resultado.get("erro"):  # ex.: limite de envios do dia — a fila de envio tenta de novo depois
            log.aviso(f"Os Shorts ficaram prontos, mas o agendamento falhou: {resultado['erro']}")
        # "finalizou 6, manda mais 6": as sugestões que sobraram viram a próxima rodada (mesmo que o envio espere)
        _proxima_rodada_automatica(projeto, canal)
        return projeto
    if abrir_painel:
        from ..orquestrador import abrir_painel_web

        abrir_painel_web(projeto)
    return projeto


def monitorar(canal_id: str, sem_llm: bool = False) -> Projeto | None:
    """Procura a live mais recente ainda não processada entre as fontes monitoradas e a processa."""
    canal = Canal.carregar(canal_id)
    gravar_ao_vivo = bool(canal.cfg.get("cortes", {}).get("gravar_ao_vivo", False))
    for fonte in fontes.carregar(canal):
        if not fonte.monitorar:
            continue
        log.info(f"Olheiro verificando {fonte.nome} ({fonte.plataforma})…")
        try:
            lives = ingestao.lives_recentes(fonte.url, fonte.plataforma)
        except ingestao.ErroIngestao as exc:
            log.aviso(f"Não foi possível listar as lives de {fonte.nome}: {exc}")
            continue
        for live in lives:
            if ja_processada(canal.id, live["id"]):
                continue
            if live["status"] == "is_live" and not gravar_ao_vivo:
                log.info(f"{fonte.nome} está ao vivo agora; os cortes saem quando a live terminar.")
                continue
            try:
                return processar(canal.id, live["url"], sem_llm=sem_llm, abrir_painel=False)
            except LiveRepetida:
                continue
            except LiveEmAndamento as exc:
                log.info(str(exc))
                continue
    log.info("Nenhuma live nova nas fontes monitoradas.")
    return None
