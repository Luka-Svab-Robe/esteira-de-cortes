"""Banco SQLite por canal: temas já produzidos, agendamentos e anti-duplicidade."""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import log, util

ESTADOS_ATIVOS = ("em_producao", "pendente_revisao", "reprovado", "aprovado", "agendado", "publicado")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tema TEXT NOT NULL,
    titulo TEXT DEFAULT '',
    resumo TEXT DEFAULT '',
    palavras_chave TEXT DEFAULT '',
    projeto TEXT UNIQUE,
    estado TEXT DEFAULT 'em_producao',
    youtube_id TEXT DEFAULT '',
    publicar_em TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agendamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto TEXT,
    tipo TEXT NOT NULL,
    youtube_id TEXT,
    titulo TEXT,
    publicar_em TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agendamentos_data ON agendamentos(publicar_em);
"""


@dataclass
class ResultadoDuplicidade:
    bloqueado: bool
    motivo: str = ""
    similares: list = field(default_factory=list)  # [(registro_dict, pontuacao)]
    em_duvida: bool = False


class Historico:
    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(ESQUEMA)

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.caminho), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # ---------------------------------------------------------------- vídeos
    def registrar(self, tema: str, projeto: str, titulo: str = "", resumo: str = "",
                  palavras_chave: list[str] | None = None, estado: str = "em_producao") -> None:
        agora = util.agora_iso()
        with self._con() as con:
            con.execute(
                """INSERT INTO videos (tema, titulo, resumo, palavras_chave, projeto, estado, criado_em, atualizado_em)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(projeto) DO UPDATE SET tema=excluded.tema, titulo=excluded.titulo,
                   resumo=excluded.resumo, palavras_chave=excluded.palavras_chave, estado=excluded.estado,
                   atualizado_em=excluded.atualizado_em""",
                (tema, titulo, resumo, ", ".join(palavras_chave or []), projeto, estado, agora, agora))

    def atualizar(self, projeto: str, **campos) -> None:
        if not campos:
            return
        campos["atualizado_em"] = util.agora_iso()
        colunas = ", ".join(f"{c}=?" for c in campos)
        with self._con() as con:
            con.execute(f"UPDATE videos SET {colunas} WHERE projeto=?", (*campos.values(), projeto))

    def listar(self, estados: tuple[str, ...] | None = None) -> list[dict]:
        with self._con() as con:
            if estados:
                marcas = ",".join("?" * len(estados))
                linhas = con.execute(f"SELECT * FROM videos WHERE estado IN ({marcas}) ORDER BY id DESC", estados)
            else:
                linhas = con.execute("SELECT * FROM videos ORDER BY id DESC")
            return [dict(l) for l in linhas]

    # ----------------------------------------------------------- agendamentos
    def registrar_agendamento(self, projeto: str, tipo: str, youtube_id: str, titulo: str, publicar_em: str) -> None:
        with self._con() as con:
            con.execute("INSERT INTO agendamentos (projeto, tipo, youtube_id, titulo, publicar_em, criado_em) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (projeto, tipo, youtube_id, titulo, publicar_em, util.agora_iso()))

    def cancelar_agendamentos(self, projeto: str) -> int:
        """Libera na agenda local os horários de um projeto (ex.: agendamento cancelado)."""
        with self._con() as con:
            return con.execute("DELETE FROM agendamentos WHERE projeto = ?", (projeto,)).rowcount

    def cancelar_agendamento_video(self, youtube_id: str) -> int:
        """Libera na agenda local o horário de um vídeo só (ex.: Short retirado por restrição)."""
        with self._con() as con:
            return con.execute("DELETE FROM agendamentos WHERE youtube_id = ?", (youtube_id,)).rowcount

    def reagendar_video(self, youtube_id: str, publicar_em: str) -> int:
        """Nova data de publicação de um vídeo na agenda local."""
        with self._con() as con:
            return con.execute("UPDATE agendamentos SET publicar_em = ? WHERE youtube_id = ?",
                               (publicar_em, youtube_id)).rowcount

    def agendamentos_futuros(self) -> list[dict]:
        agora = dt.datetime.now(dt.timezone.utc)
        with self._con() as con:
            linhas = [dict(l) for l in con.execute("SELECT * FROM agendamentos ORDER BY publicar_em")]
        futuros = []
        for linha in linhas:
            try:
                quando = dt.datetime.fromisoformat(linha["publicar_em"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if quando > agora:
                linha["quando"] = quando
                futuros.append(linha)
        return futuros

    # -------------------------------------------------------- anti-duplicidade
    @staticmethod
    def _radicais(texto: str) -> list[str]:
        return [util.radical(t) for t in util.tokens(texto, remover_genericas=True) if len(t) > 2]

    @staticmethod
    def _trigramas(texto: str) -> Counter:
        base = " ".join(util.tokens(texto, remover_genericas=True))
        base = f"  {base}  "
        return Counter(base[i:i + 3] for i in range(len(base) - 2))

    @staticmethod
    def _cosseno(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        comum = set(a) & set(b)
        num = sum(a[k] * b[k] for k in comum)
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0

    def similares(self, tema: str, texto_extra: str = "", limite: int = 5) -> list[tuple[dict, float]]:
        registros = self.listar(ESTADOS_ATIVOS)
        if not registros:
            return []
        consulta_tema = set(self._radicais(tema))
        consulta_doc = Counter(self._radicais(f"{tema} {tema} {texto_extra}"))
        tri_consulta = self._trigramas(tema)
        documentos = [Counter(self._radicais(f"{r['tema']} {r['titulo']} {r['palavras_chave']} {r['resumo']}"))
                      for r in registros]
        # IDF calculado sobre o histórico do próprio canal (palavras comuns pesam menos)
        n_docs = len(documentos) + 1
        df = Counter()
        for doc in documentos + [consulta_doc]:
            df.update(set(doc))
        idf = {t: math.log((1 + n_docs) / (1 + df[t])) + 1 for t in df}

        def tfidf(doc: Counter) -> Counter:
            return Counter({t: c * idf.get(t, 1.0) for t, c in doc.items()})

        consulta_vet = tfidf(consulta_doc)
        resultado = []
        for registro, doc in zip(registros, documentos):
            tema_reg = set(self._radicais(f"{registro['tema']} {registro['titulo']}"))
            jaccard = len(consulta_tema & tema_reg) / len(consulta_tema | tema_reg) if consulta_tema and tema_reg else 0.0
            contencao = (len(consulta_tema & tema_reg) / min(len(consulta_tema), len(tema_reg))
                         if consulta_tema and tema_reg else 0.0)
            cos_doc = self._cosseno(consulta_vet, tfidf(doc))
            cos_tri = self._cosseno(tri_consulta, self._trigramas(registro["tema"]))
            pontuacao = max(cos_doc, 0.9 * jaccard, 0.75 * contencao, 0.85 * cos_tri)
            resultado.append((registro, round(pontuacao, 3)))
        resultado.sort(key=lambda par: par[1], reverse=True)
        return resultado[:limite]

    def verificar_duplicidade(self, tema: str, texto_extra: str = "", llm=None,
                              limiar: float = 0.55, limiar_duvida: float = 0.3) -> ResultadoDuplicidade:
        candidatos = self.similares(tema, texto_extra)
        if not candidatos:
            return ResultadoDuplicidade(False, "Nenhum tema anterior no histórico.")
        melhor, pontuacao = candidatos[0]
        if pontuacao >= limiar:
            return ResultadoDuplicidade(True, f"Muito parecido com '{melhor['tema']}' (similaridade {pontuacao:.0%}).",
                                        candidatos)
        if pontuacao < limiar_duvida and llm is None:
            return ResultadoDuplicidade(False, "Tema inédito no canal.", candidatos)
        if llm is not None:
            julgamento = self._julgar_com_llm(llm, tema, texto_extra, [c[0] for c in candidatos[:8]])
            if julgamento is not None:
                if julgamento.get("duplicado"):
                    return ResultadoDuplicidade(
                        True, f"O modelo de linguagem considerou o assunto repetido: {julgamento.get('justificativa', '')}",
                        candidatos)
                return ResultadoDuplicidade(False, "Tema inédito (confirmado semanticamente).", candidatos)
        return ResultadoDuplicidade(False, f"Atenção: tema parecido com '{melhor['tema']}' ({pontuacao:.0%}).",
                                    candidatos, em_duvida=True)

    @staticmethod
    def _julgar_com_llm(llm, tema: str, texto_extra: str, registros: list[dict]) -> dict | None:
        lista = "\n".join(f"- {r['tema']} | título: {r['titulo']} | resumo: {util.limitar(r['resumo'], 200)}"
                          for r in registros)
        esquema = {
            "type": "object",
            "properties": {"duplicado": {"type": "boolean"}, "parecido_com": {"type": "string"},
                           "justificativa": {"type": "string"}},
            "required": ["duplicado", "parecido_com", "justificativa"],
            "additionalProperties": False,
        }
        try:
            return llm.gerar_json(
                "Você verifica se um novo tema de vídeo repete o ASSUNTO CENTRAL de vídeos já produzidos "
                "por um canal do YouTube. Ângulos levemente diferentes do mesmo assunto contam como repetição; "
                "assuntos apenas da mesma categoria não contam.",
                f"Novo tema: {tema}\nContexto extra: {util.limitar(texto_extra, 500)}\n\n"
                f"Vídeos já produzidos:\n{lista}\n\nResponda em português.",
                esquema, max_tokens=4000)
        except Exception as exc:
            log.aviso(f"Verificação semântica indisponível ({exc}); usando só a comparação por palavras-chave.")
            return None
