#!/usr/bin/env python3
"""Testes rápidos e offline da esteira de cortes (não usam internet, YouTube, Ollama nem GPU).

Uso:  python testes/teste_cortes.py
Cobre: leitura de legendas, frases e candidatos, validação de cortes, tradução no ritmo da fala, enquadramento,
moldura do canal, links do painel, textos dos Shorts, fonte do canal vinculado, trilhas synthwave, rotas do painel,
links da Twitch/Kick, hashtags fixas, detecção da webcam, zoom nos picos e layouts.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("ESTEIRA_SEM_VENV", "1")
_DADOS = tempfile.TemporaryDirectory(prefix="cortes_teste_")
os.environ["PLATAFORMA_DADOS"] = _DADOS.name  # banco de contas temporário (não toca em dados/)
_CANAIS = tempfile.TemporaryDirectory(prefix="canais_teste_")


def _criar_canal_de_teste() -> None:
    """O projeto vem sem canais: os testes usam um canal de cortes temporário ("canal_teste"), fora de canais/."""
    from esteira import canal

    canal.PASTA_CANAIS = Path(_CANAIS.name)
    canal.criar_canal("canal_teste", "Canal de Teste", "cortes de lives", "animado", "roxo e amarelo", "rapido",
                      tipo="cortes")


_criar_canal_de_teste()

TESTES = []


def teste(func):
    TESTES.append(func)
    return func


# ------------------------------------------------------------------ análise
@teste
def legenda_vtt_sem_marcas_de_troca_de_voz():
    from esteira.cortes import analise

    with tempfile.TemporaryDirectory() as tmp:
        vtt = Path(tmp) / "l.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n&gt;&gt; Olha isso aqui!\n\n"
                       "00:00:03.000 --> 00:00:05.000\n>> Que jogada <c>insana</c>.\n", encoding="utf-8")
        segmentos = analise.ler_vtt(vtt)
    textos = " ".join(s["texto"] for s in segmentos)
    assert ">" not in textos and "&gt" not in textos, textos
    assert "insana" in textos and "Olha isso aqui!" in textos


@teste
def frases_completas_e_candidatos_sem_sobreposicao():
    from esteira.cortes import analise

    segmentos = [{"inicio": i * 2.0, "fim": i * 2.0 + 2, "texto": f"frase número {i} termina aqui." if i % 2 else f"parte {i} da fala"}
                 for i in range(60)]
    frases = analise.frases(segmentos)
    assert frases and all(f["fim"] > f["inicio"] for f in frases)
    combinado = [0.0] * 130
    for t in range(40, 60):
        combinado[t] = 3.0
    cands = analise.candidatos(segmentos, combinado, 20, 35, 5)
    assert cands, "nenhum candidato"
    for a in cands:
        assert 20 - 0.01 <= a["fim"] - a["inicio"] <= 35 + 0.01, a
        for b in cands:
            if a is not b:
                assert a["fim"] + 5 < b["inicio"] or a["inicio"] > b["fim"] + 5, (a, b)


@teste
def validar_mantem_marca_automatica_e_corrige_tempos():
    from esteira.cortes import analise

    cands = [{"id": 1, "inicio": 100.0, "fim": 125.0, "pontuacao": 1.0, "texto": "x"}]
    cortes = analise.validar([{"candidato": 1, "inicio": 50, "fim": 400, "gancho": "g", "titulo": "t",
                              "descricao": "d", "tags": ["a"], "motivo": "m", "automatico": True}], cands, 20, 35, 5)
    assert cortes[0]["inicio"] == 100.0 and cortes[0]["fim"] == 125.0  # fora da tolerância → volta ao candidato
    assert cortes[0]["automatico"] is True and cortes[0]["indice"] == 1


@teste
def cortes_repetidos_pelo_tempo_saem():
    from esteira.cortes import analise

    a, quase_igual = {"inicio": 100.0, "fim": 125.0}, {"inicio": 103.0, "fim": 127.0}
    vizinho, colado = {"inicio": 126.0, "fim": 150.0}, {"inicio": 118.0, "fim": 140.0}  # colado: só 7 s em comum
    assert analise.repete(a, quase_igual) and analise.repete(quase_igual, a)
    assert not analise.repete(a, vizinho) and not analise.repete(a, colado)
    ficam, tirados = analise.sem_repetidos([a, vizinho, quase_igual, dict(a)])
    assert ficam == [a, vizinho] and len(tirados) == 2  # fica o primeiro de cada repetição, na ordem
    cands = [{"id": 1, "inicio": 100.0, "fim": 125.0, "pontuacao": 2.0, "texto": "x"},
             {"id": 2, "inicio": 300.0, "fim": 325.0, "pontuacao": 1.0, "texto": "y"}]
    pedido = [{"candidato": 1, "gancho": "primeiro"}, {"candidato": 1, "inicio": 102, "fim": 124, "gancho": "de novo"},
              {"candidato": 2, "gancho": "outro"}]
    cortes = analise.validar(pedido, cands, 20, 35, 2)
    assert [c["gancho"] for c in cortes] == ["primeiro", "outro"], "o repetido não pode gastar a vaga do outro momento"


@teste
def rodadas_usam_as_sugestoes_que_sobraram():
    import json
    import os

    from esteira import projeto as projeto_mod
    from esteira.cortes import orquestrador as orq

    class HistoricoFalso:  # não escreve no historico.db de verdade
        def __init__(self, *_):
            pass

        def registrar(self, *_, **__):
            pass

        def atualizar(self, *_, **__):
            pass

    original, historico = projeto_mod.PASTA_PROJETOS, orq.Historico
    with tempfile.TemporaryDirectory() as tmp:
        projeto_mod.PASTA_PROJETOS, orq.Historico = Path(tmp), HistoricoFalso
        try:
            pasta = Path(tmp) / "canal_teste" / "2026-11-18_cortes-live-x"
            for sub in ("fonte", "analise", "shorts", "logs"):
                (pasta / sub).mkdir(parents=True)
            (pasta / "fonte" / "fonte.mp4").write_bytes(b"video")
            cands = [{"id": n, "inicio": 100.0 * n, "fim": 100.0 * n + 25, "pontuacao": 10 - n, "texto": f"fala {n}."}
                     for n in range(1, 11)]
            (pasta / "analise" / "candidatos.json").write_text(json.dumps(cands))
            (pasta / "analise" / "cortes.json").write_text(json.dumps([{**cands[0], "indice": 1}, {**cands[1], "indice": 2}]))
            (pasta / "status.json").write_text(json.dumps({
                "estado": "agendado", "tipo": "cortes", "trecho": "01:00:00-01:45:00", "arquivo_inicio": 3600,
                "live": {"id": "v9", "titulo": "Live X", "canal": "Fulano"},
                "opcoes": {"max_cortes_por_live": 6}, "etapas": {}, "eventos": []}))  # não depende do padrão do canal
            origem = projeto_mod.Projeto(pasta)
            assert [c["id"] for c in orq.sugestoes_livres(origem)] == list(range(3, 11))

            r2 = orq.nova_rodada(origem, sem_llm=True, ate="analise")
            assert r2.status["rodada"] == 2 and r2.status["rodada_de"] == origem.id and r2.estado == "aguardando_etapa"
            assert os.path.samefile(r2.p("fonte", "fonte.mp4"), pasta / "fonte" / "fonte.mp4"), "sem baixar de novo"
            usados_r2 = {round(c["inicio"]) for c in r2.ler("analise/cortes.json")}
            assert len(usados_r2) == 6 and not usados_r2 & {100, 200}

            r3 = orq.nova_rodada(r2, sem_llm=True, ate="analise")
            usados_r3 = {round(c["inicio"]) for c in r3.ler("analise/cortes.json")}
            assert r3.status["rodada"] == 3 and len(usados_r3) == 2 and not usados_r3 & usados_r2
            assert orq.nova_rodada(origem, sem_llm=True, ate="analise") is None, "acabaram as sugestões"
            # o painel não mostra de novo o que outra rodada já escolheu (os desta rodada ele esconde no navegador)
            assert [c["id"] for c in orq.sem_os_usados(origem, cands)] == [1, 2]
            textos = Path(tmp) / "textos.json"
            textos.write_text(json.dumps({"cortes": [{"candidato": 1, "gancho": "x"}]}))
            assert not orq._tem_textos_para(textos, cands[2:]) and orq._tem_textos_para(textos, cands)
        finally:
            projeto_mod.PASTA_PROJETOS, orq.Historico = original, historico


@teste
def short_com_restricao_sai_da_agenda():
    import json

    from esteira import projeto as projeto_mod
    from esteira.modulos import restricoes_youtube as r

    livre = {"id": "ok1", "status": {"uploadStatus": "processed", "privacyStatus": "private"}, "contentDetails": {}}
    bloqueado = {"id": "cid", "status": {"uploadStatus": "processed", "privacyStatus": "private", "embeddable": True},
                 "contentDetails": {"regionRestriction": {"blocked": ["DE", "US"]}}}
    recusado = {"id": "rej", "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"}, "contentDetails": {}}
    idade = {"id": "age", "status": {"uploadStatus": "processed"}, "contentDetails": {"contentRating": {"ytRating": "ytAgeRestricted"}}}
    assert r.motivo(livre) is None and "2 país" in r.motivo(bloqueado) and "copyright" in r.motivo(recusado)
    assert r.motivo(idade) == "restrição de idade" and "não foi encontrado" in r.motivo(None)
    assert "só em 1" in r.motivo({"status": {}, "contentDetails": {"regionRestriction": {"allowed": ["BR"]}}})

    class Pedido:
        def __init__(self, valor):
            self.valor = valor

        def execute(self):
            return self.valor

    class YouTubeFalso:
        def __init__(self):
            self.atualizados = []

        def videos(self):
            return self

        def list(self, part, id):
            return Pedido({"items": [i for i in (livre, bloqueado) if i["id"] in id.split(",")]})

        def update(self, part, body):
            self.atualizados.append(body)
            return Pedido({})

    class HistoricoFalso:
        cancelados = []

        def __init__(self, *_):
            pass

        def cancelar_agendamento_video(self, vid):
            HistoricoFalso.cancelados.append(vid)

    original, historico = projeto_mod.PASTA_PROJETOS, r.Historico
    with tempfile.TemporaryDirectory() as tmp:
        projeto_mod.PASTA_PROJETOS, r.Historico = Path(tmp), HistoricoFalso
        try:
            pasta = Path(tmp) / "canal_r" / "p1"
            pasta.mkdir(parents=True)
            (pasta / "status.json").write_text(json.dumps({"estado": "agendado", "youtube": {"shorts": [
                {"indice": 1, "id": "ok1"}, {"indice": 2, "id": "cid"}]}, "alertas": []}))
            (pasta / "metadados.json").write_text(json.dumps({"shorts": [{"indice": 1, "selecionado": True},
                                                                         {"indice": 2, "selecionado": True}]}))
            yt = YouTubeFalso()
            canal = SimpleNamespace(id="canal_r", caminho_db=Path(tmp) / "h.db")
            retirados = r.conferir(canal, yt)
            assert [x["id"] for x in retirados] == ["cid"] and HistoricoFalso.cancelados == ["cid"]
            assert yt.atualizados == [{"id": "cid", "status": {"embeddable": True, "privacyStatus": "private"}}]
            p = projeto_mod.Projeto(pasta)
            assert "país" in p.status["youtube"]["shorts"][1]["restricao"] and "restricao" not in p.status["youtube"]["shorts"][0]
            assert [s["selecionado"] for s in p.ler("metadados.json")["shorts"]] == [True, False]
            assert r.conferir(canal, yt) == [] and len(yt.atualizados) == 1, "o retirado não é mexido de novo"
        finally:
            projeto_mod.PASTA_PROJETOS, r.Historico = original, historico


@teste
def reagendar_poe_um_short_por_dia_no_mesmo_horario():
    import datetime as dt
    import json

    from esteira import projeto as projeto_mod
    from esteira.modulos import m6_publicacao as m6

    class Pedido:
        def __init__(self, valor):
            self.valor = valor

        def execute(self):
            return self.valor

    class YouTubeFalso:
        def __init__(self):
            self.datas = {}

        def videos(self):
            return self

        def list(self, part, id):
            return Pedido({"items": [{"id": i, "status": {"privacyStatus": "private", "embeddable": True}} for i in id.split(",")]})

        def update(self, part, body):
            self.datas[body["id"]] = body["status"]["publishAt"]
            return Pedido({})

    class HistoricoFalso:
        def __init__(self, *_):
            pass

        def reagendar_video(self, *_):
            pass

    fuso = "America/Sao_Paulo"
    amanha = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).astimezone(m6.ZoneInfo(fuso)).date()
    def em(hora: int, dias: int = 0) -> str:
        d = amanha + dt.timedelta(days=dias)
        return dt.datetime(d.year, d.month, d.day, hora, tzinfo=m6.ZoneInfo(fuso)).isoformat()

    original, historico = projeto_mod.PASTA_PROJETOS, m6.Historico
    with tempfile.TemporaryDirectory() as tmp:
        projeto_mod.PASTA_PROJETOS, m6.Historico = Path(tmp), HistoricoFalso
        try:
            pasta = Path(tmp) / "canal_a" / "p1"
            pasta.mkdir(parents=True)
            shorts = [{"indice": n, "id": f"v{n}", "publicar_em": h} for n, h in
                      enumerate([em(20), em(10, 1), em(13, 1), em(17, 1)], 1)]
            (pasta / "status.json").write_text(json.dumps({"estado": "agendado", "youtube": {"shorts": shorts}}))
            canal = SimpleNamespace(id="canal_a", caminho_db=Path(tmp) / "h.db", publicacao={
                "horarios_shorts": ["20:00"], "dias_semana": ["seg", "ter", "qua", "qui", "sex", "sab", "dom"],
                "fuso_horario": fuso, "antecedencia_min_horas": 3, "intervalo_min_minutos": 60})
            yt = YouTubeFalso()
            feitos = m6.reagendar_cortes(canal, yt)
            assert [f["indice"] for f in feitos] == [2, 3, 4], "o primeiro já estava às 20:00"
            novos = [dt.datetime.fromisoformat(s["publicar_em"]) for s in projeto_mod.Projeto(pasta).status["youtube"]["shorts"]]
            assert [n.hour for n in novos] == [20, 20, 20, 20] and len({n.date() for n in novos}) == 4
            assert len(yt.datas) == 3 and all(v.endswith("Z") for v in yt.datas.values())
        finally:
            projeto_mod.PASTA_PROJETOS, m6.Historico = original, historico


@teste
def short_repetido_nao_e_agendado():
    import json

    from esteira import projeto as projeto_mod
    from esteira.cortes import orquestrador as orq
    from esteira.modulos import m6_publicacao as m6

    def criar(raiz: Path, nome: str, status: dict, shorts: list[dict]) -> projeto_mod.Projeto:
        pasta = raiz / "canal_rep" / nome
        (pasta / "shorts").mkdir(parents=True)
        (pasta / "status.json").write_text(json.dumps({"estado": "pendente_revisao", "live": {"id": "v1"}, **status}))
        (pasta / "shorts" / "shorts.json").write_text(json.dumps(shorts))
        return projeto_mod.Projeto(pasta)

    original = projeto_mod.PASTA_PROJETOS
    with tempfile.TemporaryDirectory() as tmp:
        projeto_mod.PASTA_PROJETOS = Path(tmp)
        try:
            # o lote 1 (arquivo começa em 1:00:00) já mandou o Short 1 = 1:01:40–1:02:05 da live
            criar(Path(tmp), "lote1", {"arquivo_inicio": 3600, "youtube": {"shorts": [{"indice": 1, "id": "abc"}]}},
                  [{"indice": 1, "inicio": 100, "duracao": 25}])
            novo = criar(Path(tmp), "lote2", {"arquivo_inicio": 3650}, [
                {"indice": 1, "inicio": 52, "duracao": 23},    # 1:01:42 — o mesmo momento do lote 1
                {"indice": 2, "inicio": 200, "duracao": 25},   # momento novo
                {"indice": 3, "inicio": 201, "duracao": 23}])  # repete o Short 2 deste mesmo lote
            meta = {"shorts": [{"indice": n, "selecionado": True, "titulo": f"t{n}"} for n in (1, 2, 3)]}
            ficam, tirados = orq.shorts_sem_repeticao(novo, meta["shorts"])
            assert [s["indice"] for s in ficam] == [2] and [s["indice"] for s in tirados] == [1, 3]
            assert [s["indice"] for s in m6._sem_shorts_repetidos(novo, meta, meta["shorts"], False)] == [2]
            salvo = json.loads(novo.p("metadados.json").read_text(encoding="utf-8"))
            assert [s["selecionado"] for s in salvo["shorts"]] == [False, True, False] and salvo["shorts"][0]["repetido"]
        finally:
            projeto_mod.PASTA_PROJETOS = original


# ------------------------------------------------------------------ tradução
@teste
def traducao_distribui_palavras_no_tempo_da_fala():
    from esteira.cortes import traducao

    palavras = [{"palavra": w, "inicio": i * 0.4, "fim": i * 0.4 + 0.35}
                for i, w in enumerate("Behold the god of thunder. Watch me now.".split())]
    original = traducao.traduzir
    traducao.traduzir = lambda linhas, origem, contexto="": [f"PT {l}" for l in linhas]
    try:
        pt = traducao.palavras_traduzidas(palavras, "en")
    finally:
        traducao.traduzir = original
    assert pt and pt[0]["palavra"] == "PT"
    assert all(b["inicio"] >= a["inicio"] for a, b in zip(pt, pt[1:])), "tempos fora de ordem"
    assert pt[-1]["fim"] <= palavras[-1]["fim"] + 0.01
    assert traducao.precisa_traduzir("en") and not traducao.precisa_traduzir("pt") and not traducao.precisa_traduzir(None)


# ------------------------------------------------------------------ edição / moldura
@teste
def enquadramento_e_moldura():
    from esteira.cortes import edicao

    trilha = [(i / 2, 0.5 + (0.2 if i > 20 else 0)) for i in range(60)]
    x = edicao._expressao_x(trilha, 1920, 606)
    assert "clip(t-" in x and "+-" not in x
    assert edicao._expressao_x([(i / 2, 0.5) for i in range(20)], 1920, 606).isdigit()
    cadeia = edicao._cadeia_moldura("#000000")
    assert "pad=1080:1920:40:716:color=0x000000" in cadeia, cadeia


@teste
def perfil_do_canal_usa_conexao_do_youtube():
    from esteira import util
    from esteira.cortes import edicao

    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        (pasta / "credenciais").mkdir()
        util.salvar_json(pasta / "credenciais" / "canal_youtube.json", {"id": "UCx", "titulo": "Meu Canal", "handle": "meu_canal"})
        canal = SimpleNamespace(pasta=pasta, pasta_credenciais=pasta / "credenciais", handle="", nome="Interno")
        perfil = edicao.perfil_do_canal(canal)
    assert perfil["handle"] == "@meu_canal" and perfil["nome"] == "Meu Canal" and perfil["foto"] == ""


@teste
def legenda_ancorada_no_topo_na_moldura():
    from esteira.modulos.m4_shorts import gerar_ass

    palavras = [{"palavra": "Teste", "inicio": 0.0, "fim": 0.5}]
    topo = [l for l in gerar_ass(palavras, "Arial", "&H00FFFF&", 1312, 80, ancora=8).splitlines() if l.startswith("Dialogue")]
    centro = [l for l in gerar_ass(palavras, "Arial", "&H00FFFF&", 1500).splitlines() if l.startswith("Dialogue")]
    assert "\\an8\\pos(540,1312)" in topo[0] and "\\an" not in centro[0]


# ------------------------------------------------------------------ orquestrador / fontes
@teste
def links_do_painel():
    from esteira.cortes import orquestrador as o

    assert o.link_do_momento("https://www.youtube.com/watch?v=abc", 125.7) == "https://www.youtube.com/watch?v=abc&t=125s"
    assert o.link_do_momento("https://www.twitch.tv/videos/1", 3725) == "https://www.twitch.tv/videos/1?t=1h02m05s"
    for canal_url in ("https://www.twitch.tv/criador_a", "https://www.youtube.com/@canal_exemplo", "https://www.youtube.com/@canal_exemplo/streams",
                      "https://www.youtube.com/channel/UCx/videos"):
        assert o._LINK_CANAL.match(canal_url), canal_url
    for video in ("https://www.twitch.tv/videos/123", "https://www.youtube.com/watch?v=abc", "https://www.youtube.com/live/abc"):
        assert not o._LINK_CANAL.match(video), video
    assert o.url_do_canal({"uploader_id": "canal_exemplo"}, "https://www.twitch.tv/videos/1") == "https://www.twitch.tv/canal_exemplo"


@teste
def textos_editados_pelo_dono_sobrevivem_ao_refazer():
    from esteira.cortes import orquestrador as o

    canal = SimpleNamespace(editorial={"chamada_final": ""}, pasta_trilhas=Path(tempfile.gettempdir()) / "nao_existe_trilhas")
    r = {"indice": 1, "inicio": 10.0, "titulo": "Automático", "descricao": "auto", "tags": ["a"], "trilha": "x.mp3"}
    status = {"arquivo_inicio": 0}
    auto = o._meta_short(r, {"titulo": "Velho", "editado": False}, "", {"url": "https://y"}, canal, status)
    dono = o._meta_short(r, {"titulo": "Meu título", "descricao": "Minha", "tags": ["b"], "editado": True}, "",
                         {"url": "https://y"}, canal, status)
    assert auto["titulo"].startswith("Automático") and "editado" not in auto
    assert dono["titulo"] == "Meu título" and dono["descricao"] == "Minha" and dono["editado"] is True


@teste
def canal_conectado_nao_vira_fonte_e_vigia_por_fonte():
    from esteira import util
    from esteira.cortes import fontes

    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        (pasta / "credenciais").mkdir()
        util.salvar_json(pasta / "credenciais" / "canal_youtube.json", {"id": "UCabc", "titulo": "Canal X", "handle": "@canal_x"})
        canal = SimpleNamespace(pasta=pasta, pasta_credenciais=pasta / "credenciais", nome="Canal X")
        assert fontes.carregar(canal) == []  # o canal do YouTube conectado só posta: não entra nas fontes sozinho
        a = fontes.Fonte(nome="A", url="https://www.twitch.tv/a", plataforma="twitch", monitorar=False, ids=["a"])
        b = fontes.Fonte(nome="B", url="https://kick.com/b", plataforma="kick", monitorar=True, ids=["b"])
        fontes.salvar(canal, [a, b])
        assert fontes.buscar(canal, "https://www.TWITCH.tv/a/").nome == "A"
        assert fontes.buscar(canal, "http://twitch.tv/a").nome == "A" and fontes.buscar(canal, "https://www.kick.com/b").nome == "B"
        assert fontes.buscar(canal, "https://www.twitch.tv/outro") is None
        ligada = fontes.definir_monitorar(canal, "https://www.twitch.tv/a", True)
        assert ligada.monitorar is True and a.monitorar is False  # devolve uma cópia; o original fica igual
        assert [f.monitorar for f in fontes.carregar(canal)] == [True, True]
        antes = (pasta / "fontes.yaml").read_text(encoding="utf-8")
        assert fontes.definir_monitorar(canal, "https://www.twitch.tv/outro", False) is None
        assert (pasta / "fontes.yaml").read_text(encoding="utf-8") == antes
        assert fontes.localizar(canal, {"channel_id": "UCabc", "uploader_id": "@canal_x"}) is None
        assert fontes.remover(canal, "https://kick.com/b") and [f.nome for f in fontes.carregar(canal)] == ["A"]


@teste
def link_de_canal_vira_pagina_do_canal_sem_internet():
    from esteira.cortes import orquestrador

    for link, esperado in (("https://www.twitch.tv/criador_a/videos", "https://www.twitch.tv/criador_a"),
                           ("https://www.youtube.com/@canal_exemplo/streams", "https://www.youtube.com/@canal_exemplo"),
                           ("https://kick.com/criador_c/", "https://kick.com/criador_c"),
                           ("https://www.youtube.com/channel/UCabc", "https://www.youtube.com/channel/UCabc")):
        assert orquestrador.pagina_do_canal(link) == (esperado, None), link


# ------------------------------------------------------------------ música
@teste
def trilha_synthwave_original():
    from esteira.cortes import synthwave

    audio = synthwave.compor("estrada_turbo", compassos=4)
    assert audio.ndim == 2 and audio.shape[1] == 2
    assert 0.8 < abs(audio).max() <= 0.9 + 1e-6
    assert audio.shape[0] > synthwave.SR * 7


# ------------------------------------------------------------------ Twitch / Kick
@teste
def links_da_twitch_e_da_kick():
    from esteira.cortes import fontes
    from esteira.cortes import orquestrador as o

    assert fontes.plataforma_da_url("https://kick.com/criador_c/videos/01a1e087-41b8-7c14-a922-02cc6582a3f0") == "kick"
    assert fontes.plataforma_da_url("https://www.twitch.tv/videos/123") == "twitch"
    assert fontes.plataforma_da_url("https://clips.twitch.tv/AbcDef") == "twitch"
    assert fontes.plataforma_da_url("https://youtu.be/abc") == "youtube"
    for falso in ("https://evil.com/?x=twitch.tv", "https://twitch.tv.evil.com/videos/1", "https://notkick.com/a",
                  "https://evil.com\\@twitch.tv/videos/1", "http://127.0.0.1:8080@kick.com/a/videos/x",
                  "https://www.twitch.tv/videos/1 --exec x"):
        try:
            fontes.plataforma_da_url(falso)
        except ValueError:
            continue
        raise AssertionError(f"aceitou {falso}")
    for canal_url in ("https://kick.com/criador_c", "https://kick.com/criador_c/videos", "https://www.twitch.tv/criador_b/videos"):
        assert o._LINK_CANAL.match(canal_url), canal_url
    for video in ("https://kick.com/criador_c/videos/01a1e087-41b8-7c14-a922-02cc6582a3f0",
                  "https://kick.com/criador_c?clip=clip_01ABC", "https://www.twitch.tv/criador_b/clip/Abc-123"):
        assert not o._LINK_CANAL.match(video), video
    assert o.url_do_canal({"uploader_id": "criador_c", "channel_id": "1234567"},
                          "https://kick.com/criador_c/videos/x") == "https://kick.com/criador_c"
    assert fontes.endereco_curto("https://www.twitch.tv/criador_b/") == "twitch.tv/criador_b"
    kick = fontes.Fonte(nome="Criador C", url="https://kick.com/criador_c", plataforma="kick", tipo="autorizado")
    assert kick.texto_credito == "Live de Criador C · kick.com/criador_c"
    proprio = fontes.Fonte(nome="Eu", url="https://www.youtube.com/channel/UCx", plataforma="youtube", tipo="proprio")
    assert proprio.texto_credito == ""


@teste
def cadastro_de_criador_autorizado_usa_os_dados_da_live():
    from esteira.cortes import fontes

    with tempfile.TemporaryDirectory() as tmp:
        canal = SimpleNamespace(pasta=Path(tmp), nome="Canal")
        info = {"channel": "Criador C", "uploader": "Criador C", "uploader_id": "criador_c", "channel_id": "1234567"}
        try:
            fontes.adicionar(canal, "https://kick.com/criador_c", "autorizado", info=info)
        except ValueError:
            pass
        else:
            raise AssertionError("cadastrou criador de terceiros sem autorização")
        f = fontes.adicionar(canal, "https://kick.com/criador_c", "autorizado", nome="Criador C",
                             autorizacao="Confirmado no painel", monitorar=False, info=info)
        assert f.plataforma == "kick" and "criador_c" in f.ids and not f.monitorar
        assert fontes.localizar(canal, {"uploader_id": "Criador C"}) is not None


@teste
def plano_de_download_por_plataforma():
    from esteira.cortes import orquestrador as o

    cfg = {"layout": "moldura"}
    twitch = o.plano_download(cfg, "twitch", False, "00:30:00-01:00:00")
    assert twitch["secao"] == (1800.0, 3600.0) and twitch["arquivo_inicio"] == 1800.0
    assert twitch["hd_por_trecho"] and twitch["altura"] == 480 and twitch["altura_hd"] == 1080
    kick = o.plano_download(cfg, "kick", False, None)
    assert kick["secao"] is None and kick["arquivo_inicio"] == 0 and kick["hd_por_trecho"]
    youtube = o.plano_download(cfg, "youtube", False, "00:30:00-01:00:00")
    assert youtube["secao"] is None and not youtube["hd_por_trecho"] and youtube["altura"] == 720
    ao_vivo = o.plano_download(cfg, "kick", True, None)
    assert not ao_vivo["hd_por_trecho"]
    assert o.validar_trecho("00:30:00-01:00:00", 7200) == (1800.0, 3600.0) and o.validar_trecho("", 10) is None
    for ruim, duracao in (("01:00:00-00:30:00", None), ("00:10:00-00:10:10", None), ("03:00:00-03:30:00", 7200)):
        try:
            o.validar_trecho(ruim, duracao)
        except ValueError:
            continue
        raise AssertionError(f"aceitou o trecho {ruim}")


@teste
def trecho_hls_da_lista_de_pedacos():
    from esteira.cortes import hls

    lista = ("#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-MAP:URI=\"init-0.mp4\"\n"
             + "".join(f"#EXTINF:10.000,\n{i}.mp4\n" for i in range(5)) + "#EXTINF:4.5,\n5-unmuted.mp4\n")
    init, pedacos = hls.ler_playlist(lista, "https://cdn.exemplo/abc/chunked/index-dvr.m3u8")
    assert init == "https://cdn.exemplo/abc/chunked/init-0.mp4" and len(pedacos) == 6
    assert pedacos[3].inicio == 30.0 and pedacos[5].url.endswith("/5-unmuted.mp4")
    assert [p.inicio for p in hls.selecionar(pedacos, 25.0, 41.0)] == [20.0, 30.0, 40.0]
    assert hls.selecionar(pedacos, 100.0, 110.0) == []
    formatos = [{"protocol": "m3u8_native", "height": 1080, "fps": 60, "url": "a"},
                {"protocol": "m3u8_native", "height": 480, "fps": 30, "url": "b"},
                {"protocol": "m3u8_native", "height": None, "vcodec": "none", "url": "audio"},
                {"protocol": "https", "height": 720, "url": "mp4"}]
    assert hls.escolher_formato(formatos, 480)["url"] == "b" and hls.escolher_formato(formatos, 1080)["url"] == "a"
    assert hls.escolher_formato(formatos, 360) is None


@teste
def live_no_ar_corta_so_a_parte_ja_gravada():
    from esteira.cortes import orquestrador as o

    no_ar = {"is_live": True, "duration": 21000}
    assert o.parte_ja_gravada("twitch", no_ar, "01:00:00-01:45:00")
    assert not o.parte_ja_gravada("twitch", no_ar, "05:40:00-05:55:00")  # ainda não aconteceu
    assert not o.parte_ja_gravada("twitch", no_ar, None)
    assert not o.parte_ja_gravada("youtube", no_ar, "01:00:00-01:45:00")
    assert not o.parte_ja_gravada("kick", {"is_live": False, "duration": 21000}, "01:00:00-01:45:00")
    assert not o._sobrepoe((3600, 6300), (6300, 9000)) and o._sobrepoe((3600, 6300), (6000, 7000))
    assert o._sobrepoe(None, (3600, 6300)) and o._sobrepoe((3600, 6300), None)


# ------------------------------------------------------------------ hashtags fixas
@teste
def hashtags_iguais_em_todos_os_shorts():
    from esteira.cortes import hashtags
    from esteira.cortes import orquestrador as o

    cfg = {"hashtags_fixas": ["#JogoX", "Gameplay", "jogox", "Jogo X Clips"], "hashtags_titulo": ["JogoX", "shorts"],
           "tags_fixas": ["jogo x", "cortes de live"]}
    assert hashtags.fixas(cfg) == ["JogoX", "Gameplay", "JogoXClips"]
    assert hashtags.linha(cfg, ["a"]) == hashtags.linha(cfg, ["b", "c"]) == "#JogoX #Gameplay #JogoXClips"
    assert hashtags.linha({}, ["Marvel Rivals"]).startswith("#shorts")
    assert hashtags.fixas({"hashtags_fixas": "JogoX, Gameplay #Cidade"}) == ["JogoX", "Gameplay", "Cidade"]
    com_link = {**cfg, "convite_link": "https://exemplo.com/cortes", "convite_texto": "Faça cortes: {link}"}
    assert hashtags.convite(cfg) == "" and hashtags.com_convite("Texto\n\n#JogoX", cfg) == "Texto\n\n#JogoX"
    desc = hashtags.com_convite("Texto do corte\n\n#JogoX #Gameplay", com_link)
    assert desc == "Texto do corte\n\nFaça cortes: https://exemplo.com/cortes\n\n#JogoX #Gameplay", desc
    assert hashtags.com_convite(desc, com_link) == desc  # não repete
    assert "https://exemplo.com/cortes" in hashtags.finalizar("T", "Escrito à mão", [], com_link)[1]
    longo = hashtags.titulo("x" * 120, cfg)
    assert len(longo) <= 100 and longo.endswith("#JogoX #shorts")
    assert hashtags.titulo("Fulano no #JogoX", cfg) == "Fulano no #JogoX #shorts"
    titulo, descricao, tags = hashtags.finalizar("Meu título", "Texto meu.\n\n#Gameplay", ["jogo x", "personagem"], cfg)
    assert titulo == "Meu título #JogoX #shorts"
    assert descricao.endswith("#JogoX #JogoXClips") and descricao.count("#Gameplay") == 1
    assert tags == ["jogo x", "cortes de live", "personagem"]
    canal = SimpleNamespace(editorial={"chamada_final": ""}, cfg={"cortes": cfg},
                            pasta_trilhas=Path(tempfile.gettempdir()) / "nao_existe_trilhas")
    metas = [o._meta_short({"indice": i, "inicio": 10.0 * i, "titulo": f"Corte {i}", "descricao": "d", "tags": [t]},
                           {}, "Live de X · kick.com/x", {"url": "https://kick.com/x/videos/1"}, canal, {"arquivo_inicio": 0})
             for i, t in ((1, "assalto"), (2, "policia"))]
    linhas = {m["descricao"].strip().splitlines()[-1] for m in metas}
    assert linhas == {"#JogoX #Gameplay #JogoXClips"}, linhas
    assert all(m["titulo"].endswith("#JogoX #shorts") and m["tags"][:2] == ["jogo x", "cortes de live"] for m in metas)


# ------------------------------------------------------------------ câmera e layout
@teste
def webcam_fixa_vira_tela_dividida():
    from esteira.cortes import camera

    amostras = []
    for i in range(20):  # webcam no canto (presente em 85%) + rostos do jogo andando pela tela
        rostos = [(820.0 + (i % 3), 420.0, 70.0)] if i % 7 else []
        rostos.append((100.0 + 37 * i, 200.0, 60.0))
        amostras.append(rostos)
    fc = camera.agrupar(amostras, 960, 540)
    assert fc is not None and abs(fc.cx - 820 / 960) < 0.01 and fc.presenca >= 0.8, fc
    assert camera.escolher_layout(fc) == "dividido"
    assert camera.escolher_layout(camera.Facecam(0.5, 0.4, 0.2, 0.9)) == "vertical"
    assert camera.escolher_layout(None) == "moldura"
    assert camera.agrupar([[(100.0 + 50 * i, 200.0, 60.0)] for i in range(10)], 960, 540) is None  # só rostos do jogo
    x, y, w, h = camera.caixa_camera(fc, 1920, 1080)
    assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1080 and abs(w / h - 1080 / camera.CAM_ALTURA) < 0.02
    jx, _, jw, jh = camera.caixa_jogo(1920, 1080, (x, y, w, h))
    assert jh == 1080 and abs(jw / jh - 1080 / camera.JOGO_ALTURA) < 0.01 and (jx + jw <= x or jx >= x + w)


@teste
def zoom_nos_picos_de_reacao():
    from esteira.cortes import efeitos

    passo = efeitos.PASSO
    db = [-32.0] * int(30 / passo)
    for inicio in (6.0, 18.0):  # dois gritos
        for k in range(int(inicio / passo), int((inicio + 0.6) / passo)):
            db[k] = -12.0
    janelas = efeitos.picos_de_reacao(db, 30.0)
    assert len(janelas) == 2 and abs(janelas[0][0] - 6.0) < 0.4 and abs(janelas[1][0] - 18.0) < 0.4, janelas
    assert efeitos.picos_de_reacao([-30.0] * 600, 30.0) == []
    assert efeitos.cadeia_zoom("a", "b", 1000, 562, []) == "[a]null[b];"
    cadeia = efeitos.cadeia_zoom("a", "b", 1000, 562, janelas)
    assert "enable='between(t,6.0" in cadeia and cadeia.endswith("[b];") and "crop=876:492" in cadeia, cadeia


@teste
def layout_e_filtros_do_short():
    from esteira.cortes import edicao

    status = {"live": {"plataforma": "kick"}, "opcoes": {}}
    assert edicao.layout_pedido({"layout": "moldura"}, status) == "auto"
    assert edicao.layout_pedido({"layout_twitch_kick": "dividido"}, status) == "dividido"
    assert edicao.layout_pedido({}, {**status, "opcoes": {"layout": "vertical"}}) == "vertical"
    assert edicao.layout_pedido({}, status, {"layout": "moldura"}) == "moldura"
    assert edicao.layout_pedido({"layout": "moldura"}, {"live": {"plataforma": "youtube"}}) == "moldura"
    cadeia = edicao._cadeia_dividido((1500, 700, 400, 252), (480, 0, 940, 1080), [(2.0, 3.1)], ",eq=contrast=1.05")
    assert "vstack=inputs=2" in cadeia and "crop=400:252:1500:700" in cadeia and "[cam1]" in cadeia and cadeia.endswith("[c0];")
    moldura = edicao._cadeia_moldura("#000000", [(1.0, 2.0)], ",eq=contrast=1.05")
    assert "pad=1080:1920:40:716:color=0x000000" in moldura and "enable=" in moldura


# ------------------------------------------------------------------ painel (rotas chamadas direto, sem rede)
@teste
def painel_recusa_links_e_canais_errados():
    from painel import contas, servidor, sessao

    contas.iniciar()
    dono = contas.criar_usuario("dono@exemplo.com", "Dono", "senha-forte-123", "admin")
    token = sessao.USUARIO.set(dono)
    eh_cortes = servidor._eh_cortes
    servidor._eh_cortes = lambda canal: canal == "canal_teste"  # o canal é "dele" neste teste
    try:
        _rotas_recusam_links_errados(servidor)
    finally:
        servidor._eh_cortes = eh_cortes
        sessao.USUARIO.reset(token)
    assert servidor._TRILHA_SEGURA.fullmatch("synthwave_a.mp3")
    assert not servidor._TRILHA_SEGURA.fullmatch("../segredo.mp3") and not servidor._TRILHA_SEGURA.fullmatch("x.sh")


def _rotas_recusam_links_errados(servidor) -> None:
    from fastapi import HTTPException

    casos = (({"canal": "canal_teste", "url": "abc"}, 400), ({"canal": "nao_existe", "url": "https://x"}, 404),
             ({"canal": "canal_teste", "url": "https://kick.com/criador_c"}, 400),  # página do canal, não a live
             ({"canal": "canal_teste", "url": "https://evil.com/?x=twitch.tv"}, 400),
             ({"canal": "canal_teste", "id": "../../etc"}, 400))
    for rota in (servidor.processar_live, servidor.conferir_link):
        for corpo, codigo in casos:
            try:
                rota(corpo)
            except HTTPException as exc:
                assert exc.status_code == codigo, (rota.__name__, corpo, exc.status_code, exc.detail)
            else:
                raise AssertionError(f"{rota.__name__} aceitou {corpo}")
    _rotas_de_fontes_recusam_pedidos_errados(servidor)


def _rotas_de_fontes_recusam_pedidos_errados(servidor) -> None:
    """Aba Canais: nada aqui usa a internet nem grava no fontes.yaml (tudo é recusado antes)."""
    from fastapi import HTTPException

    desconhecido = "https://www.twitch.tv/canal_que_nao_existe_xyz"
    casos = ((servidor.lives_da_fonte, ("canal_teste",), {}, 404),  # sem dizer qual canal integrado
             (servidor.lives_da_fonte, ("canal_teste",), {"fonte": desconhecido}, 404),
             (servidor.lives_da_fonte, ("nao_existe",), {"fonte": desconhecido}, 404),
             (servidor.vigiar_fonte, ({"canal": "canal_teste", "url": desconhecido, "monitorar": True},), {}, 404),
             (servidor.remover_fonte, ({"canal": "canal_teste", "url": desconhecido},), {}, 404),
             (servidor.adicionar_fonte, ({"canal": "canal_teste", "url": "https://evil.com/?x=twitch.tv", "tipo": "proprio"},), {}, 400),
             (servidor.adicionar_fonte, ({"canal": "canal_teste", "url": "https://twitch.tv/x", "tipo": "dono"},), {}, 400),
             (servidor.adicionar_fonte, ({"canal": "canal_teste", "url": "https://twitch.tv/" + "x" * 600, "tipo": "proprio"},), {}, 400))
    for rota, args, kwargs, codigo in casos:
        try:
            rota(*args, **kwargs)
        except HTTPException as exc:
            assert exc.status_code == codigo, (rota.__name__, args, kwargs, exc.status_code, exc.detail)
        else:
            raise AssertionError(f"{rota.__name__} aceitou {args} {kwargs}")


def main() -> int:
    falhas = 0
    for func in TESTES:
        try:
            func()
            print(f"  ✔ {func.__name__}")
        except Exception:
            falhas += 1
            print(f"  ✖ {func.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTES) - falhas}/{len(TESTES)} testes passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
