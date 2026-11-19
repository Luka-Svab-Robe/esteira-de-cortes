#!/usr/bin/env python3
"""Esteira Canal Dark — linha de comando.

Pensada para ser operada por um agente de IA (Claude Code, Codex CLI…) a partir
de pedidos em linguagem natural; veja AGENTS.md. Ajuda: python esteira.py -h
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent


def _usar_venv() -> None:
    """Reexecuta dentro de .venv quando ele existe (assim 'python esteira.py' sempre funciona)."""
    if os.environ.get("ESTEIRA_SEM_VENV"):
        return
    venv = RAIZ / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists() and Path(sys.prefix).resolve() != venv.resolve():
        os.environ["ESTEIRA_SEM_VENV"] = "1"
        if os.name == "nt":
            import subprocess

            sys.exit(subprocess.call([str(python), *sys.argv]))
        os.execv(str(python), [str(python), *sys.argv])

_usar_venv()
sys.path.insert(0, str(RAIZ))

import argparse  # noqa: E402
import json  # noqa: E402

from esteira import log, util  # noqa: E402


SAIDA_DUPLICADO = 3
SAIDA_AGUARDANDO_AGENTE = 4


def _blocos(texto: str | None) -> set[int] | None:
    if not texto:
        return None
    blocos: set[int] = set()
    for parte in texto.replace(" ", "").split(","):
        if "-" in parte:
            ini, fim = parte.split("-", 1)
            blocos.update(range(int(ini), int(fim) + 1))
        elif parte:
            blocos.add(int(parte))
    return blocos


def _projeto(ref: str):
    from esteira.projeto import Projeto

    return Projeto.localizar(ref)


def _imprimir_status(projeto) -> None:
    from esteira.projeto import ESTADOS

    status = projeto.status
    meta = projeto.ler("metadados.json", {}) or {}
    print(f"Projeto : {projeto.id}")
    print(f"Pasta   : {projeto.pasta}")
    print(f"Tema    : {status.get('tema')}")
    print(f"Título  : {meta.get('titulo', '')}")
    print(f"Estado  : {ESTADOS.get(status.get('estado'), status.get('estado'))}"
          + (f" — {status['detalhe_estado']}" if status.get("detalhe_estado") else ""))
    for etapa, dados in status.get("etapas", {}).items():
        print(f"  {'✔' if dados.get('concluida') else '✖'} {etapa:<11} {dados.get('em', '')}"
              + (f"  erro: {dados['erro']}" if dados.get("erro") else ""))
    for alerta in status.get("alertas", []):
        print(f"  ⚠ [{alerta['etapa']}] {alerta['mensagem']}")
    for rep in projeto.reprovacoes_pendentes():
        print(f"  ✖ REPROVADO em {rep['em']}: {rep['motivo']}")
        print(f"      sugestão: {rep['sugestao_agente']}")
    if status.get("youtube"):
        print("  YouTube:", json.dumps(status["youtube"], ensure_ascii=False))

# ------------------------------------------------------------------ comandos
def cmd_diagnostico(args) -> int:
    from esteira import diagnostico

    return 1 if diagnostico.imprimir(diagnostico.verificar(not args.rapido)) else 0


def cmd_canal(args) -> int:
    from esteira.canal import Canal, criar_canal

    if args.acao == "listar":
        canais = Canal.listar()
        if not canais:
            print("Nenhum canal criado. Use: python esteira.py canal criar …")
        for canal_id in canais:
            c = Canal.carregar(canal_id)
            print(f"- {canal_id}: {c.nome} | estilo {c.estilo} | {len(c.fila_listar())} tema(s) na fila | nicho: {c.nicho}")
        return 0
    if args.acao == "mostrar":
        c = Canal.carregar(args.canal)
        print((c.pasta / "identidade.yaml").read_text(encoding="utf-8"))
        return 0
    if args.acao == "criar":
        canal = criar_canal(args.id or args.nome, args.nome, args.nicho, args.tom, args.paleta, args.ritmo,
                            args.estilo, args.handle or "", args.publico or "", args.duracao, args.tipo)
        log.ok(f"Canal criado: {canal.id} (estilo {canal.estilo}) em {canal.pasta}")
        print(f"  Edite a identidade em {canal.pasta / 'identidade.yaml'} e adicione temas em {canal.caminho_fila}")
        print(f"CANAL={canal.id}")
        return 0
    return 2


def cmd_fila(args) -> int:
    from esteira.canal import Canal

    canal = Canal.carregar(args.canal)
    if args.acao == "listar":
        itens = canal.fila_listar()
        print(f"Fila de '{canal.nome}' ({len(itens)} tema(s)):")
        for i, item in enumerate(itens, 1):
            print(f"  {i}. {item}")
    elif args.acao == "adicionar":
        for tema in args.tema:
            canal.fila_adicionar(tema, no_inicio=args.inicio)
            log.ok(f"Adicionado à fila: {tema}")
    elif args.acao == "remover":
        for tema in args.tema:
            print(("✔ removido: " if canal.fila_remover(tema) else "⚠ não encontrado: ") + tema)
    return 0


def cmd_verificar_tema(args) -> int:
    from esteira.canal import Canal
    from esteira.historico import Historico
    from esteira.llm import obter_cliente

    canal = Canal.carregar(args.canal)
    resultado = Historico(canal.caminho_db).verificar_duplicidade(args.tema, llm=None if args.sem_llm else obter_cliente())
    print(("✖ BLOQUEADO: " if resultado.bloqueado else ("⚠ " if resultado.em_duvida else "✔ ")) + resultado.motivo)
    for registro, pontuacao in resultado.similares[:5]:
        print(f"   {pontuacao:.0%}  {registro['tema']}  [{registro['estado']}]")
    return SAIDA_DUPLICADO if resultado.bloqueado else 0


def cmd_produzir(args) -> int:
    from esteira import orquestrador

    roteiro = None
    if args.roteiro_arquivo:
        roteiro = Path(args.roteiro_arquivo).read_text(encoding="utf-8")
    elif args.roteiro_texto:
        roteiro = args.roteiro_texto
    projeto = orquestrador.produzir(args.canal, args.tema, roteiro, forcar=args.forcar, modo_agente=args.modo_agente,
                                    sem_llm=args.sem_llm, abrir_painel=not args.sem_painel, ate=args.ate)
    print(f"\nPROJETO={projeto.id}\nESTADO={projeto.estado}\nPASTA={projeto.pasta}")
    if projeto.estado == "aguardando_roteiro":
        print(f"PEDIDO_ROTEIRO={projeto.p('pesquisa', 'pedido_roteiro.md')}")
        return SAIDA_AGUARDANDO_AGENTE
    return 0


def cmd_importar_roteiro(args) -> int:
    from esteira import orquestrador

    projeto = orquestrador.importar_roteiro(_projeto(args.projeto), Path(args.arquivo), not args.sem_continuar,
                                            not args.sem_painel)
    print(f"\nPROJETO={projeto.id}\nESTADO={projeto.estado}")
    return 0


def cmd_continuar(args) -> int:
    from esteira import orquestrador

    projeto = orquestrador.continuar(_projeto(args.projeto), args.a_partir_de, args.ate, _blocos(args.blocos),
                                     args.forcar, not args.sem_painel, args.sem_llm)
    print(f"\nPROJETO={projeto.id}\nESTADO={projeto.estado}")
    return 0


def cmd_editar_bloco(args) -> int:
    projeto = _projeto(args.projeto)
    roteiro = projeto.roteiro()
    bloco = next((b for b in roteiro["blocos"] if b["id"] == args.bloco), None)
    if not bloco:
        raise SystemExit(f"Bloco {args.bloco} não existe (o roteiro tem {len(roteiro['blocos'])} blocos).")
    etapa = None
    if args.narracao is not None:
        bloco["narracao"], bloco["texto_fonetico"], etapa = args.narracao.strip(), None, "narracao"
    if args.fonetico is not None:
        bloco["texto_fonetico"], etapa = args.fonetico.strip() or None, "narracao"
    for campo, valor in (("modo_visual", args.modo), ("tipo_cartao", args.tipo_cartao), ("busca_visual", args.busca),
                         ("destaque", args.destaque), ("midia_arquivo", args.arquivo_midia)):
        if valor is not None:
            bloco[campo] = valor
            etapa = etapa or "montagem"
    if args.dados_cartao:
        bloco["dados_cartao"] = {**bloco.get("dados_cartao", {}), **json.loads(args.dados_cartao)}
        etapa = etapa or "montagem"
    if args.trocar_midia:
        bloco["midia_variante"] = int(bloco.get("midia_variante", 0)) + 1
        bloco["midia_arquivo"] = None
        etapa = etapa or "montagem"
    if args.short is not None:
        bloco["potencial_short"] = args.short == "sim"
        etapa = etapa or "shorts"
    projeto.salvar_roteiro(roteiro)
    projeto.evento(f"Bloco {args.bloco} editado manualmente")
    log.ok(f"Bloco {args.bloco} atualizado.")
    if etapa:
        print(f"PROXIMO_PASSO=python esteira.py continuar --projeto {projeto.id} --a-partir-de {etapa}")
    return 0


def cmd_projetos(args) -> int:
    from esteira.projeto import Projeto

    projetos = Projeto.listar(args.canal, tuple(args.estado.split(",")) if args.estado else None)
    if not projetos:
        print("Nenhum projeto encontrado.")
    for p in projetos:
        r = p.resumo()
        extra = f" | {r['reprovacoes_pendentes']} reprovação(ões)" if r["reprovacoes_pendentes"] else ""
        print(f"- {r['id']} | {r['estado_texto']}{extra} | {r['titulo']}")
    return 0


def cmd_status(args) -> int:
    _imprimir_status(_projeto(args.projeto))
    return 0


def cmd_pendencias(args) -> int:
    from esteira.projeto import Projeto

    projetos = Projeto.listar(args.canal, ("reprovado", "aguardando_roteiro", "erro", "erro_publicacao"))
    if not projetos:
        print("Nenhuma pendência.")
    for p in projetos:
        print("—" * 70)
        _imprimir_status(p)
    return 0


def cmd_painel(args) -> int:
    from painel.servidor import iniciar

    iniciar(porta=args.porta, abrir_navegador=not args.sem_navegador)
    return 0


def cmd_app(args) -> int:
    from esteira import app_desktop

    if args.acao == "instalar":
        atalho = app_desktop.instalar_atalho()
        log.ok(f"App instalado no menu do sistema: {app_desktop.NOME_APP} ({atalho})")
    elif args.acao == "remover":
        removido = app_desktop.remover_atalho()
        log.ok("Atalho removido do menu." if removido else "Não havia atalho no menu.")
    else:
        app_desktop.abrir_app()
    return 0


def cmd_abrir_painel(args) -> int:
    from esteira import orquestrador

    orquestrador.abrir_painel_web(_projeto(args.projeto) if args.projeto else None)
    return 0


def cmd_aprovar(args) -> int:
    from esteira import orquestrador

    resultado = orquestrador.aprovar(_projeto(args.projeto), publicar=not args.sem_publicar)
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0 if "erro" not in resultado else 1


def cmd_reprovar(args) -> int:
    from esteira import orquestrador

    registro = orquestrador.reprovar(_projeto(args.projeto), args.motivo)
    print(f"Reprovado. Sugestão para o agente: {registro['sugestao_agente']}")
    return 0


def cmd_descartar(args) -> int:
    from esteira import orquestrador

    orquestrador.descartar(_projeto(args.projeto))
    log.ok("Projeto descartado (o tema volta a ficar disponível para o canal).")
    return 0


def cmd_publicar(args) -> int:
    from esteira.canal import Canal
    from esteira.modulos import m6_publicacao

    projeto = _projeto(args.projeto)
    resultado = m6_publicacao.publicar(projeto, Canal.carregar(projeto.canal_id), simular=args.simular, forcar=args.forcar)
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_cancelar_agendamento(args) -> int:
    from esteira.canal import Canal
    from esteira.modulos import m6_publicacao

    projeto = _projeto(args.projeto)
    resultado = m6_publicacao.cancelar_agendamento(projeto, Canal.carregar(projeto.canal_id))
    log.ok(f"{len(resultado['privados'])} envio(s) tirados da agenda (ficaram privados no YouTube)"
           + (f"; {len(resultado['nao_encontrados'])} não encontrados (já apagados?)" if resultado["nao_encontrados"] else ""))
    print(f"PROJETO={projeto.id}\nESTADO={projeto.estado}")
    return 0


def cmd_trilhas(args) -> int:
    from esteira.canal import Canal
    from esteira.cortes import edicao, synthwave

    canal = Canal.carregar(args.canal)
    if args.acao == "gerar":
        criadas = synthwave.gerar_pacote(canal.pasta_trilhas)
        log.ok(f"{len(criadas)} trilha(s) original(is) nova(s)" if criadas else "O canal já tem as trilhas originais.")
    for t in edicao.trilhas_do_canal(canal):
        print(f"  - {t.name}{'  (original da esteira)' if t.name.startswith('synthwave_') else ''}")
    return 0


def cmd_youtube(args) -> int:
    from esteira.canal import Canal
    from esteira.modulos import m6_publicacao

    canal = Canal.carregar(args.canal)
    if args.acao == "autenticar":
        info = m6_publicacao.autenticar(canal, abrir_navegador=not args.sem_navegador)
        log.ok(f"Canal '{canal.id}' conectado ao YouTube: {info.get('titulo', '?')} ({info.get('id', '?')})")
    elif args.acao == "perfil":
        info = m6_publicacao.atualizar_perfil(canal)
        log.ok(f"Perfil atualizado: {info.get('titulo', '?')} {info.get('handle', '')} | foto: {info.get('avatar', 'não baixada')}")
    elif args.acao == "restricoes":  # Short com restrição no YouTube fica privado e sai da agenda
        from esteira.modulos import restricoes_youtube

        retirados = restricoes_youtube.conferir(canal)
        for r in retirados:
            print(f"  - retirado: Short {r['indice']} de {r['projeto']} — {r['motivo']}")
        print(f"RETIRADOS={len(retirados)}")
    elif args.acao == "reagendar":  # espalha os Shorts já agendados pelos horários atuais do canal
        feitos = m6_publicacao.reagendar_cortes(canal)
        print(f"REAGENDADOS={len(feitos)}")
    elif args.acao == "agenda":
        from zoneinfo import ZoneInfo

        fuso = ZoneInfo(canal.publicacao["fuso_horario"])
        datas = sorted(m6_publicacao.agenda_youtube(m6_publicacao.servico(canal)))
        print(f"{len(datas)} publicação(ões) agendada(s) no YouTube:")
        for d in datas:
            print(f"  - {d.astimezone(fuso):%d/%m/%Y %H:%M}")
    return 0


def cmd_worker(args) -> int:
    from esteira import automacao

    automacao.executar_worker(args.canal, args.quantidade)
    return 0


def cmd_automacao(args) -> int:
    from esteira import automacao

    if args.acao == "ativar":
        print(automacao.ativar(args.canal, args.horario))
    elif args.acao == "pausar":
        print(automacao.pausar(args.canal))
    else:
        estado = automacao.status()
        if not estado:
            print("Nenhuma automação configurada.")
        for canal_id, dados in estado.items():
            situacao = "ATIVA" if dados.get("ativo") else "pausada"
            print(f"- {canal_id}: {situacao} | {dados.get('horario', '-')} | via {dados.get('metodo', '-')}"
                  + (f" | próxima execução: {dados['proxima_execucao']}" if dados.get("proxima_execucao") else "")
                  + ("" if dados.get("confirmado_no_sistema", True) or not dados.get("ativo")
                     else " (⚠ agendamento não encontrado no sistema)"))
    return 0


def cmd_modelos(args) -> int:
    from esteira import modelos

    if not args.sem_kokoro:
        modelos.baixar_kokoro(args.kokoro)
    if not args.sem_whisper:
        modelos.baixar_whisper(args.whisper)
    return 0

# ------------------------------------------------------------------ parser
def cmd_fontes(args) -> int:
    from esteira.canal import Canal
    from esteira.cortes import fontes

    canal = Canal.carregar(args.canal)
    if args.acao == "listar":
        lista = fontes.carregar(canal)
        if not lista:
            print("Nenhuma fonte cadastrada. Use: python esteira.py fontes adicionar ...")
        for f in lista:
            print(f"- {f.nome} | {f.plataforma} | {f.tipo} | monitorar: {'sim' if f.monitorar else 'não'} | {f.url}"
                  + (f"\n    autorização: {f.autorizacao}" if f.autorizacao else ""))
    elif args.acao == "adicionar":
        if not args.url:
            raise SystemExit("Informe --url do canal (Twitch, Kick ou YouTube).")
        f = fontes.adicionar(canal, args.url, args.tipo, args.nome or "", args.credito or "", args.autorizacao or "",
                             not args.sem_monitorar)
        log.ok(f"Fonte cadastrada: {f.nome} ({f.plataforma}, {f.tipo}) — ids {', '.join(f.ids)}")
    elif args.acao == "remover":
        print("✔ removida" if fontes.remover(canal, args.nome or args.url or "") else "⚠ fonte não encontrada")
    return 0


def _opcoes_json(texto: str | None) -> dict:
    if not texto:
        return {}
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--opcoes precisa ser JSON válido: {exc}")
    if not isinstance(dados, dict):
        raise SystemExit("--opcoes precisa ser um objeto JSON, ex.: '{\"max_cortes_por_live\": 3}'")
    return dados


def cmd_cortes(args) -> int:
    from esteira.cortes import orquestrador as cortes

    if args.acao == "processar":
        if not (args.canal and args.url):
            raise SystemExit("Informe --canal e --url da live.")
        projeto = cortes.processar(args.canal, args.url, args.trecho, args.modo_agente, args.sem_llm,
                                   not args.sem_painel, args.forcar, ate=args.ate, opcoes=_opcoes_json(args.opcoes),
                                   autorizacao=args.autorizacao)
    elif args.acao == "reeditar":

        projeto = _projeto(args.projeto)
        cortes.reeditar_corte(projeto, args.indice, gancho=args.gancho, inicio=args.inicio, fim=args.fim,
                              trilha=args.trilha, modelo=args.modelo, layout=args.layout)
        print(f"PROJETO={projeto.id}\nESTADO={projeto.estado}")
        return 0
    elif args.acao == "importar":
        projeto = cortes.importar_selecao(_projeto(args.projeto), Path(args.arquivo), not args.sem_painel)
    elif args.acao == "rodada":  # mais Shorts do mesmo trecho: as sugestões que ainda não viraram Short
        projeto = cortes.nova_rodada(_projeto(args.projeto), args.modo_agente, args.sem_llm,
                                     Path(args.arquivo) if args.arquivo else None, not args.sem_painel, ate=args.ate)
        if projeto is None:
            print("\nRODADA=nenhuma (todas as sugestões deste trecho já viraram Shorts)")
            return 0
    elif args.acao == "continuar":
        projeto = cortes.continuar(_projeto(args.projeto), args.a_partir_de, args.modo_agente, args.sem_llm,
                                   not args.sem_painel, ate=args.ate)
    else:
        projeto = cortes.monitorar(args.canal, args.sem_llm)
        if projeto is None:
            return 0
    print(f"\nPROJETO={projeto.id}\nESTADO={projeto.estado}\nPASTA={projeto.pasta}")
    if projeto.estado == "aguardando_roteiro":
        print(f"PEDIDO_CORTES={projeto.p('analise', 'pedido_cortes.md')}")
        return SAIDA_AGUARDANDO_AGENTE
    return 0


def construir_parser() -> argparse.ArgumentParser:
    from esteira.projeto import ETAPAS_PRODUCAO

    p = argparse.ArgumentParser(prog="esteira.py", description="Esteira de produção automática para canais dark do YouTube.")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("diagnostico", help="verifica dependências, modelos e credenciais")
    s.add_argument("--rapido", action="store_true", help="não testa o navegador headless")
    s.set_defaults(func=cmd_diagnostico)

    s = sub.add_parser("canal", help="gerencia canais (listar | mostrar | criar)")
    s.add_argument("acao", choices=["listar", "mostrar", "criar"])
    s.add_argument("--canal")
    s.add_argument("--id")
    s.add_argument("--nome")
    s.add_argument("--nicho", default="")
    s.add_argument("--tom", default="envolvente")
    s.add_argument("--paleta", default="sombria_vermelha", help="nome, cores hex separadas por vírgula ou descrição")
    s.add_argument("--ritmo", default="medio", help="lento | medio | rapido")
    s.add_argument("--estilo", choices=["explicativo", "documentario", "animacao_2d", "tipografia"])
    s.add_argument("--handle")
    s.add_argument("--publico")
    s.add_argument("--duracao", type=int, default=8, help="duração alvo em minutos")
    s.add_argument("--tipo", default="dark", choices=["dark", "cortes"], help="dark = vídeos do zero; cortes = Shorts de lives")
    s.set_defaults(func=cmd_canal)

    s = sub.add_parser("fila", help="fila de temas do canal (listar | adicionar | remover)")
    s.add_argument("acao", choices=["listar", "adicionar", "remover"])
    s.add_argument("--canal", required=True)
    s.add_argument("--tema", action="append", default=[])
    s.add_argument("--inicio", action="store_true", help="adiciona no começo da fila")
    s.set_defaults(func=cmd_fila)

    s = sub.add_parser("verificar-tema", help="checa se o tema já foi produzido no canal")
    s.add_argument("--canal", required=True)
    s.add_argument("--tema", required=True)
    s.add_argument("--sem-llm", action="store_true")
    s.set_defaults(func=cmd_verificar_tema)

    s = sub.add_parser("produzir", help="produz um vídeo completo a partir de um tema ou roteiro")
    s.add_argument("--canal", required=True)
    s.add_argument("--tema")
    s.add_argument("--roteiro-arquivo")
    s.add_argument("--roteiro-texto")
    s.add_argument("--forcar", action="store_true", help="ignora o bloqueio de duplicidade")
    s.add_argument("--modo-agente", action="store_true", help="o agente de IA escreve o roteiro (sem API de LLM)")
    s.add_argument("--sem-llm", action="store_true", help="usa o gerador heurístico de roteiro")
    s.add_argument("--sem-painel", action="store_true", help="não abre o painel no final")
    s.add_argument("--ate", default="thumbnails", choices=ETAPAS_PRODUCAO)
    s.set_defaults(func=cmd_produzir)

    s = sub.add_parser("importar-roteiro", help="importa o roteiro JSON escrito pelo agente e continua")
    s.add_argument("--projeto", required=True)
    s.add_argument("--arquivo", required=True)
    s.add_argument("--sem-continuar", action="store_true")
    s.add_argument("--sem-painel", action="store_true")
    s.set_defaults(func=cmd_importar_roteiro)

    s = sub.add_parser("continuar", help="reexecuta a partir de uma etapa (só o que mudou é refeito)")
    s.add_argument("--projeto", required=True)
    s.add_argument("--a-partir-de", default="narracao", choices=ETAPAS_PRODUCAO)
    s.add_argument("--ate", default="thumbnails", choices=ETAPAS_PRODUCAO)
    s.add_argument("--blocos", help="força refazer estes blocos, ex.: 4 ou 3,5-7")
    s.add_argument("--forcar", action="store_true", help="refaz todos os blocos, ignorando o cache")
    s.add_argument("--sem-painel", action="store_true")
    s.add_argument("--sem-llm", action="store_true")
    s.set_defaults(func=cmd_continuar)

    s = sub.add_parser("editar-bloco", help="altera um bloco do roteiro (texto, visual, mídia)")
    s.add_argument("--projeto", required=True)
    s.add_argument("--bloco", type=int, required=True)
    s.add_argument("--narracao")
    s.add_argument("--fonetico", help="grafia fonética usada só na voz")
    s.add_argument("--modo", choices=["cartao_animado", "video_banco", "imagem_ia", "ilustracao_2d"])
    s.add_argument("--tipo-cartao", choices=["titulo", "contador", "lista", "grafico", "citacao"])
    s.add_argument("--dados-cartao", help='JSON, ex.: \'{"numero": "73", "unidade": "%"}\'')
    s.add_argument("--busca")
    s.add_argument("--destaque")
    s.add_argument("--trocar-midia", action="store_true", help="usa o próximo resultado do banco de mídia")
    s.add_argument("--arquivo-midia", help="usa um arquivo local (imagem ou vídeo)")
    s.add_argument("--short", choices=["sim", "nao"])
    s.set_defaults(func=cmd_editar_bloco)

    s = sub.add_parser("projetos", help="lista projetos")
    s.add_argument("--canal")
    s.add_argument("--estado", help="filtra por estado(s), ex.: pendente_revisao,reprovado")
    s.set_defaults(func=cmd_projetos)

    s = sub.add_parser("status", help="mostra o estado detalhado de um projeto")
    s.add_argument("--projeto", default="ultimo")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("pendencias", help="lista reprovações e erros com a sugestão de correção")
    s.add_argument("--canal")
    s.set_defaults(func=cmd_pendencias)

    s = sub.add_parser("painel", help="inicia o painel web de revisão")
    s.add_argument("--porta", type=int)
    s.add_argument("--sem-navegador", action="store_true")
    s.set_defaults(func=cmd_painel)

    s = sub.add_parser("app", help="app de PC: abrir (padrão) | instalar (atalho no menu) | remover")
    s.add_argument("acao", nargs="?", default="abrir", choices=["abrir", "instalar", "remover"])
    s.set_defaults(func=cmd_app)

    s = sub.add_parser("abrir-painel", help="abre o painel no navegador (inicia se preciso)")
    s.add_argument("--projeto")
    s.set_defaults(func=cmd_abrir_painel)

    for nome, func, ajuda in (("aprovar", cmd_aprovar, "aprova e agenda no YouTube"),
                              ("descartar", cmd_descartar, "descarta o projeto")):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("--projeto", required=True)
        if nome == "aprovar":
            s.add_argument("--sem-publicar", action="store_true")
        s.set_defaults(func=func)

    s = sub.add_parser("reprovar", help="reprova com o motivo do ajuste")
    s.add_argument("--projeto", required=True)
    s.add_argument("--motivo", required=True)
    s.set_defaults(func=cmd_reprovar)

    s = sub.add_parser("publicar", help="envia um projeto aprovado ao YouTube")
    s.add_argument("--projeto", required=True)
    s.add_argument("--simular", action="store_true", help="só calcula os horários, sem enviar")
    s.add_argument("--forcar", action="store_true")
    s.set_defaults(func=cmd_publicar)

    s = sub.add_parser("cancelar-agendamento",
                       help="tira do agendamento os vídeos já enviados de um projeto (ficam privados; nada é apagado)")
    s.add_argument("--projeto", required=True)
    s.set_defaults(func=cmd_cancelar_agendamento)

    s = sub.add_parser("trilhas", help="músicas do canal (listar | gerar trilhas synthwave originais)")
    s.add_argument("acao", choices=["listar", "gerar"])
    s.add_argument("--canal", required=True)
    s.set_defaults(func=cmd_trilhas)

    s = sub.add_parser("youtube", help="conexão com o YouTube (autenticar | agenda | perfil | restricoes)")
    s.add_argument("acao", choices=["autenticar", "agenda", "perfil", "restricoes", "reagendar"],
                   help="restricoes: confere os Shorts enviados e tira da agenda (privado) os que tiverem restrição")
    s.add_argument("--canal", required=True)
    s.add_argument("--sem-navegador", action="store_true")
    s.set_defaults(func=cmd_youtube)

    s = sub.add_parser("worker", help="consome o próximo tema da fila (usado pelo agendador)")
    s.add_argument("--canal", required=True)
    s.add_argument("--quantidade", type=int)
    s.set_defaults(func=cmd_worker)

    s = sub.add_parser("automacao", help="ativa/pausa a execução diária do worker")
    s.add_argument("acao", choices=["ativar", "pausar", "status"])
    s.add_argument("--canal")
    s.add_argument("--horario", help="HH:MM")
    s.set_defaults(func=cmd_automacao)

    s = sub.add_parser("fontes", help="canais de lives autorizados para cortes (listar | adicionar | remover)")
    s.add_argument("acao", choices=["listar", "adicionar", "remover"])
    s.add_argument("--canal", required=True)
    s.add_argument("--url", help="link do canal na Twitch (twitch.tv/nome), na Kick (kick.com/nome) ou no YouTube (@nome)")
    s.add_argument("--tipo", default="autorizado", choices=["proprio", "autorizado"])
    s.add_argument("--nome")
    s.add_argument("--credito", help="texto de crédito na tela e na descrição")
    s.add_argument("--autorizacao", help="como a autorização foi dada (obrigatório para canais de terceiros)")
    s.add_argument("--sem-monitorar", action="store_true", help="não procurar lives novas automaticamente")
    s.set_defaults(func=cmd_fontes)

    s = sub.add_parser("cortes", help="Shorts a partir de lives (processar | importar | continuar | rodada | monitorar | "
                                      "reeditar)")
    s.add_argument("acao", choices=["processar", "importar", "continuar", "rodada", "monitorar", "reeditar"],
                   help="rodada: mais Shorts do mesmo trecho com as sugestões que sobraram (--projeto; --arquivo opcional)")
    s.add_argument("--canal")
    s.add_argument("--url", help="link do vídeo da live (twitch.tv/videos/…, kick.com/<canal>/videos/… ou YouTube)")
    s.add_argument("--trecho", help="usa só este pedaço da live, ex.: 00:30:00-01:10:00 (Twitch/Kick baixam só ele; "
                                    "no YouTube o download é da live inteira)")
    s.add_argument("--autorizacao", help="processar: cadastra o canal da live como fonte autorizada com este texto "
                                         "(como o criador permite cortes), se ainda não for uma fonte")
    s.add_argument("--projeto")
    s.add_argument("--arquivo")
    s.add_argument("--a-partir-de", default="analise", choices=["analise", "edicao"])
    s.add_argument("--modo-agente", action="store_true", help="o agente de IA escolhe os momentos (sem API de LLM)")
    s.add_argument("--sem-llm", action="store_true", help="escolhe os momentos só pela pontuação")
    s.add_argument("--sem-painel", action="store_true")
    s.add_argument("--forcar", action="store_true", help="processa de novo uma live já processada")
    s.add_argument("--indice", type=int, help="reeditar: número do Short")
    s.add_argument("--gancho", help="reeditar: novo texto do gancho (use *palavra* para destacar)")
    s.add_argument("--inicio", type=float, help="reeditar: novo início do corte, em segundos da live")
    s.add_argument("--fim", type=float, help="reeditar: novo fim do corte, em segundos da live")
    s.add_argument("--trilha", help="reeditar: nome do arquivo da trilha ou 'nenhuma'")
    s.add_argument("--modelo", choices=["padrao", "direita", "centro"], help="reeditar: modelo visual do Short")
    s.add_argument("--layout", choices=["auto", "dividido", "moldura", "vertical"],
                   help="reeditar: auto (acha a câmera) | dividido (câmera em cima, jogo embaixo) | moldura | vertical")
    s.add_argument("--ate", choices=["ingestao", "analise"],
                   help="para depois desta etapa (para conferir e editar à mão antes de seguir)")
    s.add_argument("--opcoes", help='opções desta live em JSON, ex.: \'{"max_cortes_por_live": 3, "modelo": "centro"}\'')
    s.set_defaults(func=cmd_cortes)

    s = sub.add_parser("modelos", help="baixa os modelos locais (Kokoro e Whisper)")
    s.add_argument("acao", choices=["baixar"])
    s.add_argument("--kokoro", default="int8", choices=["int8", "completo"])
    s.add_argument("--whisper")
    s.add_argument("--sem-kokoro", action="store_true")
    s.add_argument("--sem-whisper", action="store_true")
    s.set_defaults(func=cmd_modelos)
    return p


def main(argv: list[str] | None = None) -> int:
    log.configurar()
    parser = construir_parser()
    args = parser.parse_args(argv)
    if args.comando == "canal" and args.acao == "criar" and not args.nome:
        parser.error("informe --nome para criar o canal")
    if args.comando == "canal" and args.acao == "mostrar" and not args.canal:
        parser.error("informe --canal")
    if args.comando == "automacao" and args.acao != "status" and not args.canal:
        parser.error("informe --canal")
    if args.comando == "produzir" and not (args.tema or args.roteiro_arquivo or args.roteiro_texto):
        parser.error("informe --tema, --roteiro-arquivo ou --roteiro-texto")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log.aviso("Interrompido pelo usuário.")
        return 130
    except Exception as exc:  # mensagens amigáveis para os erros esperados
        from esteira.modulos.m1_roteiro import TemaDuplicado

        from esteira.cortes.orquestrador import FonteNaoAutorizada, LiveEmAndamento, LiveRepetida

        if isinstance(exc, (FonteNaoAutorizada, LiveRepetida, LiveEmAndamento)):
            log.erro(str(exc))
            return SAIDA_DUPLICADO if isinstance(exc, LiveRepetida) else 5 if isinstance(exc, FonteNaoAutorizada) else 1
        if isinstance(exc, TemaDuplicado):
            log.erro(str(exc))
            print("  → Escolha outro tema ou use --forcar para produzir mesmo assim.")
            return SAIDA_DUPLICADO
        log.erro(f"{type(exc).__name__}: {exc}")
        if os.environ.get("ESTEIRA_DEBUG"):
            raise
        print("  (defina ESTEIRA_DEBUG=1 para ver o rastreamento completo)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
