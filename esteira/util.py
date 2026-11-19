"""Funções utilitárias: texto em português, JSON atômico, hashes e tempo."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

STOPWORDS = set(
    """
    a à às ao aos as até após antes com como contra da das de dela dele deles delas do dos
    e é ela elas ele eles em entre era eram essa essas esse esses esta estas este estes está
    estão estava estavam eu foi foram fosse há isso isto já la lá lhe lhes mais mas me mesmo
    mesma meu meus minha minhas muito muita muitos muitas na nas não nem no nos nós num numa
    o os ou para pela pelas pelo pelos por porque pois qual quais quando que quem se sem ser
    seu seus si sido só sua suas também tão te tem têm tinha tinham um uma umas uns você vocês
    vai vão ter sobre onde assim então ainda cada outro outra outros outras todo toda todos
    todas tudo nada algo alguém aqui ali seja são sendo será serão fazer feito fez faz pode
    podem poderia já agora hoje ontem sempre nunca vez vezes coisa coisas aquele aquela aquilo
    nesse nessa neste nesta desse dessa deste desta num numa pra pro the of and to in is was
    """.split()
)

# Palavras "de isca" comuns em canais dark que não distinguem um tema de outro.
GENERICAS = {
    "historia", "historias", "misterio", "misterios", "misterioso", "misteriosa", "segredo",
    "segredos", "verdade", "verdadeira", "verdadeiro", "real", "reais", "incrivel", "incriveis",
    "bizarro", "bizarra", "assustador", "assustadora", "curiosidade", "curiosidades", "fato",
    "fatos", "ninguem", "sabe", "conhece", "oculto", "oculta", "ocultos", "revelado", "revelada",
    "chocante", "impressionante", "inexplicavel", "inexplicaveis", "conta", "contam", "explicado",
    "explicada", "caso", "casos", "maior", "maiores", "mundo", "tudo", "sobre",
}


def sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, apenas letras/números e espaços simples."""
    texto = sem_acentos(texto.lower())
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def tokens(texto: str, remover_stop: bool = True, remover_genericas: bool = False) -> list[str]:
    lista = normalizar(texto).split()
    if remover_stop:
        stop = {normalizar(s) for s in STOPWORDS}
        lista = [t for t in lista if t not in stop and len(t) > 1]
    if remover_genericas:
        lista = [t for t in lista if t not in GENERICAS]
    return lista


def radical(token: str) -> str:
    """Radical bem simples (6 primeiros caracteres sem plural)."""
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token[:6]


def palavras_chave(texto: str, n: int = 8) -> list[str]:
    contagem: dict[str, int] = {}
    posicao: dict[str, int] = {}
    for tok in tokens(texto, remover_genericas=True):
        if len(tok) < 3 or tok.isdigit():
            continue
        posicao.setdefault(tok, len(posicao))
        contagem[tok] = contagem.get(tok, 0) + 1
    return sorted(contagem, key=lambda t: (-contagem[t], posicao[t]))[:n]


def palavras_chave_originais(texto: str, n: int = 3) -> list[str]:
    """Como palavras_chave, mas devolve as palavras com a grafia original (acentos, maiúsculas)."""
    originais: dict[str, str] = {}
    for bruta in re.findall(r"[\wÀ-ÿ'-]+", texto):
        originais.setdefault(normalizar(bruta), bruta.strip("'-"))
    return [originais.get(chave, chave) for chave in palavras_chave(texto, n)]


def slugify(texto: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", sem_acentos(texto.lower())).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or "sem-titulo"


def hash_dados(obj: Any) -> str:
    bruto = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:16]


def hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha1()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(1 << 20), b""):
            h.update(pedaco)
    return h.hexdigest()[:16]


def ler_json(caminho: Path, padrao: Any = None) -> Any:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return padrao
    except json.JSONDecodeError as exc:
        raise ValueError(f"Arquivo JSON inválido: {caminho} ({exc})") from exc


def salvar_json(caminho: Path, dados: Any) -> None:
    """Gravação atômica: escreve em arquivo temporário e substitui."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(caminho.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, caminho)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def salvar_texto(caminho: Path, texto: str) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(caminho.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    os.replace(tmp, caminho)


def agora() -> dt.datetime:
    return dt.datetime.now().astimezone()


def agora_iso() -> str:
    return agora().isoformat(timespec="seconds")


def formatar_tempo(segundos: float, forcar_horas: bool = False) -> str:
    total = int(round(max(0.0, segundos)))
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    if h or forcar_horas:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def tempo_srt(segundos: float) -> str:
    ms = int(round(max(0.0, segundos) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def tempo_ass(segundos: float) -> str:
    cs = int(round(max(0.0, segundos) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def dividir_frases(texto: str) -> list[str]:
    texto = re.sub(r"\s+", " ", texto.strip())
    if not texto:
        return []
    partes = re.split(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9\"“(¿¡—-])", texto)
    return [p.strip() for p in partes if p.strip()]


def contar_palavras(texto: str) -> int:
    return len(re.findall(r"\S+", texto))


def limitar(texto: str, n: int, reticencias: str = "…") -> str:
    texto = texto.strip()
    if len(texto) <= n:
        return texto
    corte = texto[: n - len(reticencias)]
    if " " in corte:
        corte = corte.rsplit(" ", 1)[0]
    return corte.rstrip(" ,;:-") + reticencias


def mesclar(base: dict, extra: dict) -> dict:
    """Mescla dicionários recursivamente (extra vence)."""
    for chave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(base.get(chave), dict):
            mesclar(base[chave], valor)
        else:
            base[chave] = valor
    return base


def lotes(lista: list, tamanho: int) -> Iterable[list]:
    for i in range(0, len(lista), tamanho):
        yield lista[i : i + tamanho]


# ---------------------------------------------------------------------------
# Números por extenso (pt-BR) — usados pela narração e pela auditoria
# ---------------------------------------------------------------------------
_ESCALAS = r"(mil|milh(?:ão|ões|oes|ao)|bilh(?:ão|ões|oes|ao)|trilh(?:ão|ões|oes|ao))"
_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _num2words(valor: float | int, ordinal: bool = False) -> str:
    try:
        from num2words import num2words
    except ImportError:
        return str(valor)
    try:
        if ordinal:
            return num2words(int(valor), to="ordinal", lang="pt_BR")
        return num2words(valor, lang="pt_BR")
    except Exception:
        return str(valor)


def _para_numero(bruto: str) -> float | int:
    limpo = bruto.replace(".", "").replace(" ", "")
    if "," in limpo:
        return float(limpo.replace(",", "."))
    return int(limpo)


def _romano_para_int(romano: str) -> int | None:
    total, anterior = 0, 0
    for letra in reversed(romano):
        valor = _ROMANOS.get(letra)
        if valor is None:
            return None
        total = total - valor if valor < anterior else total + valor
        anterior = max(anterior, valor)
    return total or None


def expandir_numeros(texto: str) -> str:
    """Converte números, moedas, porcentagens, ordinais e séculos para extenso."""

    def moeda(m: re.Match) -> str:
        numero = _num2words(_para_numero(m.group(1)))
        escala = m.group(2)
        if escala:
            return f"{numero} {escala} de reais"
        return f"{numero} reais"

    texto = re.sub(r"R\$\s?(\d[\d.]*(?:,\d+)?)\s?" + f"{_ESCALAS}?", moeda, texto, flags=re.I)
    texto = re.sub(r"US\$\s?(\d[\d.]*(?:,\d+)?)", lambda m: f"{_num2words(_para_numero(m.group(1)))} dólares", texto)
    texto = re.sub(r"(\d+(?:,\d+)?)\s?%", lambda m: f"{_num2words(_para_numero(m.group(1)))} por cento", texto)

    def ordinal(m: re.Match) -> str:
        palavra = _num2words(int(m.group(1)), ordinal=True)
        if m.group(2) == "ª":
            palavra = " ".join(p[:-1] + "a" if p.endswith("o") else p for p in palavra.split())
        return palavra

    texto = re.sub(r"(\d+)\s?([ºª°])", ordinal, texto)

    def seculo(m: re.Match) -> str:
        valor = _romano_para_int(m.group(2))
        return f"{m.group(1)} {_num2words(valor)}" if valor else m.group(0)

    texto = re.sub(r"\b(s[ée]culo|s[ée]c\.)\s+([IVXLC]{1,6})\b", seculo, texto, flags=re.I)

    def regnal(m: re.Match) -> str:
        valor = _romano_para_int(m.group(2))
        if not valor or valor > 40:
            return m.group(0)
        return f"{m.group(1)} {_num2words(valor, ordinal=valor <= 10)}"

    texto = re.sub(r"\b([A-ZÁÉÍÓÚ][a-záéíóúâêôãõç]{2,})\s+((?:X{0,3})(?:IX|IV|V?I{0,3}))\b(?![a-z])", lambda m: regnal(m) if m.group(2) else m.group(0), texto)
    texto = re.sub(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+", lambda m: _num2words(_para_numero(m.group(0))), texto)
    return re.sub(r"\s+", " ", texto).strip()
