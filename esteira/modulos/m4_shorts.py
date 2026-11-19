"""Módulo 4a — Extração de Shorts verticais (1080x1920) com legenda palavra por palavra."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import ffmpeg, log, metadados, templates, util
from ..canal import Canal
from ..config import PASTA_FONTES
from ..navegador import Navegador
from ..projeto import Projeto

LARGURA, ALTURA = 1080, 1920


def selecionar_trechos(blocos: list[dict], linha: list[dict], maximo: int = 3, minimo: float = 30.0,
                       limite: float = 58.0) -> list[dict]:
    """Escolhe janelas de blocos inteiros (30–58 s) com maior densidade de trechos virais."""
    viral = [bool(b.get("potencial_short")) for b in blocos]
    duracoes = [item["duracao"] for item in linha]
    candidatos = []
    for i in range(len(blocos)):
        total, j = 0.0, i
        while j < len(blocos) and total + duracoes[j] <= limite:
            total += duracoes[j]
            j += 1
            if total >= minimo:
                janela = range(i, j)
                virais = sum(viral[k] for k in janela)
                pontuacao = virais / len(janela) + (0.3 if viral[i] else 0.0) + 0.1 * (total / limite)
                candidatos.append((pontuacao, i, j - 1, total, virais))
    candidatos.sort(key=lambda c: (-c[0], c[1]))
    escolhidos: list[dict] = []
    existe_viral = any(viral)
    for pontuacao, ini, fim, total, virais in candidatos:
        if existe_viral and virais == 0:
            continue
        if any(not (fim < e["ini"] or ini > e["fim"]) for e in escolhidos):
            continue
        escolhidos.append({"ini": ini, "fim": fim, "duracao": round(total, 3), "pontuacao": round(pontuacao, 3)})
        if len(escolhidos) >= maximo:
            break
    return sorted(escolhidos, key=lambda e: e["ini"])


def _cor_ass(hexadecimal: str) -> str:
    h = hexadecimal.lstrip("#")
    if len(h) != 6:
        return "&H0000D7FF&"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}&".upper()


def _limpar_palavra(palavra: str) -> str:
    palavra = re.sub(r"[{}\\]", "", palavra)
    palavra = re.sub(r"^[\"“”'(\[]+|[\"“”')\],;:]+$", "", palavra)
    return palavra.upper()


def gerar_ass(palavras: list[dict], fonte: str, cor_destaque: str, y: int, tamanho: int = 88,
              ancora: int = 5) -> str:
    """Legenda palavra por palavra. ancora = alinhamento ASS do ponto y (5 = centro; 8 = topo, cresce para baixo)."""
    prefixo = "" if ancora == 5 else f"\\an{ancora}"
    grupos, atual = [], []
    for p in palavras:
        atual.append(p)
        texto = " ".join(x["palavra"] for x in atual)
        if len(atual) >= 3 or len(texto) >= 16 or re.search(r"[.!?…,;:]$", p["palavra"]):
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)
    eventos = []
    for gi, grupo in enumerate(grupos):
        proximo_grupo = grupos[gi + 1][0]["inicio"] if gi + 1 < len(grupos) else None
        exibidas = [_limpar_palavra(p["palavra"]) for p in grupo]
        for k, p in enumerate(grupo):
            inicio = p["inicio"]
            if k + 1 < len(grupo):
                fim = grupo[k + 1]["inicio"]
            elif proximo_grupo is not None and proximo_grupo - p["fim"] < 0.5:
                fim = proximo_grupo
            else:
                fim = p["fim"] + 0.25
            fim = max(fim, inicio + 0.08)
            partes = []
            for m, texto in enumerate(exibidas):
                if not texto:
                    continue
                if m == k:
                    partes.append(f"{{\\c{cor_destaque}\\fscx112\\fscy112\\t(0,90,\\fscx118\\fscy118)"
                                  f"\\t(90,180,\\fscx112\\fscy112)}}{texto}{{\\r}}")
                else:
                    partes.append(texto)
            eventos.append(f"Dialogue: 0,{util.tempo_ass(inicio)},{util.tempo_ass(fim)},Legenda,,0,0,0,,"
                           f"{{{prefixo}\\pos({LARGURA // 2},{y})}}" + " ".join(partes))
    cabecalho = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {LARGURA}
PlayResY: {ALTURA}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Legenda,{fonte},{tamanho},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return cabecalho + "\n".join(eventos) + "\n"


def _titulo_short(blocos: list[dict]) -> str:
    for bloco in blocos:
        if bloco.get("potencial_short") and bloco.get("destaque"):
            return bloco["destaque"]
    return blocos[0].get("destaque") or util.limitar(blocos[0]["narracao"], 50)


def executar(projeto: Projeto, canal: Canal, forcar: bool = False) -> list[dict]:
    log.etapa("Módulo 4 — Shorts verticais 9:16")
    roteiro = projeto.roteiro()
    linha = projeto.linha_tempo()
    if not projeto.video_final.exists():
        raise FileNotFoundError("Vídeo final ausente: rode a montagem antes dos shorts.")
    cfg = canal.shorts
    trechos = selecionar_trechos(roteiro["blocos"], linha, int(cfg.get("max_shorts", 3)),
                                 float(cfg.get("duracao_min", 30)), float(cfg.get("duracao_max", 58)))
    pasta = projeto.p("shorts")
    for antigo in pasta.glob("short_*"):
        antigo.unlink(missing_ok=True)
    projeto.limpar_alertas("shorts")
    if not trechos:
        log.aviso("Nenhum trecho de 30 a 58 segundos disponível para Shorts.")
        projeto.alertar("shorts", "Vídeo curto demais ou sem trechos virais: nenhum Short gerado.")
        projeto.salvar("shorts/shorts.json", [])
        projeto.marcar_etapa("shorts", True, quantidade=0)
        return []
    palavras = projeto.ler("legendas/palavras.json", []) or []
    tema = templates.tema_canal(canal)
    fonte = tema["fonte_titulos"] if templates.fonte_disponivel(tema["fonte_titulos"]) else "Arial"
    cor = _cor_ass(canal.paleta.get("secundaria", "#ffd700"))
    quatro_tres = str(cfg.get("enquadramento", "4:3")) == "4:3"
    altura_fg = 810 if quatro_tres else 608
    y_fg = int(ALTURA * 0.42 - altura_fg / 2)
    y_legenda = int(ALTURA * float(cfg.get("legenda_altura", 0.66)))
    tem_libass = ffmpeg.tem_filtro("subtitles")
    if not tem_libass:
        projeto.alertar("shorts", "Este FFmpeg não tem libass: Shorts gerados sem legenda queimada.")
    fontes_rel = os.path.relpath(PASTA_FONTES, pasta).replace("\\", "/") if any(PASTA_FONTES.glob("*.ttf")) else ""
    hashtags = " ".join("#" + h for h in (projeto.ler("metadados.json", {}) or {}).get("hashtags", [])[:3])
    resultados = []
    with Navegador() as nav:
        for n, trecho in enumerate(trechos, 1):
            blocos = roteiro["blocos"][trecho["ini"]:trecho["fim"] + 1]
            inicio = linha[trecho["ini"]]["inicio"]
            duracao = trecho["duracao"]
            titulo = _titulo_short(blocos)
            log.info(f"Short {n}: blocos {blocos[0]['id']}–{blocos[-1]['id']} ({duracao:.1f}s) — {titulo}")
            html = templates.renderizar("cenas/short_titulo.html", pasta / f"short_{n}_titulo.html", tema,
                                        {"titulo": titulo, "handle": canal.handle or canal.nome}, LARGURA, ALTURA, True)
            titulo_png = nav.capturar(html, pasta / f"short_{n}_titulo.png", LARGURA, ALTURA, t=5, transparente=True)
            janela = [{"palavra": p["palavra"], "inicio": max(0.0, p["inicio"] - inicio), "fim": max(0.0, p["fim"] - inicio)}
                      for p in palavras if inicio <= p["inicio"] < inicio + duracao]
            util.salvar_texto(pasta / f"short_{n}.ass", gerar_ass(janela, fonte, cor, y_legenda))
            audio_bruto, audio_norm = pasta / f"short_{n}_audio.wav", pasta / f"short_{n}_audio_norm.wav"
            ffmpeg.rodar(["-ss", f"{inicio:.3f}", "-t", f"{duracao:.3f}", "-i", projeto.video_final, "-vn",
                          "-c:a", "pcm_s16le", "-ar", "48000", audio_bruto], descricao="Áudio do Short")
            ffmpeg.normalizar_audio(audio_bruto, audio_norm, -14.0, -1.5, 11.0)
            recorte = "crop=ih*4/3:ih," if quatro_tres else ""
            grafo = (f"[0:v]split=2[a][b];"
                     f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
                     f"scale={LARGURA}:{ALTURA},eq=brightness=-0.12:saturation=0.85,setsar=1[bg];"
                     f"[b]{recorte}scale={LARGURA}:-2,setsar=1[fg];"
                     f"[bg][fg]overlay=(W-w)/2:{y_fg}[c1];[c1][1:v]overlay=0:0[c2];")
            if tem_libass:
                opcao_fontes = f":fontsdir={fontes_rel}" if fontes_rel else ""
                grafo += f"[c2]subtitles=short_{n}.ass{opcao_fontes}[c3];"
            else:
                grafo += "[c2]null[c3];"
            grafo += "[c3]fade=t=in:st=0:d=0.3,format=yuv420p[v]"
            saida = pasta / f"short_{n}.mp4"
            ffmpeg.rodar(["-ss", f"{inicio:.3f}", "-t", f"{duracao:.3f}", "-i", str(projeto.video_final),
                          "-i", titulo_png.name, "-i", audio_norm.name, "-filter_complex", grafo, "-map", "[v]",
                          "-map", "2:a:0", "-t", f"{duracao:.3f}", "-r", "30", *ffmpeg.args_video("final"),
                          "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", saida.name],
                         cwd=pasta, descricao=f"Renderização do Short {n}")
            for temporario in (audio_bruto, audio_norm, titulo_png, html):
                Path(temporario).unlink(missing_ok=True)
            resultados.append({
                "indice": n, "arquivo": f"shorts/{saida.name}", "blocos": [b["id"] for b in blocos],
                "inicio": round(inicio, 3), "duracao": duracao, "titulo": util.limitar(f"{titulo} #shorts", 100, ""),
                "descricao": f"{util.limitar(blocos[0]['narracao'], 180)}\n\nAssista ao vídeo completo: {{link_video}}\n\n#shorts {hashtags}".strip(),
            })
    projeto.salvar("shorts/shorts.json", resultados)
    meta = projeto.ler("metadados.json", {}) or {}
    anteriores = {tuple(s.get("blocos", [])): s for s in meta.get("shorts", [])}
    meta["shorts"] = []
    for r in resultados:
        anterior = anteriores.get(tuple(r["blocos"]), {})
        meta["shorts"].append({"indice": r["indice"], "blocos": r["blocos"], "selecionado": anterior.get("selecionado", True),
                               "titulo": anterior.get("titulo", r["titulo"]), "descricao": anterior.get("descricao", r["descricao"])})
    projeto.salvar("metadados.json", meta)
    metadados.atualizar(projeto, canal)
    projeto.marcar_etapa("shorts", True, quantidade=len(resultados))
    log.ok(f"{len(resultados)} Short(s) gerado(s)")
    return resultados
