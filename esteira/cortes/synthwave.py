"""Trilhas synthwave ORIGINAIS geradas aqui mesmo (clima "Vice City": pôr do sol, neon, anos 80).

Como cada faixa é composta e sintetizada pela esteira, ela não tem dono no Content ID do YouTube e pode ser
usada e monetizada sem crédito. Só numpy (filtros por FFT, reverb por convolução) + FFmpeg para gravar o MP3.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .. import ffmpeg, log

SR = 44100
# nome, tônica MIDI, progressão (graus em semitons a partir da tônica, acorde menor/maior), BPM, semente
ESTILOS = {
    "por_do_sol_neon": {"nome": "Pôr do Sol Neon", "tonica": 57, "bpm": 100, "semente": 7,
                        "acordes": [(0, "m"), (-4, "M"), (3, "M"), (-2, "M")]},       # Am F C G
    "avenida_noturna": {"nome": "Avenida Noturna", "tonica": 50, "bpm": 92, "semente": 13,
                        "acordes": [(0, "m"), (-4, "M"), (3, "M"), (-2, "M")]},       # Dm Bb F C
    "estrada_turbo": {"nome": "Estrada Turbo", "tonica": 52, "bpm": 112, "semente": 21,
                      "acordes": [(0, "m"), (-4, "M"), (-9, "M"), (-2, "M")]},        # Em C G D
}


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _acorde(raiz: int, tipo: str) -> list[int]:
    return [raiz, raiz + (3 if tipo == "m" else 4), raiz + 7]


def _filtro(sinal: np.ndarray, corte: float, tipo: str = "passa_baixa", ordem: int = 4) -> np.ndarray:
    """Filtro suave no domínio da frequência (sinal inteiro de uma vez)."""
    espectro = np.fft.rfft(sinal, axis=0)
    f = np.fft.rfftfreq(sinal.shape[0], 1 / SR)
    razao = (f / corte) ** ordem
    ganho = 1 / np.sqrt(1 + razao) if tipo == "passa_baixa" else np.sqrt(razao / (1 + razao))
    if sinal.ndim == 2:
        ganho = ganho[:, None]
    return np.fft.irfft(espectro * ganho, n=sinal.shape[0], axis=0)


def _conv(sinal: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = sinal.shape[0] + ir.shape[0] - 1
    tamanho = 1 << (n - 1).bit_length()
    saida = np.fft.irfft(np.fft.rfft(sinal, tamanho) * np.fft.rfft(ir, tamanho), tamanho)
    return saida[:sinal.shape[0]]


def _serra(freq: float, n: int, fase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    return 2 * ((t * freq + fase) % 1.0) - 1


def _quadrada(freq: float, n: int) -> np.ndarray:
    return np.sign(np.sin(2 * np.pi * freq * np.arange(n) / SR))


def _colar(destino: np.ndarray, sinal: np.ndarray, inicio: int) -> None:
    fim = min(destino.shape[0], inicio + sinal.shape[0])
    if fim > inicio:
        destino[inicio:fim] += sinal[:fim - inicio]


def compor(estilo: str = "por_do_sol_neon", compassos: int = 32) -> np.ndarray:
    """Devolve um array estéreo (n, 2) float com a faixa inteira."""
    cfg = ESTILOS[estilo]
    rng = np.random.default_rng(cfg["semente"])
    batida = 60 / cfg["bpm"]
    compasso = 4 * batida
    n_total = int(compassos * compasso * SR) + SR
    pad, baixo, arpejo = (np.zeros(n_total) for _ in range(3))
    bumbo, caixa, chimbal = (np.zeros(n_total) for _ in range(3))
    progressao = cfg["acordes"]

    for c in range(compassos):
        raiz_grau, tipo = progressao[c % len(progressao)]
        raiz = cfg["tonica"] + raiz_grau
        notas = _acorde(raiz, tipo)
        ini = int(c * compasso * SR)
        n = int(compasso * SR) + int(0.25 * SR)
        t = np.arange(n) / SR
        envelope = np.minimum(1, t / 0.08) * np.clip((compasso + 0.25 - t) / 0.25, 0, 1)
        # pad: 3 notas x 3 osciladores levemente desafinados
        for nota in notas + [notas[0] + 12]:
            for desafino in (-0.11, 0.0, 0.12):
                _colar(pad, _serra(_freq(nota + desafino), n, rng.random()) * envelope * 0.12, ini)
        # baixo em colcheias (a introdução tem só pad)
        if c >= 2:
            for k in range(8):
                nb = int(batida / 2 * SR)
                tb = np.arange(nb) / SR
                nota_baixo = raiz - 24 + (12 if k in (3, 7) else 0)
                _colar(baixo, _serra(_freq(nota_baixo), nb) * np.exp(-tb / 0.22), ini + k * nb)
        # arpejo em semicolcheias a partir do compasso 4
        if c >= 4:
            sequencia = [notas[0], notas[1], notas[2], notas[0] + 12, notas[2], notas[1]]
            for k in range(16):
                na = int(batida / 4 * SR)
                ta = np.arange(na) / SR
                nota_arp = sequencia[k % len(sequencia)] + 12
                _colar(arpejo, _quadrada(_freq(nota_arp), na) * np.exp(-ta / 0.11), ini + k * na)
        # bateria a partir do compasso 2: bumbo nos 4 tempos, caixa no 2 e 4, chimbal nos contratempos
        if c >= 2:
            for b in range(4):
                pos = ini + int(b * batida * SR)
                nk = int(0.45 * SR)
                tk = np.arange(nk) / SR
                freq_k = 45 + 85 * np.exp(-tk / 0.035)
                _colar(bumbo, np.sin(2 * np.pi * np.cumsum(freq_k) / SR) * np.exp(-tk / 0.26), pos)
                if b in (1, 3):
                    ns = int(0.35 * SR)
                    ts = np.arange(ns) / SR
                    corpo = np.sin(2 * np.pi * 185 * ts) * np.exp(-ts / 0.07)
                    _colar(caixa, (rng.standard_normal(ns) * 0.7 * np.exp(-ts / 0.11) + corpo), pos)
                nh = int(0.06 * SR)
                _colar(chimbal, rng.standard_normal(nh) * np.exp(-np.arange(nh) / SR / 0.018),
                       pos + int(batida / 2 * SR))

    # bombeamento (sidechain) no ritmo do bumbo
    t_total = np.arange(n_total) / SR
    fase_batida = (t_total % batida)
    bomba = 1 - 0.5 * np.exp(-fase_batida / 0.11) * (t_total >= 2 * compasso)

    pad = _filtro(pad, 1700) * bomba
    baixo = _filtro(baixo, 650) * bomba
    arpejo = _filtro(arpejo, 3200) * bomba
    chimbal = _filtro(chimbal, 7000, "passa_alta")
    caixa = _filtro(caixa, 9000)

    # eco do arpejo (colcheia pontuada), alternando os lados
    atraso = int(batida * 0.75 * SR)
    eco_e = np.zeros(n_total)
    eco_d = np.zeros(n_total)
    eco_e[atraso:] = arpejo[:-atraso] * 0.38
    eco_d[2 * atraso:] = arpejo[:-2 * atraso] * 0.24

    # reverb (resposta ao impulso de ruído com decaimento)
    n_ir = int(2.2 * SR)
    t_ir = np.arange(n_ir) / SR
    ir_e = rng.standard_normal(n_ir) * np.exp(-t_ir / 0.75)
    ir_d = rng.standard_normal(n_ir) * np.exp(-t_ir / 0.75)
    envio = pad * 0.5 + caixa * 0.9 + arpejo * 0.4
    rev_e = _conv(envio, ir_e)
    rev_d = _conv(envio, ir_d)
    rev_e *= 0.18 / (np.max(np.abs(rev_e)) + 1e-9)
    rev_d *= 0.18 / (np.max(np.abs(rev_d)) + 1e-9)

    def norm(x: np.ndarray, alvo: float) -> np.ndarray:
        return x * (alvo / (np.max(np.abs(x)) + 1e-9))

    pad, baixo, arpejo = norm(pad, 0.4), norm(baixo, 0.42), norm(arpejo, 0.24)
    bumbo, caixa, chimbal = norm(bumbo, 0.6), norm(caixa, 0.32), norm(chimbal, 0.1)
    esquerda = pad * 1.0 + baixo + arpejo * 0.8 + eco_e * 0.5 + bumbo + caixa + chimbal * 0.8 + rev_e
    direita = pad * 0.95 + baixo + arpejo * 0.6 + eco_d * 0.5 + bumbo + caixa + chimbal * 1.0 + rev_d
    mix = np.stack([esquerda, direita], axis=1)[: int(compassos * compasso * SR)]
    mix = np.tanh(mix * 1.1)  # saturação suave
    entrada, saida = int(0.8 * SR), int(2.5 * SR)
    mix[:entrada] *= np.linspace(0, 1, entrada)[:, None]
    mix[-saida:] *= np.linspace(1, 0, saida)[:, None]
    return mix / (np.max(np.abs(mix)) + 1e-9) * 0.89


def gerar(destino: Path, estilo: str = "por_do_sol_neon", compassos: int = 32) -> Path:
    """Compõe a faixa e grava em MP3 (destino com extensão .mp3)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    audio = compor(estilo, compassos)
    wav = destino.with_suffix(".tmp.wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype("<i2").tobytes())
    ffmpeg.rodar(["-i", wav, "-c:a", "libmp3lame", "-q:a", "2", destino], descricao="Trilha synthwave")
    wav.unlink(missing_ok=True)
    log.ok(f"Trilha original criada: {destino.name} ({ESTILOS[estilo]['nome']}, {len(audio) / SR:.0f} s)")
    return destino


def gerar_pacote(pasta: Path) -> list[Path]:
    """As 3 faixas do pacote (pula as que já existem)."""
    criadas = []
    for chave in ESTILOS:
        alvo = Path(pasta) / f"synthwave_{chave}.mp3"
        if not alvo.exists():
            criadas.append(gerar(alvo, chave))
    return criadas
