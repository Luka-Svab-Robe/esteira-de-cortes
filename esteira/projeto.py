"""Pasta de projeto (um vídeo) e seu arquivo de controle de estado (status.json)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import util
from .config import PASTA_PROJETOS

ESTADOS = {
    "em_producao": "Em produção",
    "aguardando_roteiro": "Aguardando roteiro do agente de IA",
    "aguardando_etapa": "Aguardando a próxima etapa",
    "pendente_revisao": "Pendente de revisão humana",
    "reprovado": "Reprovado — aguardando correção",
    "aprovado": "Aprovado — aguardando envio",
    "publicando": "Enviando ao YouTube",
    "agendado": "Agendado no YouTube",
    "erro": "Erro na produção",
    "erro_publicacao": "Erro ao publicar",
    "descartado": "Descartado",
}

ETAPAS_PRODUCAO = ("roteiro", "narracao", "montagem", "shorts", "thumbnails")
SUBPASTAS = ("pesquisa", "audio", "legendas", "midia", "cenas", "render", "shorts", "thumbnails", "logs")


class ProjetoNaoEncontrado(LookupError):
    pass


class Projeto:
    def __init__(self, pasta: Path):
        self.pasta = Path(pasta).resolve()

    # ------------------------------------------------------------ identidade
    @property
    def id(self) -> str:
        return f"{self.pasta.parent.name}/{self.pasta.name}"

    @property
    def canal_id(self) -> str:
        return self.pasta.parent.name

    def p(self, *partes: str) -> Path:
        return self.pasta.joinpath(*partes)

    @property
    def video_final(self) -> Path:
        return self.p("video_final.mp4")

    # ------------------------------------------------------------- criação
    @classmethod
    def criar(cls, canal_id: str, tema: str) -> "Projeto":
        base = PASTA_PROJETOS / canal_id
        nome = f"{dt.date.today().isoformat()}_{util.slugify(tema, 50)}"
        pasta = base / nome
        sufixo = 2
        while pasta.exists():
            pasta = base / f"{nome}_{sufixo}"
            sufixo += 1
        for sub in SUBPASTAS:
            (pasta / sub).mkdir(parents=True, exist_ok=True)
        projeto = cls(pasta)
        projeto.salvar_status({
            "projeto": projeto.id, "canal": canal_id, "tema": tema, "estado": "em_producao",
            "criado_em": util.agora_iso(), "atualizado_em": util.agora_iso(), "etapas": {},
            "reprovacoes": [], "alertas": [], "youtube": {}, "eventos": [],
        })
        return projeto

    @classmethod
    def listar(cls, canal_id: str | None = None, estados: tuple[str, ...] | None = None) -> list["Projeto"]:
        if not PASTA_PROJETOS.exists():
            return []
        pastas_canal = [PASTA_PROJETOS / canal_id] if canal_id else [p for p in PASTA_PROJETOS.iterdir() if p.is_dir()]
        projetos = []
        for pasta_canal in pastas_canal:
            if not pasta_canal.exists():
                continue
            for pasta in pasta_canal.iterdir():
                if (pasta / "status.json").exists():
                    projeto = cls(pasta)
                    if estados is None or projeto.estado in estados:
                        projetos.append(projeto)
        projetos.sort(key=lambda pr: pr.status.get("criado_em", ""), reverse=True)
        return projetos

    @classmethod
    def localizar(cls, ref: str) -> "Projeto":
        """Aceita: caminho da pasta, 'canal/pasta', só 'pasta', parte do nome ou 'ultimo'."""
        ref = (ref or "").strip().strip("/")
        caminho = Path(ref).expanduser()
        if caminho.is_absolute() and (caminho / "status.json").exists():
            return cls(caminho)
        if (PASTA_PROJETOS / ref / "status.json").exists():
            return cls(PASTA_PROJETOS / ref)
        if (Path.cwd() / ref / "status.json").exists():
            return cls(Path.cwd() / ref)
        todos = cls.listar()
        if ref in ("ultimo", "último", "") and todos:
            return todos[0]
        exatos = [p for p in todos if p.pasta.name == ref]
        if len(exatos) == 1:
            return exatos[0]
        parciais = [p for p in todos if ref.lower() in p.id.lower()]
        if len(parciais) == 1:
            return parciais[0]
        if len(parciais) > 1:
            opcoes = "\n  ".join(p.id for p in parciais[:10])
            raise ProjetoNaoEncontrado(f"Mais de um projeto corresponde a '{ref}':\n  {opcoes}")
        raise ProjetoNaoEncontrado(f"Projeto '{ref}' não encontrado em {PASTA_PROJETOS}")

    # --------------------------------------------------------------- status
    @property
    def status(self) -> dict:
        return util.ler_json(self.p("status.json"), {}) or {}

    def salvar_status(self, status: dict) -> None:
        status["atualizado_em"] = util.agora_iso()
        util.salvar_json(self.p("status.json"), status)

    def atualizar_status(self, **campos) -> dict:
        status = self.status
        status.update(campos)
        self.salvar_status(status)
        return status

    def progresso(self, pct: float, rotulo: str) -> None:
        """Andamento em linguagem simples para a barra do painel (0 a 100)."""
        self.atualizar_status(progresso={"pct": round(max(0.0, min(100.0, pct)), 1), "rotulo": rotulo,
                                         "em": util.agora_iso()})

    @property
    def estado(self) -> str:
        return self.status.get("estado", "em_producao")

    def definir_estado(self, estado: str, detalhe: str = "") -> None:
        if estado not in ESTADOS:
            raise ValueError(f"Estado desconhecido: {estado}")
        status = self.status
        status["estado"] = estado
        status["detalhe_estado"] = detalhe
        self._evento(status, f"Estado -> {ESTADOS[estado]}" + (f" ({detalhe})" if detalhe else ""))
        self.salvar_status(status)

    def evento(self, mensagem: str) -> None:
        status = self.status
        self._evento(status, mensagem)
        self.salvar_status(status)

    @staticmethod
    def _evento(status: dict, mensagem: str) -> None:
        eventos = status.setdefault("eventos", [])
        eventos.append({"em": util.agora_iso(), "mensagem": mensagem})
        del eventos[:-200]

    def marcar_etapa(self, etapa: str, concluida: bool = True, **detalhes) -> None:
        status = self.status
        status.setdefault("etapas", {})[etapa] = {"concluida": concluida, "em": util.agora_iso(), **detalhes}
        self._evento(status, f"Etapa '{etapa}' {'concluída' if concluida else 'com falha'}")
        self.salvar_status(status)

    def etapa_concluida(self, etapa: str) -> bool:
        return bool(self.status.get("etapas", {}).get(etapa, {}).get("concluida"))

    def alertar(self, etapa: str, mensagem: str) -> None:
        status = self.status
        alertas = status.setdefault("alertas", [])
        if not any(a["etapa"] == etapa and a["mensagem"] == mensagem for a in alertas):
            alertas.append({"etapa": etapa, "mensagem": mensagem, "em": util.agora_iso()})
        self.salvar_status(status)

    def limpar_alertas(self, etapa: str) -> None:
        status = self.status
        status["alertas"] = [a for a in status.get("alertas", []) if a.get("etapa") != etapa]
        self.salvar_status(status)

    # ------------------------------------------------------------ revisões
    def reprovar(self, motivo: str, etapas: list[str] | None = None, blocos: list[int] | None = None) -> dict:
        from .revisao import interpretar_motivo

        sugestao = interpretar_motivo(motivo)
        registro = {
            "em": util.agora_iso(), "motivo": motivo.strip(), "resolvida": False,
            "etapas_afetadas": etapas or sugestao["etapas"], "blocos_afetados": blocos or sugestao["blocos"],
            "sugestao_agente": sugestao["instrucao"],
        }
        status = self.status
        status.setdefault("reprovacoes", []).append(registro)
        status["estado"] = "reprovado"
        status["detalhe_estado"] = motivo.strip()[:200]
        self._evento(status, f"Reprovado: {motivo.strip()[:200]}")
        self.salvar_status(status)
        return registro

    def reprovacoes_pendentes(self) -> list[dict]:
        return [r for r in self.status.get("reprovacoes", []) if not r.get("resolvida")]

    def resolver_reprovacoes(self, nota: str = "") -> None:
        status = self.status
        for r in status.get("reprovacoes", []):
            if not r.get("resolvida"):
                r["resolvida"] = True
                r["resolvida_em"] = util.agora_iso()
                if nota:
                    r["nota_correcao"] = nota
        self.salvar_status(status)

    # ------------------------------------------------------------- arquivos
    def ler(self, relativo: str, padrao=None):
        return util.ler_json(self.p(relativo), padrao)

    def salvar(self, relativo: str, dados) -> None:
        util.salvar_json(self.p(relativo), dados)

    def roteiro(self) -> dict:
        dados = self.ler("roteiro.json")
        if not dados:
            raise FileNotFoundError(f"O projeto {self.id} ainda não tem roteiro.json")
        return dados

    def salvar_roteiro(self, roteiro: dict) -> None:
        self.salvar("roteiro.json", roteiro)

    def linha_tempo(self) -> list[dict]:
        return self.ler("render/linha_tempo.json", []) or []

    def resumo(self) -> dict:
        status = self.status
        meta = self.ler("metadados.json", {}) or {}
        return {
            "id": self.id, "canal": self.canal_id, "pasta": self.pasta.name, "tema": status.get("tema", ""),
            "titulo": meta.get("titulo") or status.get("tema", ""), "estado": status.get("estado"),
            "estado_texto": ESTADOS.get(status.get("estado", ""), status.get("estado", "")),
            "criado_em": status.get("criado_em"), "atualizado_em": status.get("atualizado_em"),
            "duracao": meta.get("duracao_segundos"), "tem_video": self.video_final.exists(),
            "reprovacoes_pendentes": len(self.reprovacoes_pendentes()),
            "alertas": len(status.get("alertas", [])),
        }
