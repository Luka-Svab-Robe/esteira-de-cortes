#!/usr/bin/env python3
"""Testes offline da plataforma multiusuário (contas, sessão, etapas, copiloto e checagem).

Uso:  python testes/teste_plataforma.py
Roda com banco e projetos temporários: não toca em dados/, canais/ nem projetos/ de verdade e não usa internet.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("ESTEIRA_SEM_VENV", "1")
_TMP = tempfile.TemporaryDirectory(prefix="plataforma_teste_")
os.environ["PLATAFORMA_DADOS"] = str(Path(_TMP.name) / "dados")  # antes de importar painel.*


def _criar_canal_de_teste() -> None:
    """O projeto vem sem canais: os testes usam um canal de cortes temporário ("canal_teste"), fora de canais/."""
    from esteira import canal

    canal.PASTA_CANAIS = Path(_TMP.name) / "canais"
    canal.criar_canal("canal_teste", "Canal de Teste", "cortes de lives", "animado", "roxo e amarelo", "rapido",
                      tipo="cortes")


_criar_canal_de_teste()

TESTES = []


def teste(func):
    TESTES.append(func)
    return func


def _espera_http(funcao, status: int, *args, **kwargs):
    from fastapi import HTTPException

    try:
        funcao(*args, **kwargs)
    except HTTPException as exc:
        assert exc.status_code == status, (exc.status_code, exc.detail)
        return exc.detail
    raise AssertionError(f"esperava HTTP {status}")


def _usuario(email: str = "ana@exemplo.com", papel: str = "admin") -> dict:
    from painel import contas

    contas.iniciar()
    existente = next((u for u in contas.listar_usuarios() if u["email"] == email), None)
    return existente or contas.criar_usuario(email, "Ana", "senha-forte-123", papel)


def _projeto_falso(raiz: Path, estado: str = "aguardando_etapa") -> Path:
    pasta = raiz / "canal_teste" / "2026-11-17_cortes-teste"
    (pasta / "analise").mkdir(parents=True)
    (pasta / "status.json").write_text(json.dumps({
        "estado": estado, "live": {"titulo": "Live de teste", "duracao": 3600, "url": "https://www.youtube.com/watch?v=abcdefgh"},
        "fonte": {"nome": "Canal Teste", "tipo": "proprio"}, "etapas": {}, "eventos": []}), encoding="utf-8")
    candidatos = [{"id": 1, "inicio": 100.0, "fim": 130.0, "pontuacao": 3.2, "texto": "[ __ ] olha essa jogada insana. Ninguém esperava isso."},
                  {"id": 2, "inicio": 400.0, "fim": 430.0, "pontuacao": 2.1, "texto": "Agora a perseguição começa de verdade."}]
    (pasta / "analise" / "candidatos.json").write_text(json.dumps(candidatos), encoding="utf-8")
    return pasta


class _ServidorIsolado:
    """Aponta o painel para uma pasta de projetos temporária e libera o canal de teste."""

    def __enter__(self):
        from painel import servidor, sessao

        self.servidor, self.tmp = servidor, tempfile.TemporaryDirectory()
        self.antigos = (servidor.PASTA_PROJETOS, servidor._eh_cortes)
        servidor.PASTA_PROJETOS = Path(self.tmp.name)
        servidor._eh_cortes = lambda canal: canal == "canal_teste"
        self.token = sessao.USUARIO.set(_usuario())
        return Path(self.tmp.name)

    def __exit__(self, *erro):
        from painel import sessao

        self.servidor.PASTA_PROJETOS, self.servidor._eh_cortes = self.antigos
        sessao.USUARIO.reset(self.token)
        self.tmp.cleanup()


# ------------------------------------------------------------------ contas
@teste
def senha_sessao_e_conexoes_cifradas():
    from painel import contas

    u = _usuario()
    assert contas.autenticar("ANA@exemplo.com", "senha-forte-123")["id"] == u["id"]
    assert contas.autenticar("ana@exemplo.com", "errada") is None
    token = contas.criar_sessao(u["id"], "teste")
    assert contas.usuario_da_sessao(token)["id"] == u["id"]
    contas.encerrar_sessao(token)
    assert contas.usuario_da_sessao(token) is None
    contas.salvar_conexoes(u["id"], {"ANTHROPIC_API_KEY": "sk-ant-segredo-1234"})
    with sqlite3.connect(contas.BANCO) as banco:
        bruto = banco.execute("SELECT valor FROM conexoes WHERE chave='ANTHROPIC_API_KEY'").fetchone()[0]
    assert "segredo" not in bruto, "a chave foi gravada sem cifrar"
    mascarada = next(c for c in contas.conexoes_mascaradas(u["id"]) if c["chave"] == "ANTHROPIC_API_KEY")
    assert "segredo" not in mascarada["valor"] and mascarada["valor"].endswith("1234") and mascarada["configurado"]
    assert contas.ambiente_do_usuario(u["id"])["ANTHROPIC_API_KEY"] == "sk-ant-segredo-1234"


@teste
def canal_de_outro_usuario_fica_invisivel():
    from painel import contas

    ana, bia = _usuario(), _usuario("bia@exemplo.com", "usuario")
    contas.atribuir_canal("canal_da_ana_teste", ana["id"])
    assert contas.dono_do_canal("canal_da_ana_teste") == ana["id"]
    assert "canal_da_ana_teste" not in contas.canais_do_usuario(bia["id"])


# ------------------------------------------------------------------ etapas
@teste
def opcoes_da_live_validadas():
    from painel import rotas_etapas as r

    assert r.validar_opcoes({"max_cortes_por_live": "3", "modelo": "centro", "traduzir": 0}) == \
        {"max_cortes_por_live": 3, "modelo": "centro", "traduzir": False}
    for ruim in ({"max_cortes_por_live": 50}, {"modelo": "outro"}, {"duracao_min": 40, "duracao_max": 30}, "x"):
        _espera_http(r.validar_opcoes, 400, ruim)


@teste
def opcoes_da_live_sobrepoem_o_canal():
    from esteira.cortes import orquestrador as o

    canal = SimpleNamespace(cfg={"cortes": {"max_cortes_por_live": 5, "modelo": "padrao"}})
    projeto = SimpleNamespace(status={"opcoes": {"max_cortes_por_live": 2, "modelo": "direita", "layout": "hack"}})
    cfg = o.cfg_cortes(canal, projeto)
    assert cfg["max_cortes_por_live"] == 2 and cfg["modelo"] == "direita" and "layout" not in cfg
    assert canal.cfg["cortes"]["max_cortes_por_live"] == 5, "não pode alterar a configuração do canal"


@teste
def momentos_editados_a_mao():
    from esteira.projeto import Projeto
    from painel import rotas_etapas as r

    with _ServidorIsolado() as raiz:
        pasta = _projeto_falso(raiz, "pendente_revisao")
        corpo = {"cortes": [{"inicio": 400, "fim": 428, "gancho": "A *perseguição*", "tags": "jogo, fuga"},
                            {"inicio": 98, "fim": 131, "gancho": "Jogada insana", "titulo": "Que jogada!"}]}
        resposta = r.salvar_momentos("canal_teste", pasta.name, corpo)
        cortes = json.loads((pasta / "analise" / "cortes.json").read_text(encoding="utf-8"))
        assert resposta["ok"] and [c["indice"] for c in cortes] == [1, 2] and cortes[0]["inicio"] == 98
        assert cortes[0]["automatico"] is False and "[" not in cortes[0]["texto"] and cortes[0]["pontuacao"] == 3.2
        assert cortes[1]["tags"] == ["jogo", "fuga"] and cortes[1]["titulo"] == "A perseguição"
        p = Projeto(pasta)
        assert p.estado == "aguardando_etapa" and p.status["edicao_desatualizada"] is True
        _espera_http(r.salvar_momentos, 400, "canal_teste", pasta.name, {"cortes": [{"inicio": 1, "fim": 3, "gancho": "x"}]})
        _espera_http(r.salvar_momentos, 400, "canal_teste", pasta.name, {"cortes": [{"inicio": 10, "fim": 40, "gancho": " "}]})
        _espera_http(r.salvar_momentos, 400, "canal_teste", pasta.name, {"cortes": [{"inicio": 3590, "fim": 3700, "gancho": "fim"}]})
        (pasta / "analise" / "cortes.json").write_text(json.dumps([{"inicio": 400, "fim": 428, "gancho": "Run now",
            "titulo": "Run now!", "automatico": True}]), encoding="utf-8")
        r.salvar_momentos("canal_teste", pasta.name, {"cortes": [
            {"inicio": 400, "fim": 428, "gancho": "Run now", "titulo": "Run now!"},
            {"inicio": 98, "fim": 131, "gancho": "Meu gancho", "titulo": "Meu título"}]})
        salvos = json.loads((pasta / "analise" / "cortes.json").read_text(encoding="utf-8"))
        assert [c["automatico"] for c in salvos] == [False, True], "texto automático intocado deve continuar traduzível"
        repetido = r.salvar_momentos("canal_teste", pasta.name, {"cortes": [  # "＋ Usar" num trecho já escolhido
            {"inicio": 98, "fim": 131, "gancho": "Meu gancho"}, {"inicio": 400, "fim": 428, "gancho": "Outro"},
            {"inicio": 99, "fim": 130, "gancho": "Mesmo trecho"}]})
        assert repetido["repetidos"] == 1 and [c["gancho"] for c in repetido["cortes"]] == ["Meu gancho", "Outro"]
        _espera_http(r.ver_momentos, 404, "outro_canal", pasta.name)
        visto = r.ver_momentos("canal_teste", pasta.name)
        assert visto["editavel"] and len(visto["candidatos"]) == 2 and visto["video"] is None


@teste
def etapa_recusada_sem_video_ou_em_estado_errado():
    from painel import rotas_etapas as r

    with _ServidorIsolado() as raiz:
        pasta = _projeto_falso(raiz)
        _espera_http(r.rodar_etapa, 404, "canal_teste", pasta.name, "publicar", {})
        assert "baixada" in _espera_http(r.rodar_etapa, 409, "canal_teste", pasta.name, "edicao", {})["mensagem"]
        status = json.loads((pasta / "status.json").read_text(encoding="utf-8"))
        (pasta / "status.json").write_text(json.dumps({**status, "estado": "agendado"}), encoding="utf-8")
        _espera_http(r.rodar_etapa, 409, "canal_teste", pasta.name, "analise", {})
        _espera_http(r.salvar_momentos, 409, "canal_teste", pasta.name, {"cortes": [{"inicio": 10, "fim": 40, "gancho": "a"}]})


# ------------------------------------------------------------------ copiloto e checagem
@teste
def copiloto_sem_ia_usa_a_fala_do_trecho():
    from painel import copiloto

    antigos = copiloto._cliente_do_usuario, copiloto._via_ollama
    copiloto._cliente_do_usuario, copiloto._via_ollama = (lambda uid: None), (lambda campo, ctx, uid: None)
    try:
        ctx = {"fala": "Olha essa jogada insana do Paulo. Ninguém esperava isso no final. Que absurdo foi esse.",
               "titulo_live": "Live de jogo"}
        for campo in copiloto.CAMPOS:
            r = copiloto.sugerir(1, campo, ctx)
            assert r["motor"] == "regras" and r["sugestoes"], (campo, r)
            assert all(len(s) <= copiloto.LIMITE_CAMPO[campo] for s in r["sugestoes"])
        assert len(copiloto.sugerir(1, "gancho", ctx)["sugestoes"][0].split()) <= 6
    finally:
        copiloto._cliente_do_usuario, copiloto._via_ollama = antigos


@teste
def copiloto_limpa_resposta_da_ia():
    from painel import copiloto

    assert copiloto._limpar("tags", [["#Jogo", "Fuga "], "jogo, fuga"]) == ["jogo, fuga"]
    assert copiloto._limpar("titulo", ['"Um título"', "x" * 200])[1] == "x" * 90


@teste
def checagem_automatica_aponta_problemas():
    from painel import rotas_etapas as r

    bom = {"indice": 1, "video": "/v.mp4", "duracao": 40, "gancho": "Que *jogada*", "trilha": "a.mp3"}
    texto_bom = {"titulo": "Jogada insana", "descricao": "Corte da live de Canal X", "tags": ["jogo"]}
    assert r._checar_short(bom, texto_bom, "Canal X", {"duracao_min": 20})["nivel"] == "ok"
    ruim = r._checar_short({**bom, "video": None, "duracao": 200}, {**texto_bom, "titulo": "t" * 120}, "Canal X",
                           {"duracao_min": 20})
    textos = " ".join(i["texto"] for i in ruim["itens"])
    assert ruim["nivel"] == "erro" and "3 minutos" in textos and "120 letras" in textos and "não foi gerado" in textos
    assert r._checar_short(bom, {**texto_bom, "descricao": "sem crédito"}, "Canal X", {"duracao_min": 20})["nivel"] == "aviso"


# ------------------------------------------------------------------ progresso
@teste
def progresso_real_sem_log_bruto():
    from esteira.projeto import Projeto
    from painel import progresso

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "envio.log"
        log.write_text("[youtube] abc: Downloading\n[download]  10.0% of 1GiB\r[download]  50.0% of 1GiB\n", encoding="utf-8")
        assert progresso.pct_download(log) == 50.0
        assert progresso.do_processo(str(log), True, None) == {"pct": 21, "rotulo": "Baixando a live (50%)"}
        log.write_text("Traceback...\nERROR: HTTP Error 403: Forbidden\n", encoding="utf-8")
        assert "recusou" in progresso.do_processo(str(log), False, 1)["rotulo"]
        pasta = _projeto_falso(Path(tmp), "em_producao")
        p = Projeto(pasta)
        p.progresso(5, "Baixando a live")
        log.write_text("[download]  80.0%\n", encoding="utf-8")
        assert progresso.do_projeto(p, str(log))["pct"] == 31
        log.write_text("[download]   3.0%\n", encoding="utf-8")  # 2ª passada (áudio): a barra não volta
        assert progresso.do_projeto(p, str(log))["pct"] == 31
        p.progresso(72.5, "Editando o Short 2 de 2")
        assert progresso.do_projeto(p) == {"pct": 72, "rotulo": "Editando o Short 2 de 2"}
        p.definir_estado("erro", "edicao: ffmpeg exited with code 1 Traceback (most recent call last)")
        assert progresso.do_projeto(p)["rotulo"] == progresso.MENSAGEM_GENERICA
    assert progresso.mensagem_de_erro("Duração inválida (3 s): use entre 5 e 60 s.").startswith("Duração inválida")


# ------------------------------------------------------------------ segurança
@teste
def endpoint_do_usuario_nunca_recebe_a_chave_da_plataforma():
    from painel import contas

    u = _usuario("carla@exemplo.com", "usuario")
    contas.salvar_conexoes(u["id"], {"OPENAI_BASE_URL": "http://meu-servidor.exemplo:1234/v1"})
    env = contas.ambiente_do_usuario(u["id"])
    assert env["OPENAI_API_KEY"] == "" and env["LLM_PROVEDOR"] == "openai", env


@teste
def primeiro_admin_so_uma_vez():
    from painel import contas

    _usuario()  # já existe alguém
    try:
        contas.criar_usuario("intruso@exemplo.com", "Intruso", "senha-forte-123", "admin", primeiro=True)
    except contas.PlataformaJaConfigurada:
        pass
    else:
        raise AssertionError("criou um segundo administrador pelo primeiro acesso")
    assert not any(x["email"] == "intruso@exemplo.com" for x in contas.listar_usuarios())


@teste
def conexoes_recusam_enderecos_de_metadados():
    from painel.rotas_conta import _endereco_permitido

    for ruim in ("http://169.254.169.254/latest", "http://metadata.google.internal/", "http://[fe80::1]/"):
        assert not _endereco_permitido(ruim), ruim
    for bom in ("http://localhost:11434", "https://api.openai.com/v1", "http://192.168.0.10:1234/v1"):
        assert _endereco_permitido(bom), bom


@teste
def limitador_de_tentativas_bloqueia_e_poda():
    from fastapi import HTTPException

    from painel import sessao

    for _ in range(3):
        sessao.registrar_tentativa("teste|limite", limite=3, janela=60)
    try:
        sessao.registrar_tentativa("teste|limite", limite=3, janela=60, mensagem="calma")
    except HTTPException as exc:
        assert exc.status_code == 429 and exc.detail["mensagem"] == "calma"
    else:
        raise AssertionError("não limitou")
    sessao._tentativas.update({f"velha{i}": [0.0] for i in range(sessao.MAX_CHAVES_TENTATIVAS + 1)})
    sessao.registrar_tentativa("teste|nova", limite=3, janela=60)
    assert len(sessao._tentativas) < 10, len(sessao._tentativas)


# ------------------------------------------------------------------ legenda
@teste
def legenda_editada_a_mao_e_refeita():
    from esteira.cortes import edicao
    from esteira.projeto import Projeto
    from painel import rotas_legenda as r, servidor

    with _ServidorIsolado() as raiz:
        pasta = _projeto_falso(raiz, "pendente_revisao")
        (pasta / "shorts").mkdir()
        (pasta / "cortes").mkdir()
        (pasta / "analise" / "cortes.json").write_text(json.dumps([{"indice": 1, "inicio": 100.0, "fim": 130.0, "gancho": "g"}]), encoding="utf-8")
        (pasta / "shorts" / "shorts.json").write_text(json.dumps([{"indice": 1, "arquivo": "shorts/corte_1.mp4", "duracao": 30.0,
                                                                   "legenda_traduzida_de": "en"}]), encoding="utf-8")
        (pasta / "cortes" / "corte_1_palavras.json").write_text(json.dumps([{"palavra": "Look", "inicio": 0.5, "fim": 0.9}]), encoding="utf-8")
        (pasta / "cortes" / "corte_1_palavras_pt.json").write_text(json.dumps(
            [{"palavra": "Olha", "inicio": 0.5, "fim": 0.9}, {"palavra": "isso!", "inicio": 0.9, "fim": 1.4},
             {"palavra": "Corre", "inicio": 3.0, "fim": 3.5}]), encoding="utf-8")
        visto = r.ver_legenda("canal_teste", pasta.name, 1)  # projeto antigo: usa a tradução guardada
        assert [f["texto"] for f in visto["frases"]] == ["Olha isso!", "Corre"] and visto["editavel"] and not visto["editada"]
        disparos = []
        antigo = servidor._iniciar_processo
        servidor._iniciar_processo = lambda canal, args, url, **extra: disparos.append(args) or {"id": "x"}
        try:
            _espera_http(r.salvar_legenda, 400, "canal_teste", pasta.name, 1, {"frases": [{"inicio": 5, "fim": 40, "texto": "longe"}]})
            r.salvar_legenda("canal_teste", pasta.name, 1, {"frases": [
                {"inicio": 0.5, "fim": 1.4, "texto": "Olha esse *lance*!"}, {"inicio": 1.0, "fim": 3.5, "texto": "Corre, Paulo"},
                {"inicio": 4, "fim": 5, "texto": "  "}]})
        finally:
            servidor._iniciar_processo = antigo
        assert disparos and disparos[0][:2] == ["cortes", "reeditar"] and "--indice" in disparos[0]
        editada = json.loads((pasta / "cortes" / "corte_1_legenda_editada.json").read_text(encoding="utf-8"))
        palavras = editada["palavras"]
        assert [w["palavra"] for w in palavras] == ["Olha", "esse", "*lance*!", "Corre,", "Paulo"]
        assert palavras[3]["inicio"] >= 1.4, "linha sobreposta deve começar depois da anterior"
        assert editada["traduzida_de"] == "en" and editada["inicio"] == 100.0
        p = Projeto(pasta)
        corte = p.ler("analise/cortes.json")[0]
        assert corte["legenda_editada"] is True
        assert edicao.legenda_editada(p, corte)["palavras"] == palavras
        assert edicao.legenda_editada(p, {**corte, "inicio": 101.0}) is None, "trecho mudou: volta à automática"
        status = json.loads((pasta / "status.json").read_text(encoding="utf-8"))
        (pasta / "status.json").write_text(json.dumps({**status, "estado": "agendado"}), encoding="utf-8")
        _espera_http(r.salvar_legenda, 409, "canal_teste", pasta.name, 1, {"automatica": True})


@teste
def cadastrar_canal_e_agenda_de_publicacao():
    from esteira.canal import Canal
    from painel import contas, rotas_agenda, rotas_conta, sessao

    with _ServidorIsolado():
        dono = sessao.usuario_atual()
        criado = rotas_conta.criar_canal({"nome": "Meu Canal Novo", "nicho": "humor"})
        novo = Canal.carregar(criado["canal"])
        assert novo.editorial["tipo_canal"] == "cortes" and contas.dono_do_canal(novo.id) == dono["id"]
        _espera_http(rotas_conta.criar_canal, 400, {"nome": "ab"})

        caminho = Canal.carregar("canal_teste").pasta / "identidade.yaml"
        original = caminho.read_text(encoding="utf-8")
        try:
            r = rotas_agenda.salvar_agenda_do_canal("canal_teste", {"horarios": ["21:00", "09:30", "21:00"], "dias": ["sab", "seg", "xx"]})
            assert r["horarios"] == ["09:30", "21:00"] and r["dias"] == ["seg", "sab"], r
            pub = Canal.carregar("canal_teste").publicacao
            assert pub["horarios_shorts"] == ["09:30", "21:00"] and pub["dias_semana"] == ["seg", "sab"]
            texto = caminho.read_text(encoding="utf-8")
            assert texto.count("fuso_horario: America/Sao_Paulo") == 1 and "cortes:" in texto  # o resto ficou igual
            for corpo in ({"horarios": ["25:00"], "dias": ["seg"]}, {"horarios": [], "dias": ["seg"]},
                          {"horarios": ["10:00"], "dias": []}, {"horarios": [f"{h:02d}:00" for h in range(13)], "dias": ["seg"]}):
                _espera_http(rotas_agenda.salvar_agenda_do_canal, 400, "canal_teste", corpo)
            _espera_http(rotas_agenda.salvar_agenda_do_canal, 404, "canal_de_outro", {"horarios": ["10:00"], "dias": ["seg"]})
        finally:
            caminho.write_text(original, encoding="utf-8")


def _pedido(host: str, cliente: str) -> SimpleNamespace:
    return SimpleNamespace(headers={"host": host}, client=SimpleNamespace(host=cliente), cookies={})


@teste
def primeiro_acesso_escolhe_com_ou_sem_senha():
    from painel import contas, rotas_conta, sessao

    antigos = (contas.PASTA_DADOS, contas.BANCO, contas.CHAVE)
    with tempfile.TemporaryDirectory() as tmp:  # banco só deste teste: o primeiro acesso exige ninguém cadastrado
        contas.PASTA_DADOS, contas.BANCO, contas.CHAVE = Path(tmp), Path(tmp) / "p.db", Path(tmp) / ".chave"
        try:
            contas.iniciar()
            assert contas.modo_acesso() is None and contas.usuario_aberto() is None
            local, rede = _pedido("127.0.0.1:8765", "127.0.0.1"), _pedido("minha-maquina.lan:8765", "192.168.0.5")
            assert sessao.acesso_local(local) and sessao.acesso_local(_pedido("[::1]:8765", "::1"))
            assert not sessao.acesso_local(rede) and not sessao.acesso_local(_pedido("127.0.0.1:8765", "192.168.0.5"))
            assert not sessao.acesso_local(_pedido("minha-maquina.lan", "127.0.0.1"))  # nome de rede liberado: pede senha
            _espera_http(rotas_conta.primeiro_acesso, 400, rede, {"modo": "aberto"})  # sem senha só neste computador

            resposta = rotas_conta.primeiro_acesso(local, {"modo": "aberto", "nome": "Ana"})
            dono = resposta["usuario"]
            assert resposta["modo"] == "aberto" and dono["papel"] == "admin" and dono["nome"] == "Ana"
            assert sessao.usuario_da_requisicao(local)["id"] == dono["id"]
            assert sessao.usuario_da_requisicao(rede) is None  # pela rede continua fechado
            assert contas.autenticar(contas.EMAIL_LOCAL, "") is None  # a conta local não entra pela tela de login
            _espera_http(rotas_conta.primeiro_acesso, 409, local, {"modo": "aberto"})
            try:
                contas.criar_usuario("outro@exemplo.com", "Outro", "senha-forte-123", "admin", primeiro=True)
                raise AssertionError("criou um segundo primeiro acesso")
            except contas.PlataformaJaConfigurada:
                pass

            token = sessao.USUARIO.set(dono)
            try:
                _espera_http(rotas_conta.criar_usuario, 409, {"email": "equipe@exemplo.com", "nome": "E", "senha": "senha-forte-123"})
            finally:
                sessao.USUARIO.reset(token)
            for email, senha in ((contas.EMAIL_LOCAL, "senha-forte-123"), ("ana@exemplo.com", "curta")):
                try:
                    contas.proteger_conta(dono["id"], email, senha)
                    raise AssertionError(f"aceitou {email!r}/{senha!r}")
                except ValueError:
                    pass
            protegido = contas.proteger_conta(dono["id"], "ana@exemplo.com", "senha-forte-123")
            assert contas.modo_acesso() == "protegido" and contas.usuario_aberto() is None
            assert sessao.usuario_da_requisicao(local) is None
            assert contas.autenticar("ana@exemplo.com", "senha-forte-123")["id"] == protegido["id"]

            equipe = contas.criar_usuario("equipe@exemplo.com", "Equipe", "senha-forte-123")
            for senha in ("errada", "senha-forte-123"):  # senha errada; depois a equipe ainda ativa
                try:
                    contas.liberar_sem_senha(protegido["id"], senha)
                    raise AssertionError("liberou sem senha")
                except ValueError:
                    pass
            contas.definir_ativo(equipe["id"], False)
            contas.liberar_sem_senha(protegido["id"], "senha-forte-123")
            assert contas.modo_acesso() == "aberto" and contas.usuario_aberto()["id"] == protegido["id"]

            with contas._con() as con:  # banco antigo (sem a tabela de modo) e com contas: continua pedindo senha
                con.execute("DELETE FROM config")
            assert contas.modo_acesso() == "protegido" and contas.usuario_aberto() is None
        finally:
            contas.PASTA_DADOS, contas.BANCO, contas.CHAVE = antigos


@teste
def aba_apis_guarda_o_app_do_google_sem_mostrar_o_segredo():
    """Aba APIs: só o administrador grava o app do Google; o segredo nunca volta na resposta; um canal só fica
    'conectado' quando o token existe; cada usuário vê só os próprios canais de cortes."""
    from esteira import canal as canal_mod
    from esteira.canal import Canal
    from esteira.modulos import m6_publicacao
    from painel import contas, rotas_apis, sessao

    ana, bia = _usuario(), _usuario("bia@exemplo.com", "usuario")
    canal_mod.criar_canal("canal_apis_teste", "Canal APIs", "cortes de lives", "animado", "roxo", "rapido", tipo="cortes")
    contas.atribuir_canal("canal_apis_teste", ana["id"])
    id_valido = "123456789012-abcdefgh.apps.googleusercontent.com"
    segredo = "segredo-de-teste-9876"  # valor falso, sem o prefixo de segredo real do Google
    with tempfile.TemporaryDirectory() as tmp:
        arquivo = Path(tmp) / "credenciais" / "client_secret.json"  # nunca o credenciais/ de verdade
        original = m6_publicacao.caminho_segredo
        m6_publicacao.caminho_segredo = lambda: arquivo
        token = sessao.USUARIO.set(ana)
        try:
            antes = rotas_apis.ver_apis_youtube()
            assert antes["app"]["configurado"] is False and antes["pode_editar_app"] is True, antes
            por_id = {c["id"]: c for c in antes["canais"]}
            assert por_id["canal_apis_teste"]["conectado"] is False, por_id
            _espera_http(rotas_apis.salvar_app_youtube, 400, {"client_id": "nao-e-um-id", "client_secret": segredo})
            _espera_http(rotas_apis.salvar_app_youtube, 400, {"client_id": id_valido, "client_secret": "curto"})
            assert not arquivo.exists(), "credenciais inválidas não podem ser gravadas"

            outra = sessao.USUARIO.set(bia)
            try:
                _espera_http(rotas_apis.salvar_app_youtube, 403, {"client_id": id_valido, "client_secret": segredo})
                visto_pela_bia = rotas_apis.ver_apis_youtube()
                assert visto_pela_bia["pode_editar_app"] is False
                assert "canal_apis_teste" not in [c["id"] for c in visto_pela_bia["canais"]], "canal de outro usuário apareceu"
            finally:
                sessao.USUARIO.reset(outra)

            salvo = rotas_apis.salvar_app_youtube({"client_id": id_valido, "client_secret": segredo})
            assert salvo["configurado"] is True and salvo["client_id"] == id_valido, salvo
            assert "segredo-de-teste" not in json.dumps(salvo) and salvo["segredo"].endswith("9876"), salvo
            gravado = json.loads(arquivo.read_text(encoding="utf-8"))["installed"]
            assert gravado["client_secret"] == segredo and gravado["token_uri"].startswith("https://"), gravado
            if os.name != "nt":  # no Windows a permissão POSIX não se aplica
                assert arquivo.stat().st_mode & 0o777 == 0o600, oct(arquivo.stat().st_mode)

            depois = rotas_apis.ver_apis_youtube()
            assert "segredo-de-teste" not in json.dumps(depois), "o segredo vazou pela aba APIs"
            assert depois["app"]["configurado"] is True
            por_id = {c["id"]: c for c in depois["canais"]}
            assert por_id["canal_apis_teste"]["conectado"] is False

            token_canal = Canal.carregar("canal_apis_teste").caminho_token_youtube
            token_canal.parent.mkdir(parents=True, exist_ok=True)
            token_canal.write_text("{}", encoding="utf-8")  # simula a autorização do canal feita no login
            conectado = rotas_apis.ver_apis_youtube()
            assert {c["id"]: c for c in conectado["canais"]}["canal_apis_teste"]["conectado"] is True
            assert conectado["pronto"] is True, conectado  # app + pelo menos um canal conectado

            arquivo.write_text("{não é json", encoding="utf-8")  # arquivo corrompido não derruba a aba
            corrompido = rotas_apis.ver_apis_youtube()
            assert corrompido["app"]["configurado"] is False and corrompido["app"]["erro"], corrompido["app"]
        finally:
            sessao.USUARIO.reset(token)
            m6_publicacao.caminho_segredo = original


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
    _TMP.cleanup()
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
