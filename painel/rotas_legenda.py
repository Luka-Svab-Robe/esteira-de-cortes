"""Legenda de cada Short: ver as frases com os tempos, corrigir o texto (ou os tempos) e refazer o vídeo."""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, HTTPException

from esteira.cortes.traducao import distribuir_palavras, frases_da_fala

router = APIRouter()

BLOQUEADOS = {"publicando", "agendado", "aprovado"}
MAX_FRASES, MAX_TEXTO = 400, 200
PALAVRAS_POR_FRASE = 7


def _srv():
    from painel import servidor  # import tardio: o servidor inclui este router

    return servidor


def _erro(status: int, mensagem: str):
    raise HTTPException(status, {"mensagem": mensagem})


def _short(p, indice: int) -> dict:
    s = next((x for x in (p.ler("shorts/shorts.json", []) or []) if int(x["indice"]) == int(indice)), None)
    if not s:
        _erro(404, "Short não encontrado.")
    return s


def _ocupado(projeto_id: str) -> bool:
    return any(e.get("projeto") == projeto_id and e["proc"].poll() is None for e in _srv()._envios.values())


def legenda_atual(p, s: dict) -> dict:
    """A legenda que está no vídeo: a salva na edição; em projetos antigos, a transcrição (traduzida, se houve)."""
    n = int(s["indice"])
    salva = p.ler(f"cortes/corte_{n}_legenda.json")
    if salva and salva.get("palavras") is not None:
        return salva
    traduzida = p.ler(f"cortes/corte_{n}_palavras_pt.json") if s.get("legenda_traduzida_de") else None
    return {"palavras": traduzida or p.ler(f"cortes/corte_{n}_palavras.json", []) or [], "editada": False,
            "traduzida_de": s.get("legenda_traduzida_de") if traduzida else None}


@router.get("/api/projetos/{canal}/{pasta}/shorts/{indice}/legenda")
def ver_legenda(canal: str, pasta: str, indice: int):
    srv = _srv()
    p = srv._projeto(canal, pasta)
    s = _short(p, indice)
    atual = legenda_atual(p, s)
    frases = [{"inicio": round(f["inicio"], 2), "fim": round(f["fim"], 2), "texto": f["texto"]}
              for f in frases_da_fala(atual["palavras"], max_palavras=PALAVRAS_POR_FRASE)]
    return {"indice": int(s["indice"]), "video": srv._url(p, s.get("arquivo")), "duracao": s.get("duracao"),
            "frases": frases, "editada": bool(atual.get("editada")), "traduzida_de": atual.get("traduzida_de"),
            "editavel": p.estado not in BLOQUEADOS and not _ocupado(p.id)}


def _validar_frases(bruto, duracao: float) -> list[dict]:
    if not isinstance(bruto, list) or len(bruto) > MAX_FRASES:
        _erro(400, "Legenda inválida.")
    frases = []
    for n, f in enumerate(bruto, 1):
        if not isinstance(f, dict):
            _erro(400, "Legenda inválida.")
        texto = re.sub(r"\s+", " ", str(f.get("texto", ""))).strip()[:MAX_TEXTO]
        if not texto:
            continue  # frase apagada = some da tela
        try:
            inicio, fim = float(f["inicio"]), float(f["fim"])
        except (KeyError, TypeError, ValueError):
            _erro(400, f"Linha {n}: tempo inválido.")
        if inicio < 0 or fim <= inicio or fim > duracao + 0.5:
            _erro(400, f"Linha {n}: o tempo precisa ficar entre 0 e {duracao:.1f} s, com o fim depois do começo.")
        frases.append({"inicio": inicio, "fim": fim, "texto": texto})
    frases.sort(key=lambda f: f["inicio"])
    for anterior, atual in zip(frases, frases[1:]):  # linhas encostadas não podem se sobrepor
        if atual["inicio"] < anterior["fim"]:
            atual["inicio"] = anterior["fim"]
            if atual["fim"] - atual["inicio"] < 0.2:
                _erro(400, f"A linha “{atual['texto'][:30]}” ficou sem tempo: ajuste o começo e o fim dela.")
    return frases


@router.put("/api/projetos/{canal}/{pasta}/shorts/{indice}/legenda")
def salvar_legenda(canal: str, pasta: str, indice: int, corpo: dict = Body(...)):
    """Grava a legenda corrigida (ou volta à automática) e refaz só este Short."""
    srv = _srv()
    p = srv._projeto(canal, pasta)
    srv._canal_cortes(canal)
    if p.estado in BLOQUEADOS:
        _erro(409, "Este Short já foi para o YouTube: cancele o agendamento antes de mudar a legenda.")
    if _ocupado(p.id):
        _erro(409, "A equipe ainda está trabalhando nesta live; espere terminar.")
    s = _short(p, indice)
    cortes = p.ler("analise/cortes.json", []) or []
    corte = next((c for c in cortes if int(c.get("indice", 0)) == int(indice)), None)
    if not corte:
        _erro(404, "Momento deste Short não encontrado.")
    automatica = bool(corpo.get("automatica"))
    if not automatica:
        frases = _validar_frases(corpo.get("frases"), float(s.get("duracao") or 0))
        if not frases:
            _erro(400, "A legenda ficou vazia. Para tirar a legenda inteira, apague o Short ou deixe ao menos uma linha.")
        atual = legenda_atual(p, s)
        p.salvar(f"cortes/corte_{int(indice)}_legenda_editada.json", {
            "palavras": [w for f in frases for w in distribuir_palavras(f["inicio"], f["fim"], f["texto"])],
            "inicio": corte["inicio"], "fim": corte["fim"], "idioma": atual.get("idioma") or s.get("idioma_fala"),
            "traduzida_de": atual.get("traduzida_de")})
    corte["legenda_editada"] = not automatica
    p.salvar("analise/cortes.json", cortes)
    live = p.status.get("live", {}) or {}
    envio = srv._iniciar_processo(canal, ["cortes", "reeditar", "--projeto", p.id, "--indice", str(int(indice))],
                                  live.get("url", ""), titulo=f"Refazendo a legenda do Short {indice}",
                                  projeto=p.id, indice=int(indice))
    return {"ok": True, "envio": envio["id"], "automatica": automatica}
