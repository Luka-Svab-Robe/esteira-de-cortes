"""Orquestração: encadeia os módulos, controla estados e abre o painel de revisão."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from urllib.parse import quote

import requests

from . import log, metadados, util
from .canal import Canal
from .config import PASTA_LOGS, RAIZ, env_bool, env_int
from .historico import Historico
from .modulos import m1_roteiro, m2_narracao, m3_montagem, m4_shorts, m4_thumbnails, m6_publicacao
from .projeto import ETAPAS_PRODUCAO, Projeto


def _indice(etapa: str) -> int:
    if etapa not in ETAPAS_PRODUCAO:
        raise ValueError(f"Etapa desconhecida '{etapa}'. Use uma de: {', '.join(ETAPAS_PRODUCAO)}")
    return ETAPAS_PRODUCAO.index(etapa)


def executar_etapa(projeto: Projeto, canal: Canal, etapa: str, blocos: set[int] | None = None,
                   forcar: bool = False, sem_llm: bool = False) -> None:
    if etapa == "roteiro":
        tema = projeto.status.get("tema", "")
        bruto_arq = projeto.p("pesquisa", "roteiro_bruto.txt")
        bruto = bruto_arq.read_text(encoding="utf-8") if bruto_arq.exists() else None
        m1_roteiro.executar(canal, tema, bruto, forcar=True, sem_llm=sem_llm, projeto=projeto)
    elif etapa == "narracao":
        m2_narracao.executar(projeto, canal, blocos, forcar)
    elif etapa == "montagem":
        m3_montagem.executar(projeto, canal, blocos, forcar)
    elif etapa == "shorts":
        m4_shorts.executar(projeto, canal, forcar)
    elif etapa == "thumbnails":
        m4_thumbnails.executar(projeto, canal, forcar)


def continuar(projeto: Projeto, a_partir_de: str = "narracao", ate: str = "thumbnails",
              blocos: set[int] | None = None, forcar: bool = False, abrir_painel: bool = True,
              sem_llm: bool = False) -> Projeto:
    canal = Canal.carregar(projeto.canal_id)
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    tinha_reprovacao = bool(projeto.reprovacoes_pendentes())
    projeto.definir_estado("em_producao", f"etapas {a_partir_de} → {ate}")
    etapa = a_partir_de
    inicio = time.time()
    try:
        for etapa in ETAPAS_PRODUCAO[_indice(a_partir_de): _indice(ate) + 1]:
            executar_etapa(projeto, canal, etapa, blocos, forcar, sem_llm)
            if projeto.estado == "aguardando_roteiro":
                return projeto
    except Exception as exc:
        projeto.marcar_etapa(etapa, False, erro=str(exc)[:500])
        projeto.definir_estado("erro", f"{etapa}: {str(exc)[:300]}")
        log.erro(f"Falha na etapa '{etapa}': {exc}")
        raise
    if tinha_reprovacao:
        projeto.resolver_reprovacoes(f"reprocessado a partir de '{a_partir_de}'")
    if ate == "thumbnails":
        projeto.definir_estado("pendente_revisao")
        Historico(canal.caminho_db).atualizar(projeto.id, estado="pendente_revisao")
        log.ok(f"Produção concluída em {int(time.time() - inicio)}s: {projeto.pasta}")
        if abrir_painel:
            abrir_painel_web(projeto)
    return projeto


def produzir(canal_id: str, tema: str | None = None, roteiro_bruto: str | None = None, forcar: bool = False,
             modo_agente: bool = False, sem_llm: bool = False, abrir_painel: bool = True,
             ate: str = "thumbnails") -> Projeto:
    canal = Canal.carregar(canal_id)
    projeto = m1_roteiro.executar(canal, tema, roteiro_bruto, forcar=forcar, modo_agente=modo_agente, sem_llm=sem_llm)
    if projeto.estado == "aguardando_roteiro" or ate == "roteiro":
        return projeto
    return continuar(projeto, "narracao", ate, abrir_painel=abrir_painel, sem_llm=sem_llm)


def importar_roteiro(projeto: Projeto, arquivo, continuar_producao: bool = True, abrir_painel: bool = True) -> Projeto:
    canal = Canal.carregar(projeto.canal_id)
    log.anexar_arquivo(projeto.p("logs", "execucao.log"))
    m1_roteiro.importar_roteiro(projeto, canal, arquivo)
    if continuar_producao:
        return continuar(projeto, "narracao", abrir_painel=abrir_painel)
    return projeto


# ---------------------------------------------------------------- decisões
def salvar_edicoes(projeto: Projeto, edicoes: dict) -> dict:
    canal = Canal.carregar(projeto.canal_id)
    meta = projeto.ler("metadados.json", {}) or {}
    for campo in ("titulo", "descricao_base", "tags", "hashtags", "capitulos", "frases_thumbnail"):
        if campo in edicoes and edicoes[campo] is not None:
            meta[campo] = edicoes[campo]
    if edicoes.get("thumbnail_escolhida"):
        meta["thumbnail_escolhida"] = int(edicoes["thumbnail_escolhida"])
    if isinstance(edicoes.get("shorts"), list):
        por_indice = {int(s["indice"]): s for s in edicoes["shorts"] if "indice" in s}
        for short in meta.get("shorts", []):
            novo = por_indice.get(int(short["indice"]))
            if novo:
                antes = (short.get("titulo"), short.get("descricao"), short.get("tags"), short.get("selecionado"))
                short["selecionado"] = bool(novo.get("selecionado", short.get("selecionado", True)))
                short["titulo"] = str(novo.get("titulo", short.get("titulo", "")))[:100]
                if "descricao" in novo:  # Shorts de cortes têm descrição e tags próprias
                    short["descricao"] = metadados._limpar_youtube(str(novo["descricao"]))[:4900]
                if isinstance(novo.get("tags"), list):
                    short["tags"] = metadados.limitar_tags([str(t) for t in novo["tags"]])
                if antes != (short.get("titulo"), short.get("descricao"), short.get("tags"), short.get("selecionado")):
                    short["editado"] = True
    meta["editado_em"] = util.agora_iso()
    projeto.salvar("metadados.json", meta)
    return metadados.atualizar(projeto, canal)


def aprovar(projeto: Projeto, edicoes: dict | None = None, publicar: bool = True) -> dict:
    canal = Canal.carregar(projeto.canal_id)
    if edicoes:
        salvar_edicoes(projeto, edicoes)
    projeto.definir_estado("aprovado")
    Historico(canal.caminho_db).atualizar(projeto.id, estado="aprovado")
    if not publicar:
        return {"estado": "aprovado"}
    try:
        resultado = m6_publicacao.publicar(projeto, canal)
        return {"estado": "agendado", "youtube": resultado}
    except m6_publicacao.ErroPublicacao as exc:
        log.erro(str(exc))
        if projeto.estado in ("aprovado", "publicando"):
            projeto.definir_estado("erro_publicacao", str(exc))
        return {"estado": projeto.estado, "erro": str(exc)}


def reprovar(projeto: Projeto, motivo: str, etapas: list[str] | None = None, blocos: list[int] | None = None) -> dict:
    if not motivo.strip():
        raise ValueError("Descreva o motivo da reprovação.")
    canal = Canal.carregar(projeto.canal_id)
    registro = projeto.reprovar(motivo, etapas, blocos)
    Historico(canal.caminho_db).atualizar(projeto.id, estado="reprovado")
    return registro


def descartar(projeto: Projeto) -> None:
    canal = Canal.carregar(projeto.canal_id)
    projeto.definir_estado("descartado")
    Historico(canal.caminho_db).atualizar(projeto.id, estado="descartado")


# ---------------------------------------------------------------- painel
def _painel_ativo(porta: int) -> bool:
    try:
        return requests.get(f"http://127.0.0.1:{porta}/api/saude", timeout=1.5).ok
    except requests.RequestException:
        return False


def iniciar_painel_em_segundo_plano(porta: int) -> None:
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    saida = open(PASTA_LOGS / "painel.log", "a", encoding="utf-8")
    opcoes: dict = {"cwd": str(RAIZ), "stdout": saida, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        opcoes["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        opcoes["start_new_session"] = True
    subprocess.Popen([sys.executable, str(RAIZ / "esteira.py"), "painel", "--sem-navegador", "--porta", str(porta)],
                     **opcoes)
    for _ in range(40):
        if _painel_ativo(porta):
            return
        time.sleep(0.5)
    log.aviso(f"O painel demorou a responder; veja {PASTA_LOGS / 'painel.log'}")


def abrir_painel_web(projeto: Projeto | None = None, abrir_navegador: bool = True) -> str:
    porta = env_int("PAINEL_PORTA", 8765)
    if not _painel_ativo(porta):
        iniciar_painel_em_segundo_plano(porta)
    url = f"http://127.0.0.1:{porta}/" + (f"#/painel/{quote(projeto.id, safe='')}" if projeto else "")
    if abrir_navegador and env_bool("PAINEL_ABRIR_NAVEGADOR", True):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    log.ok(f"Painel de revisão: {url}")
    return url
