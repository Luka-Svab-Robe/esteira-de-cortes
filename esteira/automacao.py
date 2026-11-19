"""Esteira automática: worker que consome a fila do canal e agendamento no sistema operacional.

Linux  -> timer do systemd --user (roda ao acordar/ligar se perdeu o horário); crontab como alternativa
macOS  -> LaunchAgent (launchd)
Windows-> Agendador de Tarefas (schtasks)
"""
from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import log, util
from .canal import Canal
from .config import PASTA_AUTOMACAO, PASTA_LOGS, RAIZ

MARCA = "esteira-canal-dark"


def _python() -> str:
    candidatos = [RAIZ / ".venv" / "bin" / "python", RAIZ / ".venv" / "Scripts" / "python.exe"]
    for c in candidatos:
        if c.exists():
            return str(c)
    return sys.executable


def _estado() -> dict:
    return util.ler_json(PASTA_AUTOMACAO / "estado.json", {}) or {}


def _salvar_estado(canal_id: str, **dados) -> None:
    estado = _estado()
    estado[canal_id] = {**estado.get(canal_id, {}), **dados, "atualizado_em": util.agora_iso()}
    util.salvar_json(PASTA_AUTOMACAO / "estado.json", estado)


def _script(canal_id: str) -> Path:
    PASTA_AUTOMACAO.mkdir(parents=True, exist_ok=True)
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    log_cron = PASTA_LOGS / f"worker_{canal_id}.agendado.log"
    if os.name == "nt":
        caminho = PASTA_AUTOMACAO / f"worker_{canal_id}.cmd"
        caminho.write_text(f'@echo off\r\ncd /d "{RAIZ}"\r\n"{_python()}" esteira.py worker --canal {canal_id} '
                           f'>> "{log_cron}" 2>&1\r\n', encoding="utf-8")
    else:
        caminho = PASTA_AUTOMACAO / f"worker_{canal_id}.sh"
        caminho.write_text(f'#!/bin/sh\ncd "{RAIZ}" && "{_python()}" esteira.py worker --canal {canal_id} '
                           f'>> "{log_cron}" 2>&1\n', encoding="utf-8")
        caminho.chmod(0o755)
    return caminho


def _hora(horario: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in horario.strip().split(":")[:2])
        assert 0 <= h < 24 and 0 <= m < 60
        return h, m
    except Exception as exc:
        raise ValueError(f"Horário inválido '{horario}'. Use HH:MM, por exemplo 03:00.") from exc


# ------------------------------------------------------------ Linux / cron
def _crontab_linhas() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def _crontab_gravar(linhas: list[str]) -> None:
    subprocess.run(["crontab", "-"], input="\n".join(linhas).rstrip() + "\n", text=True, check=True)


def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _ativar_linux(canal_id: str, h: int, m: int, script: Path, intervalo: int | None = None) -> str:
    etiqueta = f"# {MARCA}:{canal_id}"
    # Preferência: timer do systemd do usuário. Diferente do cron, ele executa assim que a máquina
    # volta da suspensão ou é ligada, se o horário foi perdido (Persistent=true). Cron é a alternativa.
    if _systemd_usuario_disponivel():
        if shutil.which("crontab"):  # remove um agendamento antigo via cron, se houver
            linhas = _crontab_linhas()
            novas = [l for l in linhas if not l.endswith(etiqueta)]
            if len(novas) != len(linhas):
                _crontab_gravar(novas)
        pasta = _systemd_dir()
        pasta.mkdir(parents=True, exist_ok=True)
        nome = f"esteira-{canal_id}"
        (pasta / f"{nome}.service").write_text(
            f"[Unit]\nDescription=Esteira Canal Dark - worker {canal_id}\n\n[Service]\nType=oneshot\n"
            f'ExecStart="{script}"\n', encoding="utf-8")  # aspas: a pasta "Esteira de Cortes" tem espaços
        (pasta / f"{nome}.timer").write_text(
            f"[Unit]\nDescription=Esteira Canal Dark - agenda {canal_id}\n\n[Timer]\n"
            f"OnCalendar={_calendario_systemd(h, m, intervalo)}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n",
            encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{nome}.timer"], check=True)
        return "systemd"
    if shutil.which("crontab"):
        linhas = [l for l in _crontab_linhas() if not l.endswith(etiqueta)]
        linhas.append(f'{_calendario_cron(h, m, intervalo)} "{script}" {etiqueta}')
        _crontab_gravar(linhas)
        return "cron"
    raise RuntimeError("Nem 'systemctl --user' nem 'crontab' estão disponíveis para agendar a esteira.")


def _calendario_systemd(h: int, m: int, intervalo: int | None) -> str:
    if not intervalo:
        return f"*-*-* {h:02d}:{m:02d}:00"
    return f"*:0/{intervalo}" if intervalo < 60 else f"*-*-* 0/{max(1, intervalo // 60)}:00:00"


def _calendario_cron(h: int, m: int, intervalo: int | None) -> str:
    if not intervalo:
        return f"{m} {h} * * *"
    return f"*/{intervalo} * * * *" if intervalo < 60 else f"0 */{max(1, intervalo // 60)} * * *"


def _systemd_usuario_disponivel() -> bool:
    if not shutil.which("systemctl"):
        return False
    return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0


def _pausar_linux(canal_id: str) -> None:
    etiqueta = f"# {MARCA}:{canal_id}"
    if shutil.which("crontab"):
        linhas = _crontab_linhas()
        novas = [l for l in linhas if not l.endswith(etiqueta)]
        if len(novas) != len(linhas):
            _crontab_gravar(novas)
    nome = f"esteira-{canal_id}"
    if (_systemd_dir() / f"{nome}.timer").exists():
        # só desliga o timer: o .service fica, para não matar um processamento que esteja rodando agora
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{nome}.timer"], check=False)
        (_systemd_dir() / f"{nome}.timer").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


# ------------------------------------------------------------- macOS / launchd
def _plist(canal_id: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.esteiracanaldark.{canal_id}.plist"


def _ativar_macos(canal_id: str, h: int, m: int, script: Path, intervalo: int | None = None) -> str:
    caminho = _plist(canal_id)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    log_arq = str(PASTA_LOGS / f"worker_{canal_id}.agendado.log")
    with open(caminho, "wb") as f:
        plistlib.dump({"Label": f"com.esteiracanaldark.{canal_id}", "ProgramArguments": ["/bin/sh", str(script)],
                       **({"StartInterval": intervalo * 60} if intervalo else {"StartCalendarInterval": {"Hour": h, "Minute": m}}),
                       "StandardOutPath": log_arq,
                       "StandardErrorPath": log_arq}, f)
    dominio = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", dominio, str(caminho)], capture_output=True)
    if subprocess.run(["launchctl", "bootstrap", dominio, str(caminho)], capture_output=True).returncode != 0:
        subprocess.run(["launchctl", "load", "-w", str(caminho)], check=True)
    return "launchd"


def _pausar_macos(canal_id: str) -> None:
    caminho = _plist(canal_id)
    if caminho.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(caminho)], capture_output=True)
        subprocess.run(["launchctl", "unload", "-w", str(caminho)], capture_output=True)
        caminho.unlink(missing_ok=True)


# ----------------------------------------------------------- Windows / schtasks
def _tarefa(canal_id: str) -> str:
    return f"EsteiraCanalDark\\{canal_id}"


def _ativar_windows(canal_id: str, h: int, m: int, script: Path, intervalo: int | None = None) -> str:
    agenda = ["/SC", "MINUTE", "/MO", str(intervalo)] if intervalo else ["/SC", "DAILY", "/ST", f"{h:02d}:{m:02d}"]
    subprocess.run(["schtasks", "/Create", "/F", *agenda, "/TN", _tarefa(canal_id),
                    "/TR", f'"{script}"'], check=True, capture_output=True)
    return "schtasks"


def _pausar_windows(canal_id: str) -> None:
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", _tarefa(canal_id)], capture_output=True)


# ------------------------------------------------------------------ API
def ativar(canal_id: str, horario: str | None = None) -> str:
    canal = Canal.carregar(canal_id)
    horario = horario or canal.automacao.get("horario_execucao", "03:00")
    h, m = _hora(horario)
    script = _script(canal_id)
    # canal de cortes: o olheiro roda de tempos em tempos procurando lives novas
    intervalo = int(canal.automacao.get("intervalo_minutos", 60)) if canal.editorial.get("tipo_canal") == "cortes" else None
    sistema = platform.system()
    if sistema == "Windows":
        metodo = _ativar_windows(canal_id, h, m, script, intervalo)
    elif sistema == "Darwin":
        metodo = _ativar_macos(canal_id, h, m, script, intervalo)
    else:
        metodo = _ativar_linux(canal_id, h, m, script, intervalo)
    quando = f"a cada {intervalo} min" if intervalo else f"{h:02d}:{m:02d}"
    _salvar_estado(canal_id, ativo=True, horario=quando, metodo=metodo, script=str(script))
    if intervalo:
        return f"Olheiro de lives ATIVADO para '{canal_id}': verifica as fontes {quando} (via {metodo})."
    return f"Esteira automática ATIVADA para '{canal_id}': todos os dias às {h:02d}:{m:02d} (via {metodo})."


def pausar(canal_id: str) -> str:
    sistema = platform.system()
    if sistema == "Windows":
        _pausar_windows(canal_id)
    elif sistema == "Darwin":
        _pausar_macos(canal_id)
    else:
        _pausar_linux(canal_id)
    _salvar_estado(canal_id, ativo=False)
    return f"Esteira automática PAUSADA para '{canal_id}'. A fila continua salva."


def status() -> dict:
    estado = _estado()
    if platform.system() != "Linux":
        return estado
    linhas = _crontab_linhas() if shutil.which("crontab") else []
    for canal_id, dados in estado.items():
        if dados.get("metodo") == "cron":
            dados["confirmado_no_sistema"] = any(l.endswith(f"# {MARCA}:{canal_id}") for l in linhas)
        elif dados.get("metodo") == "systemd" and shutil.which("systemctl"):
            timer = f"esteira-{canal_id}.timer"
            ativo = subprocess.run(["systemctl", "--user", "is-active", timer], capture_output=True, text=True)
            dados["confirmado_no_sistema"] = ativo.stdout.strip() == "active"
            proximo = subprocess.run(["systemctl", "--user", "show", timer, "-p", "NextElapseUSecRealtime", "--value"],
                                     capture_output=True, text=True).stdout.strip()
            if proximo:
                dados["proxima_execucao"] = proximo
    return estado


# ------------------------------------------------------------------ worker
class Trava:
    def __init__(self, canal: Canal, validade_horas: float = 6):
        self.caminho = canal.pasta / ".worker.lock"
        self.validade = validade_horas * 3600

    def _ativa(self) -> bool:
        if not self.caminho.exists():
            return False
        if time.time() - self.caminho.stat().st_mtime > self.validade:
            return False
        try:
            pid = int(self.caminho.read_text().strip() or 0)
        except ValueError:
            return False
        if os.name != "nt" and pid:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
        return True

    def __enter__(self):
        if self._ativa():
            raise RuntimeError("Já existe um worker rodando para este canal.")
        self.caminho.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.caminho.unlink(missing_ok=True)


def executar_worker(canal_id: str, quantidade: int | None = None) -> list:
    from . import orquestrador
    from .modulos.m1_roteiro import TemaDuplicado
    from .modulos.m6_publicacao import ErroPublicacao, publicar
    from .projeto import Projeto

    canal = Canal.carregar(canal_id)
    log.anexar_arquivo(PASTA_LOGS / f"worker_{canal_id}.log")
    log.etapa(f"Worker do canal '{canal.nome}' — {util.agora_iso()}")
    produzidos = []
    with Trava(canal):
        for projeto in Projeto.listar(canal_id, ("aprovado", "erro_publicacao")):
            log.info(f"Tentando publicar novamente: {projeto.id}")
            try:
                publicar(projeto, canal)
            except ErroPublicacao as exc:
                log.aviso(f"Publicação de {projeto.id} ainda falhou: {exc}")
        revisao = bool(canal.automacao.get("revisao_humana_obrigatoria", True))
        pendentes = Projeto.listar(canal_id, ("pendente_revisao", "reprovado", "aguardando_roteiro"))
        limite = int(canal.automacao.get("max_pendentes_revisao", 3))
        if revisao and len(pendentes) >= limite:
            log.aviso(f"{len(pendentes)} vídeo(s) aguardando revisão (limite {limite}). Revise no painel para liberar a fila.")
            return produzidos
        if canal.editorial.get("tipo_canal") == "cortes":
            from .cortes.orquestrador import monitorar
            from .modulos import restricoes_youtube

            try:  # Short que o YouTube restringiu (direitos, idade, país) sai da agenda antes de ir ao ar
                restricoes_youtube.conferir(canal)
            except Exception as exc:
                log.aviso(f"Não consegui conferir as restrições no YouTube agora ({exc}).")

            projeto = monitorar(canal_id)  # olheiro: processa a live nova mais recente das fontes
            return [projeto] if projeto else []
        meta = quantidade or int(canal.automacao.get("videos_por_execucao", 1))
        tentativas = 0
        while len(produzidos) < meta and tentativas < 10:
            tentativas += 1
            fila = canal.fila_listar()
            if not fila:
                log.aviso("A fila de temas está vazia. Adicione temas em " + str(canal.caminho_fila))
                break
            item = fila[0]
            try:
                entrada = canal.ler_item_fila(item)
                projeto = orquestrador.produzir(canal_id, entrada["tema"], entrada["roteiro_bruto"], abrir_painel=False)
            except TemaDuplicado as exc:
                log.aviso(f"Tema pulado por duplicidade: {item} — {exc}")
                canal.fila_marcar(item, "duplicado")
                continue
            except Exception as exc:
                log.erro(f"Falha ao produzir '{item}': {exc}")
                canal.fila_marcar(item, f"erro: {str(exc)[:120]}")
                continue
            canal.fila_marcar(item, f"produzido -> {projeto.id}")
            produzidos.append(projeto)
            if not revisao and projeto.estado == "pendente_revisao":
                log.info("Revisão humana desativada neste canal: aprovando e publicando automaticamente.")
                orquestrador.aprovar(projeto)
    log.ok(f"Worker finalizado: {len(produzidos)} vídeo(s) produzido(s).")
    return produzidos
