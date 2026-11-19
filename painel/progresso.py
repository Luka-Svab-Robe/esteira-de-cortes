"""Andamento das lives para a tela: % real (0–100) com rótulo simples e erros em português claro, sem log bruto."""
from __future__ import annotations

import re
from pathlib import Path

from esteira.projeto import Projeto

ETAPAS_CORTES = ["ingestao", "analise", "edicao", "publicacao"]
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PCT_YTDLP = re.compile(r"\[download\]\s+([\d.]+)%")
LER_BYTES_FINAIS = 12000
ERROS_CONHECIDOS = (
    (r"403|Forbidden", "O YouTube recusou o download agora. Tente de novo em alguns minutos."),
    (r"No space left|sem espaço", "Acabou o espaço no disco do computador."),
    (r"ainda está no ar|is_live|transmissão contínua", "A live ainda está no ar. Espere ela terminar para cortar."),
    (r"quotaExceeded|cota", "A cota diária do YouTube acabou; amanhã a equipe continua."),
    (r"invalid_grant|não conectado|token (has been )?(expired|revoked)", "A conexão com o YouTube expirou: reconecte o canal."),
    (r"não foi baixad|fonte\.\*", "A live não foi baixada. Toque em “Tentar de novo”."),
    (r"Network|Connection|timed out|Temporary failure|getaddrinfo|Sem conexão", "Sem conexão com a internet."),
    (r"Duração inválida[^\n]*", None),  # a própria mensagem já é clara
    (r"Nenhum corte válido|Escolha os momentos", "Nenhum momento escolhido: escolha os trechos e mande editar."),
)
MENSAGEM_GENERICA = "Algo deu errado nesta etapa. Toque em “Tentar de novo”."


def _linhas_finais(caminho: Path) -> list[str]:
    try:
        with open(caminho, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - LER_BYTES_FINAIS))
            return f.read().decode("utf-8", "replace").replace("\r", "\n").splitlines()
    except OSError:
        return []


def pct_download(caminho_log: str | Path | None) -> float | None:
    """Último % de download que o yt-dlp escreveu no log do processo."""
    if not caminho_log:
        return None
    for linha in reversed(_linhas_finais(Path(caminho_log))):
        m = _PCT_YTDLP.search(_ANSI.sub("", linha))
        if m:
            return min(100.0, float(m.group(1)))
    return None


def mensagem_de_erro(texto: str | None) -> str:
    """Traduz um erro técnico numa frase simples para o dono do canal."""
    texto = _ANSI.sub("", texto or "")
    for padrao, amigavel in ERROS_CONHECIDOS:
        m = re.search(padrao, texto, re.I)
        if m:
            return amigavel or m.group(0).strip()
    return MENSAGEM_GENERICA


def ultima_falha(caminho_log: str | Path | None) -> str:
    """Erro de um processo que terminou mal, já em linguagem simples."""
    linhas = [l for l in _linhas_finais(Path(caminho_log)) if l.strip()] if caminho_log else []
    return mensagem_de_erro("\n".join(linhas[-15:]))


def etapa_atual(p: Projeto) -> int:
    etapas = p.status.get("etapas", {}) or {}
    if p.estado == "agendado":
        return len(ETAPAS_CORTES)
    for i, nome in enumerate(ETAPAS_CORTES):
        if not (etapas.get(nome) or {}).get("concluida"):
            return i
    return len(ETAPAS_CORTES)


def do_projeto(p: Projeto, log_do_processo: str | None = None, _maximos: dict = {}) -> dict:
    """{pct, rotulo} do projeto. Durante o download, usa o % do yt-dlp dentro da faixa reservada a ele."""
    from esteira.cortes.orquestrador import PCT_DOWNLOAD, ROTULO_DOWNLOAD

    estado = p.estado
    if estado in ("pendente_revisao", "reprovado", "aprovado", "agendado"):
        return {"pct": 100, "rotulo": "Pronto"}
    gravado = p.status.get("progresso") or {}
    pct, rotulo = float(gravado.get("pct", 0)), gravado.get("rotulo") or "Começando"
    if estado == "em_producao" and rotulo == ROTULO_DOWNLOAD:
        baixado = pct_download(log_do_processo)
        if baixado is not None:
            inicio, fim = PCT_DOWNLOAD
            pct, rotulo = inicio + (fim - inicio) * baixado / 100, f"{ROTULO_DOWNLOAD} ({baixado:.0f}%)"
    if estado == "em_producao":  # nunca volta para trás na tela (vídeo e áudio baixam em duas passadas)
        marco, maior = _maximos.get(p.id, (None, 0.0))
        if marco == gravado.get("em"):
            pct = max(pct, maior)
        _maximos[p.id] = (gravado.get("em"), pct)
    if estado == "erro":
        rotulo = mensagem_de_erro(p.status.get("detalhe_estado"))
    elif estado == "aguardando_etapa":
        rotulo = p.status.get("detalhe_estado") or "Esperando você decidir a próxima etapa"
    elif estado == "aguardando_roteiro":
        rotulo = "Esperando a IA escolher os momentos"
    return {"pct": int(round(pct)), "rotulo": rotulo}


def do_processo(log: str | None, rodando: bool, codigo: int | None) -> dict:
    """{pct, rotulo} de um processo que ainda não criou o projeto (os primeiros segundos)."""
    if rodando:
        baixado = pct_download(log)
        if baixado is not None:
            from esteira.cortes.orquestrador import PCT_DOWNLOAD

            return {"pct": int(PCT_DOWNLOAD[0] + (PCT_DOWNLOAD[1] - PCT_DOWNLOAD[0]) * baixado / 100),
                    "rotulo": f"Baixando a live ({baixado:.0f}%)"}
        return {"pct": 2, "rotulo": "Lendo a live"}
    if codigo == 0:
        return {"pct": 100, "rotulo": "Pronto"}
    if codigo == 4:
        return {"pct": 50, "rotulo": "Esperando a IA escolher os momentos"}
    return {"pct": 0, "rotulo": ultima_falha(log)}
