"""Módulo 2 — Narração neural local e auto-auditoria de pronúncia.

Para cada bloco:
  1. sintetiza a voz (Kokoro ONNX local, CPU; Edge-TTS como alternativa online);
  2. limpa silêncios, normaliza o nível e adiciona as pausas do ritmo do canal;
  3. transcreve com Faster-Whisper (tempos por palavra) e compara com o roteiro;
  4. se o erro (WER) passar do limite, ajusta a escrita fonética e regera SÓ esse bloco.
"""
from __future__ import annotations

import asyncio
import difflib
import re
import shutil
import wave
from pathlib import Path

from .. import ffmpeg, log, metadados, util
from ..canal import Canal
from ..config import PASTA_MODELOS, caminho_env, env, env_float, env_int
from ..projeto import Projeto

VERSAO = 3
LETRAS = {"A": "á", "B": "bê", "C": "cê", "D": "dê", "E": "é", "F": "éfe", "G": "gê", "H": "agá", "I": "í",
          "J": "jota", "K": "cá", "L": "éle", "M": "ême", "N": "ene", "O": "ó", "P": "pê", "Q": "quê", "R": "érre",
          "S": "ésse", "T": "tê", "U": "u", "V": "vê", "W": "dáblio", "X": "xis", "Y": "ípsilon", "Z": "zê"}
VOGAIS = set("AEIOU")


class ErroNarracao(RuntimeError):
    pass


# --------------------------------------------------------------- motores
class MotorKokoro:
    nome = "kokoro"
    _instancia = None

    def __init__(self, voz: str, velocidade: float):
        self.voz = voz or "pm_alex"
        self.velocidade = velocidade

    @staticmethod
    def disponivel() -> bool:
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False
        return caminho_env("KOKORO_MODELO", "modelos/kokoro/kokoro-v1.0.int8.onnx").exists() and \
            caminho_env("KOKORO_VOZES", "modelos/kokoro/voices-v1.0.bin").exists()

    @classmethod
    def _modelo(cls):
        if cls._instancia is None:
            from kokoro_onnx import Kokoro

            cls._instancia = Kokoro(str(caminho_env("KOKORO_MODELO", "modelos/kokoro/kokoro-v1.0.int8.onnx")),
                                    str(caminho_env("KOKORO_VOZES", "modelos/kokoro/voices-v1.0.bin")))
        return cls._instancia

    def sintetizar(self, texto: str, destino: Path, fator_velocidade: float = 1.0) -> Path:
        import numpy as np

        velocidade = float(self.velocidade) * fator_velocidade
        amostras, taxa = self._modelo().create(texto, voice=self.voz, speed=velocidade, lang="pt-br")
        dados = (np.clip(np.asarray(amostras, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
        destino = destino.with_suffix(".wav")
        with wave.open(str(destino), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(taxa))
            w.writeframes(dados.tobytes())
        return destino


class MotorEdge:
    nome = "edge"

    def __init__(self, voz: str, velocidade: float):
        self.voz = voz or "pt-BR-AntonioNeural"
        self.velocidade = velocidade

    @staticmethod
    def disponivel() -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def sintetizar(self, texto: str, destino: Path, fator_velocidade: float = 1.0) -> Path:
        import edge_tts

        destino = destino.with_suffix(".mp3")
        taxa = f"{int(round((float(self.velocidade) * fator_velocidade - 1) * 100)):+d}%"

        async def falar() -> None:
            await edge_tts.Communicate(texto, self.voz, rate=taxa).save(str(destino))

        for tentativa in range(3):
            try:
                asyncio.run(falar())
                if destino.exists() and destino.stat().st_size > 1000:
                    return destino
            except Exception as exc:  # erros de rede do serviço online
                log.debug(f"Edge-TTS falhou (tentativa {tentativa + 1}): {exc}")
        raise ErroNarracao("Edge-TTS indisponível (verifique a conexão com a internet).")


def preparar_motores(canal: Canal) -> list:
    voz = canal.voz
    velocidade = float(voz.get("velocidade", 1.0) or 1.0)
    preferido = str(voz.get("motor", "auto")).lower()
    kokoro = MotorKokoro(voz.get("kokoro_voz"), velocidade) if MotorKokoro.disponivel() else None
    edge = MotorEdge(voz.get("edge_voz"), velocidade) if MotorEdge.disponivel() else None
    ordem = [edge, kokoro] if preferido == "edge" else [kokoro, edge]
    motores = [m for m in ordem if m]
    if preferido == "kokoro" and not kokoro:
        log.aviso("Modelo Kokoro não encontrado (rode: python esteira.py modelos baixar); usando Edge-TTS.")
    if not motores:
        raise ErroNarracao("Nenhum motor de voz disponível. Rode python instalar.py (Kokoro) ou instale edge-tts.")
    return motores


# --------------------------------------------------------------- auditoria
class Auditor:
    def __init__(self):
        self.modelo = None
        self.desativado_motivo = ""
        self.dispositivo = "cpu"

    def _carregar(self, forcar_cpu: bool = False):
        from faster_whisper import WhisperModel

        nome = env("WHISPER_MODELO", "small")
        dispositivo = "cpu" if forcar_cpu else env("WHISPER_DISPOSITIVO", "auto").lower()
        if dispositivo == "auto":
            try:
                import ctranslate2

                dispositivo = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                dispositivo = "cpu"
        tipo = "float16" if dispositivo == "cuda" else "int8"
        self.modelo = WhisperModel(nome, device=dispositivo, compute_type=tipo,
                                   download_root=str(PASTA_MODELOS / "whisper"))
        self.dispositivo = dispositivo
        log.info(f"Auditor de pronúncia: Faster-Whisper '{nome}' em {dispositivo.upper()}")

    def transcrever(self, wav: Path) -> list[dict] | None:
        if self.desativado_motivo:
            return None
        if self.modelo is None:
            try:
                self._carregar()
            except Exception as exc:
                try:
                    self._carregar(forcar_cpu=True)
                except Exception:
                    self.desativado_motivo = f"Faster-Whisper indisponível: {exc}"
                    log.aviso(self.desativado_motivo + " — auditoria de pronúncia desativada.")
                    return None
        try:
            return self._transcrever(wav)
        except Exception as exc:
            if self.dispositivo == "cuda":
                log.aviso(f"GPU falhou no Whisper ({exc}); refazendo na CPU.")
                self._carregar(forcar_cpu=True)
                return self._transcrever(wav)
            raise

    def _transcrever(self, wav: Path) -> list[dict]:
        segmentos, _ = self.modelo.transcribe(str(wav), language="pt", word_timestamps=True, beam_size=5,
                                              condition_on_previous_text=False, vad_filter=False)
        palavras = []
        for segmento in segmentos:
            for p in segmento.words or []:
                if p.word.strip():
                    palavras.append({"palavra": p.word.strip(), "inicio": float(p.start), "fim": float(p.end)})
        return palavras


# ------------------------------------------------------ texto e fonética
def tokens_wer(texto: str) -> list[str]:
    texto = util.expandir_numeros(texto.replace("&", " e "))
    return util.normalizar(texto).split()


def taxa_erro(referencia: list[str], hipotese: list[str]) -> float:
    if not referencia:
        return 0.0 if not hipotese else 1.0
    anterior = list(range(len(hipotese) + 1))
    for i, ref in enumerate(referencia, 1):
        atual = [i] + [0] * len(hipotese)
        for j, hip in enumerate(hipotese, 1):
            atual[j] = min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ref != hip))
        anterior = atual
    return anterior[-1] / len(referencia)


def aplicar_pronuncias(texto: str, dicionario: dict) -> str:
    for original, falado in sorted((dicionario or {}).items(), key=lambda kv: -len(kv[0])):
        texto = re.sub(rf"(?<!\w){re.escape(str(original))}(?!\w)", str(falado), texto)
    return texto


def _pronunciavel(sigla: str) -> bool:
    if len(sigla) < 4:
        return False
    vogais = sum(1 for c in sigla if c in VOGAIS)
    return vogais >= 2 and not re.search(r"[^AEIOU]{3}", sigla)


def expandir_siglas(texto: str, apenas: set[str] | None = None) -> str:
    def trocar(m: re.Match) -> str:
        sigla = m.group(0)
        if apenas is not None and util.normalizar(sigla) not in apenas:
            return sigla
        if _pronunciavel(sigla):
            return sigla.capitalize()
        return " ".join(LETRAS.get(c, c) for c in sigla)

    return re.sub(r"\b[A-Z]{2,6}\b", trocar, texto)


def limpar_simbolos(texto: str) -> str:
    trocas = {"&": " e ", "@": " arroba ", "+": " mais ", "=": " igual a ", "/": " barra ", "–": ", ", "—": ", ",
              "(": ", ", ")": ", ", "[": "", "]": "", "*": "", "#": "", "\"": "", "“": "", "”": ""}
    for simbolo, troca in trocas.items():
        texto = texto.replace(simbolo, troca)
    return re.sub(r"\s+,", ",", re.sub(r"\s+", " ", texto)).strip()


def palavras_problematicas(original: str, palavras_ouvidas: list[dict]) -> set[str]:
    ref = tokens_wer(original)
    hip = tokens_wer(" ".join(p["palavra"] for p in palavras_ouvidas))
    problemas = set()
    for op, i1, i2, _, _ in difflib.SequenceMatcher(None, ref, hip, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            problemas.update(ref[i1:i2])
    return problemas


def reescrever_com_llm(llm, texto: str, problemas: set[str]) -> str | None:
    esquema = {"type": "object", "properties": {"texto_fonetico": {"type": "string"}},
               "required": ["texto_fonetico"], "additionalProperties": False}
    try:
        resposta = llm.gerar_json(
            "Você prepara textos para um sintetizador de voz em português do Brasil. Reescreva o texto para que "
            "seja pronunciado corretamente: escreva números por extenso, siglas como devem ser lidas, nomes "
            "estrangeiros com grafia fonética aportuguesada. Mantenha o mesmo sentido e a mesma ordem de palavras; "
            "não acrescente nem remova informações.",
            f"Texto: {texto}\nPalavras que o sintetizador pronunciou errado: {', '.join(sorted(problemas)) or '(desconhecidas)'}",
            esquema, max_tokens=4000)
        novo = str(resposta.get("texto_fonetico", "")).strip()
        return novo or None
    except Exception as exc:
        log.debug(f"Reescrita fonética via LLM falhou: {exc}")
        return None


# ------------------------------------------------------------- alinhamento
def alinhar_palavras(original: str, ouvidas: list[dict] | None, duracao: float, inicio_fala: float) -> list[dict]:
    """Associa os tempos do Whisper às palavras ORIGINAIS do roteiro (grafia correta na legenda)."""
    exibicao = original.split()
    if not exibicao:
        return []
    mapa_ref: list[int] = []
    ref: list[str] = []
    for i, palavra in enumerate(exibicao):
        for tok in tokens_wer(palavra):
            ref.append(tok)
            mapa_ref.append(i)
    hip, tempos = [], []
    for p in ouvidas or []:
        toks = tokens_wer(p["palavra"]) or [util.normalizar(p["palavra"])]
        passo = (p["fim"] - p["inicio"]) / max(1, len(toks))
        for k, tok in enumerate(toks):
            hip.append(tok)
            tempos.append((p["inicio"] + k * passo, p["inicio"] + (k + 1) * passo))
    intervalos: dict[int, list[float]] = {}
    if hip:
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ref, hip, autojunk=False).get_opcodes():
            if op not in ("equal", "replace") or i2 == i1:
                continue
            for k in range(i1, i2):
                j = j1 + int((k - i1) * (j2 - j1) / (i2 - i1))
                j = min(max(j, j1), j2 - 1)
                ini, fim = tempos[j]
                faixa = intervalos.setdefault(mapa_ref[k], [ini, fim])
                faixa[0], faixa[1] = min(faixa[0], ini), max(faixa[1], fim)
    fim_fala = max((t[1] for t in tempos), default=duracao - 0.2)
    resultado = [{"palavra": w, "inicio": None, "fim": None} for w in exibicao]
    for i, (ini, fim) in intervalos.items():
        resultado[i]["inicio"], resultado[i]["fim"] = ini, fim
    # interpola palavras sem tempo (não reconhecidas) proporcionalmente ao tamanho
    i = 0
    while i < len(resultado):
        if resultado[i]["inicio"] is not None:
            i += 1
            continue
        j = i
        while j < len(resultado) and resultado[j]["inicio"] is None:
            j += 1
        ini = resultado[i - 1]["fim"] if i > 0 else inicio_fala
        fim = resultado[j]["inicio"] if j < len(resultado) else max(fim_fala, ini + 0.3 * (j - i))
        pesos = [max(1, len(resultado[k]["palavra"])) for k in range(i, j)]
        total, acumulado = sum(pesos), ini
        for k, peso in zip(range(i, j), pesos):
            passo = (fim - ini) * peso / total
            resultado[k]["inicio"], resultado[k]["fim"] = acumulado, acumulado + passo
            acumulado += passo
        i = j
    for p in resultado:
        p["inicio"] = round(max(0.0, min(p["inicio"], duracao)), 3)
        p["fim"] = round(max(p["inicio"], min(p["fim"], duracao)), 3)
    return resultado


# ---------------------------------------------------------------- áudio
def processar_audio(bruto: Path, saida: Path, pausa_inicio: float, pausa_fim: float) -> float:
    corte = "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:start_silence=0.04"
    filtro = (f"{corte},areverse,{corte},areverse,loudnorm=I=-18:TP=-3:LRA=7,aresample=48000,"
              f"adelay={int(pausa_inicio * 1000)}:all=1,apad=pad_dur={pausa_fim:.3f}")
    ffmpeg.rodar(["-i", bruto, "-af", filtro, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", saida],
                 descricao="Tratamento do áudio da narração")
    return ffmpeg.duracao(saida)


class FalhaDoMotor(ErroNarracao):
    """O motor de voz falhou tecnicamente (não é problema de pronúncia)."""


def _sintetizar_bloco(bloco: dict, canal: Canal, motor, auditor: Auditor, llm, pasta_tmp: Path,
                      destino: Path) -> dict:
    """Sintetiza UM bloco com UM motor (a voz precisa ser a mesma no vídeo todo).

    Tentativas: 1) texto + dicionário do canal; 2) números e siglas por extenso;
    3) reescrita fonética pelo LLM (se houver) ou fala 8% mais lenta.
    """
    limite = env_float("LIMITE_ERRO_PRONUNCIA", 0.08)
    max_tentativas = max(1, env_int("MAX_TENTATIVAS_NARRACAO", 3))
    ritmo = canal.ritmo
    original = bloco["narracao"]
    base = bloco.get("texto_fonetico") or original
    base = limpar_simbolos(aplicar_pronuncias(base, canal.voz.get("pronuncias", {})))
    ref_original = tokens_wer(original)
    tentativas: list[dict] = []
    texto = base
    problemas: set[str] = set()
    for n in range(1, max_tentativas + 1):
        fator = 1.0
        if n == 1 and motor.nome == "kokoro":
            texto = util.expandir_numeros(base)
        elif n == 2:
            texto = expandir_siglas(util.expandir_numeros(texto), None)
        elif n >= 3:
            reescrito = reescrever_com_llm(llm, texto, problemas) if llm else None
            if reescrito:
                texto = reescrito
            else:
                fator = 0.92
        bruto = pasta_tmp / f"bloco_{bloco['id']:03d}_t{n}_{motor.nome}"
        try:
            arquivo_bruto = motor.sintetizar(texto, bruto, fator)
        except Exception as exc:
            raise FalhaDoMotor(f"Motor {motor.nome} falhou no bloco {bloco['id']}: {exc}") from exc
        tratado = pasta_tmp / f"bloco_{bloco['id']:03d}_t{n}.wav"
        duracao = processar_audio(arquivo_bruto, tratado, float(ritmo.get("pausa_inicio_seg", 0.15)),
                                  float(ritmo.get("pausa_fim_seg", 0.3)))
        esperado = util.contar_palavras(original) / canal.palavras_por_segundo
        ouvidas = auditor.transcrever(tratado) if duracao > 0.5 else []
        if ouvidas is None:
            wer = 0.0
        else:
            hip = tokens_wer(" ".join(p["palavra"] for p in ouvidas))
            wer = min(taxa_erro(ref_original, hip), taxa_erro(tokens_wer(texto), hip))
        if duracao < 0.5 or duracao > esperado * 2.8 + 2:
            wer = max(wer, 1.0)  # áudio vazio ou com falha grave
        tentativas.append({"tentativa": n, "motor": motor.nome, "texto": texto, "wer": round(wer, 4),
                           "duracao": round(duracao, 3), "arquivo": tratado, "ouvidas": ouvidas})
        if wer <= limite:
            break
        problemas = palavras_problematicas(original, ouvidas or [])
        log.info(f"Bloco {bloco['id']}: erro de pronúncia {wer:.0%} com {motor.nome} — ajustando fonética "
                 f"({', '.join(sorted(problemas)[:5]) or 'falha de áudio'})")
    if not tentativas:
        raise ErroNarracao(f"Nenhum motor de voz conseguiu sintetizar o bloco {bloco['id']}.")
    melhor = min(tentativas, key=lambda t: (t["wer"], t["tentativa"]))
    shutil.copyfile(melhor["arquivo"], destino)
    palavras = alinhar_palavras(original, melhor["ouvidas"], melhor["duracao"],
                                float(ritmo.get("pausa_inicio_seg", 0.15)))
    return {"bloco": bloco["id"], "motor": melhor["motor"], "wer": melhor["wer"], "texto_fonetico_usado": melhor["texto"],
            "tentativas": len(tentativas), "duracao": melhor["duracao"], "palavras": palavras,
            "transcricao": " ".join(p["palavra"] for p in (melhor["ouvidas"] or [])),
            "auditado": melhor["ouvidas"] is not None, "acima_do_limite": melhor["wer"] > limite}


# ---------------------------------------------------------------- legendas
def gerar_srt(palavras: list[dict]) -> str:
    cues, atual = [], []
    for i, p in enumerate(palavras):
        atual.append(p)
        texto = " ".join(x["palavra"] for x in atual)
        proxima = palavras[i + 1] if i + 1 < len(palavras) else None
        pausa = (proxima["inicio"] - p["fim"]) if proxima else 99
        if len(texto) >= 38 or len(atual) >= 8 or pausa > 0.6 or re.search(r"[.!?…]$", p["palavra"]) or not proxima:
            fim = p["fim"] + 0.15
            if proxima:
                fim = min(fim, proxima["inicio"])
            cues.append((atual[0]["inicio"], max(fim, atual[0]["inicio"] + 0.4), texto))
            atual = []
    linhas = []
    for n, (ini, fim, texto) in enumerate(cues, 1):
        linhas.append(f"{n}\n{util.tempo_srt(ini)} --> {util.tempo_srt(fim)}\n{texto}\n")
    return "\n".join(linhas)


def montar_saidas(projeto: Projeto, roteiro: dict) -> list[dict]:
    """Concatena a narração completa e gera linha do tempo, palavras globais e SRT."""
    linha, palavras_globais, lista = [], [], []
    inicio = 0.0
    for bloco in roteiro["blocos"]:
        wav = projeto.p("audio", f"bloco_{bloco['id']:03d}.wav")
        dados = projeto.ler(f"legendas/bloco_{bloco['id']:03d}.json")
        if not wav.exists() or not dados:
            raise ErroNarracao(f"Falta o áudio do bloco {bloco['id']}; rode a etapa de narração novamente.")
        duracao = ffmpeg.duracao(wav)
        linha.append({"bloco": bloco["id"], "inicio": round(inicio, 4), "fim": round(inicio + duracao, 4),
                      "duracao": round(duracao, 4)})
        for p in dados["palavras"]:
            palavras_globais.append({"palavra": p["palavra"], "inicio": round(inicio + p["inicio"], 3),
                                     "fim": round(inicio + p["fim"], 3), "bloco": bloco["id"]})
        lista.append(f"file '{wav.name}'")
        inicio += duracao
    util.salvar_texto(projeto.p("audio", "lista_concat.txt"), "\n".join(lista) + "\n")
    ffmpeg.rodar(["-f", "concat", "-safe", "0", "-i", "lista_concat.txt", "-c:a", "pcm_s16le",
                  "narracao_completa.wav"], cwd=projeto.p("audio"), descricao="Concatenação da narração")
    projeto.salvar("render/linha_tempo.json", linha)
    projeto.salvar("legendas/palavras.json", palavras_globais)
    util.salvar_texto(projeto.p("legendas", "video.srt"), gerar_srt(palavras_globais))
    return linha


# ------------------------------------------------------------------ etapa
def executar(projeto: Projeto, canal: Canal, blocos_alvo: set[int] | None = None, forcar: bool = False) -> None:
    log.etapa("Módulo 2 — Narração neural e auditoria de pronúncia")
    roteiro = projeto.roteiro()
    motores = preparar_motores(canal)
    log.info("Motores de voz: " + " → ".join(m.nome for m in motores))
    auditor = Auditor()
    from ..llm import obter_cliente

    llm = obter_cliente()
    cache = projeto.ler("audio/cache.json", {}) or {}
    pasta_tmp = projeto.p("audio", "tentativas")
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    automatico = str(canal.voz.get("motor", "auto")).lower() == "auto"
    # Voz única por vídeo: o motor escolhido fica gravado no projeto e vale para reprocessamentos.
    fixado = cache.get("_motor_projeto") if automatico else None
    motor = next((m for m in motores if m.nome == fixado), motores[0])
    ids_atuais = {b["id"] for b in roteiro["blocos"]}

    def narrar(motor_atual, forcar_todos: bool) -> list[dict]:
        resumo_local = []
        for bloco in roteiro["blocos"]:
            bid = bloco["id"]
            destino = projeto.p("audio", f"bloco_{bid:03d}.wav")
            legenda = projeto.p("legendas", f"bloco_{bid:03d}.json")
            chave = util.hash_dados({"v": VERSAO, "narracao": bloco["narracao"], "fonetico": bloco.get("texto_fonetico"),
                                     "voz": canal.voz, "motor": motor_atual.nome,
                                     "pausas": [canal.ritmo.get("pausa_inicio_seg"), canal.ritmo.get("pausa_fim_seg")]})
            alvo = forcar_todos or (blocos_alvo is not None and bid in blocos_alvo)
            if not alvo and cache.get(str(bid), {}).get("chave") == chave and destino.exists() and legenda.exists():
                resumo_local.append(cache[str(bid)]["resumo"])
                continue
            resultado = _sintetizar_bloco(bloco, canal, motor_atual, auditor, llm, pasta_tmp, destino)
            projeto.salvar(f"legendas/bloco_{bid:03d}.json", resultado)
            info = {k: resultado[k] for k in ("bloco", "motor", "wer", "tentativas", "duracao", "auditado",
                                               "acima_do_limite")}
            cache[str(bid)] = {"chave": chave, "resumo": info}
            projeto.salvar("audio/cache.json", cache)
            resumo_local.append(info)
            simbolo = "⚠" if resultado["acima_do_limite"] else "✔"
            log.info(f"{simbolo} Bloco {bid:03d}: {resultado['duracao']:.1f}s via {resultado['motor']} "
                     f"(erro {resultado['wer']:.0%}, {resultado['tentativas']} tentativa(s))")
        return resumo_local

    alternativo = next((m for m in motores if m.nome != motor.nome), None)
    try:
        resumo = narrar(motor, forcar)
    except FalhaDoMotor as exc:
        if not alternativo:
            raise
        log.aviso(f"{exc} — refazendo a narração inteira com {alternativo.nome} para manter uma voz única.")
        motor, alternativo = alternativo, None
        resumo = narrar(motor, True)
    ruins = [r for r in resumo if r.get("acima_do_limite")]
    if automatico and not fixado and alternativo and resumo and len(ruins) / len(resumo) > 0.3:
        log.aviso(f"{motor.nome} errou a pronúncia em {len(ruins)} de {len(resumo)} blocos; refazendo TODA a "
                  f"narração com {alternativo.nome} para manter uma voz única no vídeo.")
        try:
            motor = alternativo
            resumo = narrar(motor, True)
        except FalhaDoMotor as exc:
            log.aviso(f"{exc}; mantendo a narração anterior.")
            motor = next(m for m in motores if m.nome != alternativo.nome)
            resumo = narrar(motor, True)
    cache["_motor_projeto"] = motor.nome
    projeto.salvar("audio/cache.json", cache)
    log.info(f"Voz do vídeo: {motor.nome} ({motor.voz})")
    # remove sobras de blocos que não existem mais
    for arquivo in list(projeto.p("audio").glob("bloco_*.wav")) + list(projeto.p("legendas").glob("bloco_*.json")):
        m = re.search(r"bloco_(\d+)", arquivo.name)
        if m and int(m.group(1)) not in ids_atuais:
            arquivo.unlink(missing_ok=True)
    shutil.rmtree(pasta_tmp, ignore_errors=True)
    linha = montar_saidas(projeto, roteiro)
    projeto.salvar("audio/auditoria.json", resumo)
    projeto.limpar_alertas("narracao")
    problematicos = [r["bloco"] for r in resumo if r.get("acima_do_limite")]
    if problematicos:
        projeto.alertar("narracao", f"Pronúncia acima do limite nos blocos {problematicos}: ouça esses trechos.")
    if resumo and not all(r.get("auditado") for r in resumo):
        projeto.alertar("narracao", "Auditoria Whisper indisponível em alguns blocos; tempos das legendas estimados.")
    metadados.atualizar(projeto, canal)
    total = linha[-1]["fim"] if linha else 0
    projeto.marcar_etapa("narracao", True, duracao_seg=round(total, 2), blocos=len(linha),
                         blocos_com_alerta=problematicos)
    log.ok(f"Narração completa: {util.formatar_tempo(total)} em {len(linha)} blocos")
