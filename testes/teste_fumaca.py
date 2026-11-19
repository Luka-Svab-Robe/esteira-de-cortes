#!/usr/bin/env python3
"""Teste de fumaça ponta a ponta da esteira.

Cria um canal temporário (_teste_fumaca), produz um vídeo curto a partir de um
roteiro pronto (sem LLM), confere todos os artefatos e testa o reprocessamento
parcial, a anti-duplicidade e o agendamento simulado. No fim, apaga o canal e
os projetos de teste (use --manter para inspecionar).

Uso:
  python testes/teste_fumaca.py                 # usa internet para pesquisa e mídia
  python testes/teste_fumaca.py --offline       # sem pesquisa web e sem bancos de mídia
  python testes/teste_fumaca.py --estilo tipografia --manter
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ROTEIRO = """O farol de Flannan, na Escócia, guarda um dos mistérios mais estranhos do século vinte.
Em dezembro de 1900, um navio passou pela ilha e percebeu que a luz do farol estava apagada.
Dias depois, uma equipe chegou ao local. O portão estava fechado e as camas estavam desfeitas.
Os três faroleiros tinham simplesmente desaparecido. Nenhum corpo foi encontrado.
Um detalhe chamou atenção: apenas dois dos três casacos de chuva tinham sumido.
Por que um dos homens sairia sem casaco no meio de uma tempestade?
A teoria mais aceita fala de uma onda gigante, de mais de trinta metros, que teria arrastado os três para o mar.
Mais de cem anos depois, ninguém sabe ao certo o que aconteceu em Flannan.
Se você gosta de mistérios reais, se inscreva no canal."""


def verificar(condicao: bool, mensagem: str) -> None:
    print(("  ✔ " if condicao else "  ✖ ") + mensagem, flush=True)
    if not condicao:
        raise AssertionError(mensagem)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--manter", action="store_true")
    parser.add_argument("--estilo", default="explicativo", choices=["explicativo", "documentario", "animacao_2d", "tipografia"])
    args = parser.parse_args()
    if args.offline:
        os.environ["PESQUISA_DESATIVADA"] = "1"
        os.environ["MIDIA_DESATIVADA"] = "1"
    os.environ["PAINEL_ABRIR_NAVEGADOR"] = "0"

    from esteira import ffmpeg, log, orquestrador
    from esteira.canal import Canal, criar_canal
    from esteira.config import PASTA_CANAIS, PASTA_PROJETOS
    from esteira.historico import Historico
    from esteira.modulos import m6_publicacao
    from esteira.modulos.m1_roteiro import TemaDuplicado

    log.configurar()
    canal_id = "_teste_fumaca"
    shutil.rmtree(PASTA_CANAIS / canal_id, ignore_errors=True)
    shutil.rmtree(PASTA_PROJETOS / canal_id, ignore_errors=True)
    inicio = time.time()
    try:
        print("\n=== 1. Criação de canal ===")
        canal = criar_canal(canal_id, "Teste de Fumaça", "mistérios reais e curiosidades", "sombrio e direto",
                            "vermelho e preto", "medio", estilo=args.estilo, duracao_min=1)
        verificar(canal.estilo == args.estilo, f"canal criado com estilo {args.estilo}")

        print("\n=== 2. Produção completa (roteiro pronto, sem LLM) ===")
        projeto = orquestrador.produzir(canal_id, "O mistério do farol de Flannan", ROTEIRO, sem_llm=True,
                                        abrir_painel=False)
        verificar(projeto.estado == "pendente_revisao", f"estado final = {projeto.estado}")
        roteiro = projeto.roteiro()
        linha = projeto.linha_tempo()
        verificar(len(roteiro["blocos"]) >= 6, f"{len(roteiro['blocos'])} blocos no roteiro")
        duracoes = [round(b["duracao"], 1) for b in linha]
        verificar(all(3.5 <= d <= 14.5 for d in duracoes[:-1]), f"blocos de ~5 a 12 segundos: {duracoes}")
        motores = {b["motor"] for b in projeto.ler("audio/auditoria.json", [])}
        verificar(len(motores) == 1, f"voz única no vídeo inteiro: {motores}")
        total = linha[-1]["fim"]
        info = ffmpeg.info_midia(projeto.video_final)
        verificar(abs(info["duracao"] - total) < 0.25, f"vídeo {info['duracao']:.2f}s = narração {total:.2f}s")
        verificar((info.get("largura"), info.get("altura")) == (1920, 1080), "vídeo 1920x1080")
        loud = ffmpeg.medir_loudness(projeto.video_final)
        verificar(-15.0 <= loud["input_i"] <= -13.0, f"loudness integrado {loud['input_i']} LUFS (alvo -14)")
        verificar(loud["input_tp"] <= -1.4, f"pico real {loud['input_tp']} dBTP (máx -1.5)")
        verificar(projeto.p("legendas", "video.srt").stat().st_size > 100, "legendas SRT geradas")
        shorts = projeto.ler("shorts/shorts.json", [])
        verificar(len(shorts) >= 1, f"{len(shorts)} Short(s) gerado(s)")
        for s in shorts:
            i = ffmpeg.info_midia(projeto.p(s["arquivo"]))
            verificar((i.get("largura"), i.get("altura")) == (1080, 1920) and 30 <= i["duracao"] <= 58.5,
                      f"Short {s['indice']}: 1080x1920, {i['duracao']:.1f}s")
        from PIL import Image

        for n in (1, 2, 3):
            with Image.open(projeto.p("thumbnails", f"thumb_{n}.jpg")) as im:
                verificar(im.size == (1280, 720), f"thumbnail {n} 1280x720")
        meta = projeto.ler("metadados.json")
        verificar(bool(meta.get("titulo")) and "descricao_final" in meta, "metadados de SEO gerados")
        registros = Historico(canal.caminho_db).listar()
        verificar(any(r["projeto"] == projeto.id for r in registros), "tema gravado no histórico SQLite")

        print("\n=== 3. Anti-duplicidade ===")
        try:
            orquestrador.produzir(canal_id, "Farol de Flannan: o mistério", ROTEIRO, sem_llm=True, abrir_painel=False)
            verificar(False, "tema repetido deveria ser bloqueado")
        except TemaDuplicado as exc:
            verificar(True, f"tema repetido bloqueado ({exc.resultado.motivo})")

        print("\n=== 4. Reprovação e reprocessamento parcial ===")
        orquestrador.reprovar(projeto, "Trocar o destaque da cena 2 e encurtar o texto da capa 1")
        verificar(projeto.estado == "reprovado", "projeto reprovado com motivo registrado")
        cache_antes = projeto.ler("cenas/cache.json")
        roteiro["blocos"][1]["destaque"] = "Três homens sumiram"
        projeto.salvar_roteiro(roteiro)
        t0 = time.time()
        orquestrador.continuar(projeto, "montagem", abrir_painel=False)
        cache_depois = projeto.ler("cenas/cache.json")
        mudaram = [k for k in cache_depois if cache_antes.get(k, {}).get("chave") != cache_depois[k]["chave"]]
        verificar(mudaram == ["2"], f"apenas a cena 2 foi re-renderizada (mudaram: {mudaram}) em {time.time() - t0:.0f}s")
        verificar(projeto.estado == "pendente_revisao" and not projeto.reprovacoes_pendentes(),
                  "projeto voltou para revisão com a reprovação resolvida")

        print("\n=== 5. Agendamento (simulação, sem enviar) ===")
        plano = m6_publicacao.publicar(projeto, Canal.carregar(canal_id), simular=True)
        verificar(bool(plano.get("video")), f"vídeo seria publicado em {plano['video']}")
        print(f"\nTESTE DE FUMAÇA APROVADO em {time.time() - inicio:.0f}s — projeto: {projeto.pasta}")
        return 0
    finally:
        if not args.manter:
            shutil.rmtree(PASTA_CANAIS / canal_id, ignore_errors=True)
            shutil.rmtree(PASTA_PROJETOS / canal_id, ignore_errors=True)


def _usar_venv() -> None:
    if os.environ.get("ESTEIRA_SEM_VENV"):
        return
    venv = RAIZ / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists() and Path(sys.prefix).resolve() != venv.resolve():
        os.environ["ESTEIRA_SEM_VENV"] = "1"
        if os.name == "nt":
            sys.exit(subprocess.call([str(python), *sys.argv]))
        os.execv(str(python), [str(python), *sys.argv])


if __name__ == "__main__":
    _usar_venv()
    sys.exit(main())
