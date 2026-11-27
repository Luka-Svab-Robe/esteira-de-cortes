"""Analista: encontra os momentos de interesse de uma live e escolhe os cortes.

Sinais por segundo (os disponíveis são combinados):
  - chat:      ritmo de mensagens do chat da live (YouTube) — o público reagindo;
  - repetidos: "mais repetidos" do YouTube (heatmap), quando o vídeo já tem audiência;
  - energia:   picos de volume (risadas, gritos, empolgação);
  - fala:      expressões de reação, perguntas e exclamações na transcrição.
Janelas candidatas começam e terminam em fronteiras de frase. A escolha final (com gancho, título e
descrição) é feita pelo LLM; sem LLM, pelo agente de IA (modo agente) ou por heurística.
"""
from __future__ import annotations

import html
import json
import math
import re
import subprocess
from pathlib import Path

from .. import ffmpeg, log, util
from ..config import PASTA_MODELOS, env

REACOES = ("kkk", "haha", "hahaha", "risos", "laughter", "meu deus", "caramba", "nossa", "não acredito",
           "nao acredito", "olha isso", "socorro", "mano", "gente", "sério", "serio", "absurdo", "inacreditável",
           "oh my god", "no way", "wow", "what", "crazy", "insane", "incredible")

ESQUEMA_CORTES = {
    "type": "object",
    "properties": {"cortes": {"type": "array", "items": {
        "type": "object",
        "properties": {"candidato": {"type": "integer"}, "inicio": {"type": "number"}, "fim": {"type": "number"},
                       "gancho": {"type": "string"}, "titulo": {"type": "string"}, "descricao": {"type": "string"},
                       "tags": {"type": "array", "items": {"type": "string"}}, "motivo": {"type": "string"}},
        "required": ["candidato", "inicio", "fim", "gancho", "titulo", "descricao", "tags", "motivo"],
        "additionalProperties": False}}},
    "required": ["cortes"], "additionalProperties": False,
}


# ------------------------------------------------------------------ leitura dos sinais
def ler_chat(caminho: Path | None) -> list[float]:
    if not caminho or not Path(caminho).exists():
        return []
    tempos = []
    with open(caminho, encoding="utf-8", errors="replace") as f:
        for linha in f:
            try:
                acao = json.loads(linha).get("replayChatItemAction", {})
                tempos.append(int(acao.get("videoOffsetTimeMsec", 0)) / 1000.0)
            except (ValueError, AttributeError):
                continue
    return sorted(t for t in tempos if t > 0)


def _tempo_vtt(texto: str) -> float:
    partes = texto.strip().replace(",", ".").split(":")
    partes = [float(p) for p in partes]
    while len(partes) < 3:
        partes.insert(0, 0.0)
    return partes[0] * 3600 + partes[1] * 60 + partes[2]


def ler_vtt(caminho: Path | None) -> list[dict]:
    """Legenda VTT -> segmentos. Trata o formato 'rolante' das legendas automáticas do YouTube."""
    if not caminho or not Path(caminho).exists():
        return []
    blocos = Path(caminho).read_text(encoding="utf-8", errors="replace").split("\n\n")
    segmentos: list[dict] = []
    for bloco in blocos:
        linhas = [l for l in bloco.strip().splitlines() if l.strip()]
        tempo = next((l for l in linhas if "-->" in l), None)
        if not tempo:
            continue
        ini, fim = tempo.split("-->")
        texto_linhas = [html.unescape(re.sub(r"<[^>]+>", "", l)).strip() for l in linhas[linhas.index(tempo) + 1:]]
        # ">>" e "- " marcam troca de quem fala nas legendas automáticas; não fazem parte da fala
        texto_linhas = [re.sub(r"\s*(>>|&gt;&gt;)\s*", " ", l).strip(" -") for l in texto_linhas]
        # "[ __ ]" é o palavrão censurado pelo YouTube nas legendas automáticas
        texto_linhas = [re.sub(r"\s{2,}", " ", re.sub(r"\[\s*_+\s*\]", "", l)).strip(" -,") for l in texto_linhas]
        texto_linhas = [l for l in texto_linhas if l]
        if not texto_linhas:
            continue
        texto = texto_linhas[-1]  # nas automáticas, a linha de cima repete o trecho anterior
        if segmentos and texto == segmentos[-1]["texto"]:
            segmentos[-1]["fim"] = _tempo_vtt(fim.split()[0])
            continue
        segmentos.append({"inicio": _tempo_vtt(ini), "fim": _tempo_vtt(fim.split()[0]), "texto": texto})
    return [s for s in segmentos if s["fim"] - s["inicio"] > 0.05]


def _janela_ffmpeg(inicio: float, fim: float | None) -> list[str]:
    """Argumentos de entrada do FFmpeg para ler só [inicio, fim] (busca precisa, pois o áudio é decodificado)."""
    args = ["-ss", f"{inicio:.3f}"] if inicio > 0 else []
    return args + (["-t", f"{fim - inicio:.3f}"] if fim else [])


def transcrever(video: Path, idioma: str | None, inicio: float = 0.0, fim: float | None = None) -> list[dict]:
    """Transcrição rápida (modelo leve) da janela [inicio, fim] do vídeo, em segundos relativos ao início da janela.

    Os cortes recebem uma transcrição precisa depois, no editor.
    """
    import numpy as np
    from faster_whisper import WhisperModel

    modelo = WhisperModel(env("WHISPER_MODELO_ANALISE", "base"), device="cpu", compute_type="int8",
                          download_root=str(PASTA_MODELOS / "whisper"))
    log.info("Transcrevendo a live para a análise (pode levar um tempo em lives longas)…")
    bruto = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *_janela_ffmpeg(inicio, fim),
                            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                           stdout=subprocess.PIPE, check=True).stdout
    audio = np.frombuffer(bruto, dtype="<i2").astype(np.float32) / 32768.0
    segmentos, _ = modelo.transcribe(audio, language=idioma or None, vad_filter=True, beam_size=1,
                                     condition_on_previous_text=False)
    return [{"inicio": float(s.start), "fim": float(s.end), "texto": s.text.strip()} for s in segmentos if s.text.strip()]


def energia_por_segundo(video: Path, inicio: float = 0.0, fim: float | None = None) -> list[float]:
    """Volume (dB RMS) a cada segundo da janela, lendo o áudio em fluxo para não estourar a memória."""
    import numpy as np

    taxa = 4000
    proc = subprocess.Popen([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *_janela_ffmpeg(inicio, fim),
                             "-i", str(video), "-vn", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"],
                            stdout=subprocess.PIPE)
    valores = []
    while True:
        bloco = proc.stdout.read(taxa * 2)
        if not bloco:
            break
        amostras = np.frombuffer(bloco, dtype="<i2").astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(amostras ** 2))) if amostras.size else 0.0
        valores.append(20 * math.log10(max(rms, 1e-5)))
    proc.wait()
    return valores


# ------------------------------------------------------------------ combinação
def _suavizar(valores: list[float], janela: int) -> list[float]:
    if not valores:
        return []
    meia = janela // 2
    acumulado = [0.0]
    for v in valores:
        acumulado.append(acumulado[-1] + v)
    saida = []
    for i in range(len(valores)):
        a, b = max(0, i - meia), min(len(valores), i + meia + 1)
        saida.append((acumulado[b] - acumulado[a]) / (b - a))
    return saida


def _zscore(valores: list[float]) -> list[float]:
    if not valores:
        return []
    media = sum(valores) / len(valores)
    desvio = math.sqrt(sum((v - media) ** 2 for v in valores) / len(valores)) or 1.0
    return [max(-2.0, min(4.0, (v - media) / desvio)) for v in valores]


def montar_sinais(duracao: int, chat: list[float], heatmap: list[dict], energia: list[float]) -> dict:
    sinais: dict[str, list[float]] = {}
    if len(chat) >= 30:
        contagem = [0.0] * duracao
        for t in chat:
            if 0 <= int(t) < duracao:
                contagem[int(t)] += 1
        sinais["chat"] = _zscore(_suavizar(contagem, 15))
    if heatmap:
        valores = [0.0] * duracao
        for h in heatmap:
            # B02: o trecho pode começar depois de intervalos que já estão antes da janela (índices negativos
            # escreveriam no fim da lista). Recorta nas duas pontas e ignora o que não cai dentro da duração.
            ini = max(0, math.floor(float(h.get("start_time", 0))))
            fim = min(duracao, math.ceil(float(h.get("end_time", 0))))
            for s in range(ini, fim):
                valores[s] = float(h.get("value", 0))
        sinais["repetidos"] = _zscore(valores)
    if energia:
        sinais["energia"] = _zscore(_suavizar((energia + [energia[-1]] * duracao)[:duracao], 5))
    pesos = {"chat": 0.45, "repetidos": 0.35, "energia": 0.2}
    soma = sum(pesos[k] for k in sinais) or 1.0
    combinado = [sum(pesos[k] * sinais[k][i] for k in sinais) / soma for i in range(duracao)] if sinais else [0.0] * duracao
    return {"combinado": combinado, "disponiveis": sorted(sinais)}


def _reacoes(texto: str) -> int:
    baixo = texto.lower()
    return sum(baixo.count(r) for r in REACOES) + texto.count("!") + texto.count("?") // 2


FIM_DE_FRASE = re.compile(r"[.!?…][\"'”’)\]]*$")


def frases(segmentos: list[dict], limite: float = 15.0) -> list[dict]:
    """Reparte a transcrição em frases completas (o tempo de cada frase é estimado pela posição dos caracteres).

    Legendas automáticas cortam a fala em pedaços de 2–3 s no meio das frases; começar um corte assim soa
    errado. Se a transcrição não tiver pontuação (frases longas demais), devolve os segmentos originais.
    """
    saida: list[dict] = []
    texto_atual: list[str] = []
    inicio_atual = None
    for seg in segmentos:
        texto = seg["texto"].strip()
        if not texto:
            continue
        duracao, total, pos = seg["fim"] - seg["inicio"], len(texto), 0
        for parte in re.split(r"(?<=[.!?…])\s+", texto):
            ini = seg["inicio"] + duracao * pos / total
            pos = min(total, pos + len(parte) + 1)
            if inicio_atual is None:
                inicio_atual = ini
            texto_atual.append(parte)
            if FIM_DE_FRASE.search(parte):
                saida.append({"inicio": round(inicio_atual, 2), "fim": round(seg["inicio"] + duracao * pos / total, 2),
                              "texto": " ".join(texto_atual)})
                texto_atual, inicio_atual = [], None
    if texto_atual and segmentos:
        saida.append({"inicio": round(inicio_atual, 2), "fim": segmentos[-1]["fim"], "texto": " ".join(texto_atual)})
    duracoes = sorted(f["fim"] - f["inicio"] for f in saida)
    if len(saida) < 3 or duracoes[len(duracoes) // 2] > limite:
        return segmentos
    return saida


def candidatos(segmentos: list[dict], combinado: list[float], dmin: float, dmax: float, quantidade: int) -> list[dict]:
    """Janelas de frases inteiras entre dmin e dmax segundos, ordenadas pela pontuação (sem sobreposição)."""
    segmentos = frases(segmentos)
    if not segmentos:  # sem transcrição: janelas fixas
        segmentos = [{"inicio": float(t), "fim": float(min(t + 10, len(combinado))), "texto": ""}
                     for t in range(0, max(1, len(combinado) - 10), 10)]
    janelas = []
    for i, seg in enumerate(segmentos):
        j, textos = i, []
        while j < len(segmentos) and segmentos[j]["fim"] - seg["inicio"] <= dmax:
            textos.append(segmentos[j]["texto"])
            duracao = segmentos[j]["fim"] - seg["inicio"]
            if duracao >= dmin:
                ini, fim = int(seg["inicio"]), int(min(len(combinado), segmentos[j]["fim"]))
                trecho = combinado[ini:fim] or [0.0]
                texto = " ".join(textos)
                pontos = (sum(trecho) / len(trecho) + 0.6 * max(trecho)
                          + 0.25 * min(_reacoes(texto), 6) / max(1, len(textos))
                          + (0.15 if re.search(r"[.!?…]\s*$", texto) else 0))
                janelas.append({"inicio": round(seg["inicio"], 2), "fim": round(segmentos[j]["fim"], 2),
                                "pontuacao": round(pontos, 3), "texto": texto})
            j += 1
    janelas.sort(key=lambda w: w["pontuacao"], reverse=True)
    escolhidas: list[dict] = []
    for janela in janelas:
        if all(janela["fim"] + 5 < e["inicio"] or janela["inicio"] > e["fim"] + 5 for e in escolhidas):
            escolhidas.append(janela)
        if len(escolhidas) >= quantidade:
            break
    for n, janela in enumerate(escolhidas, 1):
        janela["id"] = n
    return escolhidas


# ------------------------------------------------------------------ escolha final
def instrucoes(canal, fonte_nome: str, maximo: int, dmin: float, dmax: float) -> str:
    return (f"Você é o analista de cortes do canal \"{canal.nome}\" (nicho: {canal.nicho}). Recebe trechos candidatos "
            f"de uma live de {fonte_nome}, com a transcrição e uma pontuação de reação do público. Escolha até {maximo} "
            f"cortes para Shorts verticais: momentos que funcionam sozinhos (começo claro, clímax, fim), com gancho nos "
            f"primeiros 3 segundos. Ajuste 'inicio' e 'fim' (em segundos, dentro de ±8 s do candidato) para começar e "
            f"terminar em frases completas; duração entre {dmin:.0f} e {dmax:.0f} segundos. Não escolha trechos "
            f"repetitivos, ofensivos ou que precisem de contexto. Escreva em português do Brasil: 'gancho' (até 6 "
            f"palavras, aparece na tela), 'titulo' (até 80 caracteres, curioso e honesto, sem clickbait enganoso), "
            f"'descricao' (2 frases sobre o momento) e 5 a 10 'tags'. Não invente o que não está na transcrição.")


def mensagem(candidatos_: list[dict]) -> str:
    linhas = [f"[{c['id']}] {util.formatar_tempo(c['inicio'])}–{util.formatar_tempo(c['fim'])} "
              f"({c['fim'] - c['inicio']:.0f}s, pontuação {c['pontuacao']}): {c['texto']}" for c in candidatos_]
    return "Candidatos:\n" + "\n".join(linhas)


REPETIDO_FRACAO = 0.5  # dois cortes que dividem mais da metade do tempo do menor são o mesmo momento


def repete(a: dict, b: dict) -> bool:
    """Os dois cortes mostram o mesmo momento (dividem mais da metade da duração do menor)?"""
    comum = min(float(a["fim"]), float(b["fim"])) - max(float(a["inicio"]), float(b["inicio"]))
    menor = min(float(a["fim"]) - float(a["inicio"]), float(b["fim"]) - float(b["inicio"]))
    return menor > 0 and comum > REPETIDO_FRACAO * menor


def sem_repetidos(cortes: list[dict]) -> tuple[list[dict], list[dict]]:
    """(cortes que ficam, cortes repetidos tirados), mantendo a ordem: de cada repetição fica o primeiro."""
    ficam: list[dict] = []
    tirados: list[dict] = []
    for corte in cortes:
        (tirados if any(repete(corte, outro) for outro in ficam) else ficam).append(corte)
    return ficam, tirados


def validar(cortes: list[dict], candidatos_: list[dict], dmin: float, dmax: float, maximo: int) -> list[dict]:
    por_id = {c["id"]: c for c in candidatos_}
    finais = []
    for corte in cortes:
        base = por_id.get(int(corte.get("candidato", 0) or 0))
        if not base:
            continue
        ini = float(corte.get("inicio", base["inicio"]))
        fim = float(corte.get("fim", base["fim"]))
        if abs(ini - base["inicio"]) > 10 or abs(fim - base["fim"]) > 10 or not (dmin - 3 <= fim - ini <= dmax + 2):
            ini, fim = base["inicio"], base["fim"]
        if any(repete({"inicio": ini, "fim": fim}, f) for f in finais):  # mesmo momento duas vezes: não gasta vaga
            continue
        finais.append({"inicio": round(ini, 2), "fim": round(fim, 2), "gancho": str(corte.get("gancho", "")).strip()[:60],
                       "titulo": str(corte.get("titulo", "")).strip()[:90],
                       "descricao": str(corte.get("descricao", "")).strip(),
                       "tags": [str(t).strip() for t in corte.get("tags", []) if str(t).strip()][:12],
                       "motivo": str(corte.get("motivo", "")).strip(), "pontuacao": base["pontuacao"],
                       "texto": base["texto"], "automatico": bool(corte.get("automatico"))})
        if len(finais) >= maximo:
            break
    finais.sort(key=lambda c: c["inicio"])
    for n, corte in enumerate(finais, 1):
        corte["indice"] = n
    return finais


# Palavras de conversa (português falado e inglês comum, sem acentos) que não descrevem o momento: ficam fora das
# tags automáticas (sem modelo de linguagem as tags saem da transcrição).
FALA_COMUM = frozenset("""
cara mano mana gente pessoal galera olha olhe vamos vamo bora temos tenho tava to ta ai ah eh oh ne tipo sim nao
entao agora aqui ali isso vai vou foi fala falar faco precisa pode posso quer quero acho sei saber ver vem viu bom
boa beleza nossa deus caraca kkk kkkk haha hahaha ue opa oi ok okay legal velho mesmo cade ganho
this that what just man you your are not for get got like yeah yes no uh um hey go going gonna we they he she him
her it its me my be do does did all can will one there here now good god really know right with have has had were
been dude bro guys lol wow
""".split())
TAGS_AUTOMATICAS = 8


def _tags_automaticas(texto: str, fonte_nome: str = "") -> list[str]:
    """Tags sem modelo de linguagem: o criador da live, "cortes de live" e as palavras que descrevem o momento."""
    palavras = [p for p in util.palavras_chave(texto, 20) if p not in FALA_COMUM]
    base = [util.normalizar(fonte_nome)] if (fonte_nome or "").strip() else []
    return list(dict.fromkeys(base + ["cortes de live"] + palavras))[:TAGS_AUTOMATICAS]


def heuristica(candidatos_: list[dict], maximo: int, fonte_nome: str = "") -> list[dict]:
    cortes = []
    for c in candidatos_[:maximo]:
        frases = util.dividir_frases(c["texto"]) or [c["texto"]]
        primeira = frases[0]
        cortes.append({"candidato": c["id"], "inicio": c["inicio"], "fim": c["fim"],
                       "gancho": " ".join(primeira.split()[:6]).rstrip(",.;:"),
                       "titulo": util.limitar(primeira, 80, ""), "descricao": util.limitar(c["texto"], 220),
                       "tags": _tags_automaticas(c["texto"], fonte_nome),
                       "motivo": "maior reação do público (heurística)", "automatico": True})
    return cortes
