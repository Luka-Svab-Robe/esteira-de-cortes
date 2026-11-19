"""Perfis de canal: identidade visual/editorial, fila de temas e criação de canais."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import util
from .config import PASTA_CANAIS

ESTILOS = ("explicativo", "documentario", "animacao_2d", "tipografia")
POSICOES_MARCA = ("superior_esquerdo", "superior_direito", "inferior_esquerdo", "inferior_direito")

PALETAS = {
    "sombria_vermelha": {"fundo": "#0b0d12", "primaria": "#e63946", "secundaria": "#f1c453", "texto": "#f5f5f5", "texto_suave": "#9aa3b2"},
    "neon_azul": {"fundo": "#070b1a", "primaria": "#3a86ff", "secundaria": "#00f5d4", "texto": "#f0f4ff", "texto_suave": "#8d99ae"},
    "dourada": {"fundo": "#0f0d0a", "primaria": "#d4a017", "secundaria": "#f4e1a1", "texto": "#fdf8ee", "texto_suave": "#a89f8c"},
    "verde_terminal": {"fundo": "#050a06", "primaria": "#39ff14", "secundaria": "#b8ffb0", "texto": "#e8ffe8", "texto_suave": "#6b8f6b"},
    "roxo_mistico": {"fundo": "#0d0716", "primaria": "#9d4edd", "secundaria": "#ff9e00", "texto": "#f6f0ff", "texto_suave": "#a393b8"},
    "noir": {"fundo": "#0a0a0a", "primaria": "#f2f2f2", "secundaria": "#c1121f", "texto": "#f2f2f2", "texto_suave": "#8a8a8a"},
    "oceano": {"fundo": "#04141c", "primaria": "#00b4d8", "secundaria": "#ffd166", "texto": "#eefbff", "texto_suave": "#86a8b5"},
    "laranja_energia": {"fundo": "#120a04", "primaria": "#ff6b00", "secundaria": "#ffd60a", "texto": "#fff7ec", "texto_suave": "#b09a85"},
}

# Palavras em linguagem natural -> paleta pré-definida
_CORES_NATURAIS = [
    (("vermelh", "sangue", "terror", "sombri"), "sombria_vermelha"),
    (("azul", "neon", "tecnolog", "cyber"), "neon_azul"),
    (("dourad", "ouro", "amarel", "luxo", "riqueza"), "dourada"),
    (("verde", "terminal", "hacker", "matrix"), "verde_terminal"),
    (("roxo", "violeta", "mistic", "lilas"), "roxo_mistico"),
    (("preto e branco", "noir", "cinza", "monocrom"), "noir"),
    (("ciano", "turquesa", "oceano", "mar"), "oceano"),
    (("laranja", "energia", "fogo"), "laranja_energia"),
]

RITMOS = {
    "lento": {"bloco_min_seg": 7, "bloco_max_seg": 12, "transicao": "dissolve", "transicao_seg": 0.8,
              "animacao_seg": 3.5, "movimento_camera": "suave"},
    "medio": {"bloco_min_seg": 5, "bloco_max_seg": 10, "transicao": "fade", "transicao_seg": 0.5,
              "animacao_seg": 3.0, "movimento_camera": "suave"},
    "rapido": {"bloco_min_seg": 5, "bloco_max_seg": 7, "transicao": "smoothleft", "transicao_seg": 0.35,
               "animacao_seg": 2.2, "movimento_camera": "dinamico"},
}

PADRAO: dict = {
    "canal": {"id": "", "nome": "", "nicho": "", "publico": "público geral adulto", "idioma": "pt-BR", "handle": ""},
    "editorial": {"tom_narracao": "envolvente e claro", "duracao_alvo_min": 8, "estilo_visual": "documentario",
                  "chamada_final": "Se inscreva no canal para não perder o próximo vídeo.", "categoria_youtube": 27,
                  "evitar": [], "tipo_canal": "dark"},
    # canais do tipo "cortes": Shorts a partir de lives de fontes autorizadas
    "cortes": {"max_cortes_por_live": 5, "duracao_min": 20, "duracao_max": 58, "enquadramento": "auto",
               "musica_volume_db": -20, "gravar_ao_vivo": False,
               "max_horas_ao_vivo": 6, "layout": "moldura", "modelo": "padrao", "cor_fundo": "#000000", "brilho_fundo": False, "foco_foto": "45% 30%",
               "traduzir": True, "idioma_legenda": "pt",
               # Twitch/Kick e acabamento profissional (ver esteira/cortes/camera.py e efeitos.py)
               "layout_twitch_kick": "auto", "hd_por_trecho": True, "altura_analise": 480, "altura_hd": 1080,
               "zoom_reacoes": True, "realce": True, "tratar_voz": True,
               "hashtags_fixas": [], "hashtags_titulo": ["shorts"], "tags_fixas": [], "convite_link": ""},
    "identidade_visual": {
        "paleta": dict(PALETAS["sombria_vermelha"]),
        "tipografia": {"titulos": "Anton", "corpo": "Poppins"},
        "marca_dagua": {"arquivo": "assets/logo.png", "posicao": "inferior_direito", "opacidade": 0.8,
                        "escala": 0.08, "margem": 40},
        "filtro_cor": "nenhum",
        "destaque_posicao": "inferior",
    },
    "voz": {"motor": "auto", "kokoro_voz": "pm_alex", "edge_voz": "pt-BR-AntonioNeural", "velocidade": 1.0,
            "pronuncias": {}},
    "ritmo": {"bloco_min_seg": 5, "bloco_max_seg": 12, "transicao": "fade", "transicao_seg": 0.5,
              "animacao_seg": 3.0, "movimento_camera": "suave", "pausa_inicio_seg": 0.15, "pausa_fim_seg": 0.3},
    "musica": {"pasta": "trilhas", "volume_db": -20, "ducking": True, "gerar_ambiente_se_vazio": True},
    "shorts": {"max_shorts": 3, "duracao_min": 30, "duracao_max": 58, "legenda_altura": 0.66, "enquadramento": "4:3"},
    "publicacao": {"fuso_horario": "America/Sao_Paulo", "horarios_video": ["18:00"],
                   "horarios_shorts": ["12:00", "15:00", "21:00"],
                   "dias_semana": ["seg", "ter", "qua", "qui", "sex", "sab", "dom"],
                   "antecedencia_min_horas": 3, "intervalo_min_minutos": 60, "publicar_shorts": True,
                   "enviar_legendas": True, "declarar_conteudo_sintetico": True, "feito_para_criancas": False,
                   "licenca": "youtube"},
    "automacao": {"videos_por_execucao": 1, "revisao_humana_obrigatoria": True, "horario_execucao": "03:00",
                  "max_pendentes_revisao": 3, "intervalo_minutos": 60},
}


class CanalNaoEncontrado(LookupError):
    pass


_CACHE_CFG: dict[str, tuple] = {}  # canal -> ((id, mtime, tamanho), cfg já mesclada)


@dataclass
class Canal:
    id: str
    pasta: Path
    cfg: dict

    # ------------------------------------------------------------------ carga
    @classmethod
    def carregar(cls, canal_id: str) -> "Canal":
        pasta = PASTA_CANAIS / canal_id
        arquivo = pasta / "identidade.yaml"
        if not arquivo.exists():
            disponiveis = ", ".join(cls.listar()) or "nenhum"
            raise CanalNaoEncontrado(f"Canal '{canal_id}' não existe. Canais disponíveis: {disponiveis}")
        marca = arquivo.stat()
        chave = (canal_id, marca.st_mtime_ns, marca.st_size)
        if _CACHE_CFG.get(canal_id, (None,))[0] != chave:  # só relê o YAML quando o arquivo muda
            dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
            cfg = util.mesclar(copy.deepcopy(PADRAO), dados)
            cfg["canal"]["id"] = canal_id
            if cfg["editorial"]["estilo_visual"] not in ESTILOS:
                raise ValueError(f"estilo_visual inválido em {arquivo}: use um de {', '.join(ESTILOS)}")
            _CACHE_CFG[canal_id] = (chave, cfg)
        return cls(canal_id, pasta, copy.deepcopy(_CACHE_CFG[canal_id][1]))

    @staticmethod
    def listar(incluir_ocultos: bool = False) -> list[str]:
        if not PASTA_CANAIS.exists():
            return []
        return sorted(p.name for p in PASTA_CANAIS.iterdir()
                      if (p / "identidade.yaml").exists() and (incluir_ocultos or not p.name.startswith("_")))

    # ------------------------------------------------------------- atalhos
    @property
    def nome(self) -> str:
        return self.cfg["canal"].get("nome") or self.id

    @property
    def nicho(self) -> str:
        return self.cfg["canal"].get("nicho", "")

    @property
    def handle(self) -> str:
        return self.cfg["canal"].get("handle", "")

    @property
    def estilo(self) -> str:
        return self.cfg["editorial"]["estilo_visual"]

    @property
    def editorial(self) -> dict:
        return self.cfg["editorial"]

    @property
    def paleta(self) -> dict:
        return self.cfg["identidade_visual"]["paleta"]

    @property
    def tipografia(self) -> dict:
        return self.cfg["identidade_visual"]["tipografia"]

    @property
    def marca(self) -> dict:
        return self.cfg["identidade_visual"]["marca_dagua"]

    @property
    def voz(self) -> dict:
        return self.cfg["voz"]

    @property
    def ritmo(self) -> dict:
        return self.cfg["ritmo"]

    @property
    def musica(self) -> dict:
        return self.cfg["musica"]

    @property
    def shorts(self) -> dict:
        return self.cfg["shorts"]

    @property
    def publicacao(self) -> dict:
        return self.cfg["publicacao"]

    @property
    def automacao(self) -> dict:
        return self.cfg["automacao"]

    @property
    def palavras_por_segundo(self) -> float:
        return 2.5 * float(self.voz.get("velocidade", 1.0) or 1.0)

    # ------------------------------------------------------------- caminhos
    @property
    def caminho_db(self) -> Path:
        return self.pasta / "historico.db"

    @property
    def caminho_fila(self) -> Path:
        return self.pasta / "fila.txt"

    @property
    def caminho_processados(self) -> Path:
        return self.pasta / "fila_processados.txt"

    @property
    def pasta_trilhas(self) -> Path:
        return self.pasta / self.musica.get("pasta", "trilhas")

    @property
    def pasta_credenciais(self) -> Path:
        return self.pasta / "credenciais"

    @property
    def caminho_token_youtube(self) -> Path:
        return self.pasta_credenciais / "token_youtube.json"

    def caminho_logo(self) -> Path | None:
        arquivo = self.pasta / self.marca.get("arquivo", "assets/logo.png")
        if arquivo.exists():
            return arquivo
        automatico = self.pasta / "assets" / "logo_auto.png"
        return automatico if automatico.exists() else None

    # ---------------------------------------------------------------- fila
    def _linhas_fila(self) -> list[str]:
        if not self.caminho_fila.exists():
            return []
        return self.caminho_fila.read_text(encoding="utf-8").splitlines()

    def fila_listar(self) -> list[str]:
        return [l.strip() for l in self._linhas_fila() if l.strip() and not l.strip().startswith("#")]

    def fila_adicionar(self, tema: str, no_inicio: bool = False) -> None:
        linhas = self._linhas_fila()
        if no_inicio:
            indice = next((i for i, l in enumerate(linhas) if l.strip() and not l.strip().startswith("#")), len(linhas))
            linhas.insert(indice, tema.strip())
        else:
            linhas.append(tema.strip())
        util.salvar_texto(self.caminho_fila, "\n".join(linhas).rstrip() + "\n")

    def fila_remover(self, item: str) -> bool:
        linhas = self._linhas_fila()
        for i, linha in enumerate(linhas):
            if linha.strip() == item.strip():
                del linhas[i]
                util.salvar_texto(self.caminho_fila, "\n".join(linhas).rstrip() + "\n")
                return True
        return False

    def fila_marcar(self, item: str, resultado: str) -> None:
        self.fila_remover(item)
        with open(self.caminho_processados, "a", encoding="utf-8") as f:
            f.write(f"{util.agora_iso()} | {resultado} | {item}\n")

    def ler_item_fila(self, item: str) -> dict:
        """Interpreta uma linha da fila: tema livre ou 'roteiro: arquivo.txt | tema opcional'."""
        if item.lower().startswith("roteiro:"):
            resto = item.split(":", 1)[1].strip()
            arquivo, _, tema = resto.partition("|")
            caminho = self.pasta / "roteiros" / arquivo.strip()
            if not caminho.exists():
                raise FileNotFoundError(f"Roteiro da fila não encontrado: {caminho}")
            return {"tema": tema.strip() or None, "roteiro_bruto": caminho.read_text(encoding="utf-8")}
        return {"tema": item, "roteiro_bruto": None}


# --------------------------------------------------------------------------
# Criação de canais
# --------------------------------------------------------------------------
MODELO_IDENTIDADE = """\
# =====================================================================
#  Identidade do canal "{nome}"
#  Edite à vontade: tudo aqui controla roteiro, voz, visual e publicação.
# =====================================================================
canal:
  nome: {nome_j}
  nicho: {nicho_j}
  publico: {publico_j}
  idioma: pt-BR
  handle: {handle_j}

editorial:
  tom_narracao: {tom_j}
  duracao_alvo_min: {duracao}          # duração aproximada do vídeo longo
  estilo_visual: {estilo}              # explicativo | documentario | animacao_2d | tipografia
  tipo_canal: {tipo_canal}             # dark (vídeos criados do zero) | cortes (Shorts a partir de lives)
  chamada_final: {cta_j}
  categoria_youtube: {categoria}       # 20 = Jogos, 24 = Entretenimento, 27 = Educação, 28 = Ciência e Tecnologia
  evitar: []                           # assuntos/abordagens proibidos neste canal

identidade_visual:
  paleta:
    fundo: "{fundo}"
    primaria: "{primaria}"
    secundaria: "{secundaria}"
    texto: "{texto}"
    texto_suave: "{texto_suave}"
  tipografia:
    titulos: "{fonte_titulos}"         # Anton | Bebas Neue | Archivo Black | DM Serif Display | Special Elite
    corpo: "Poppins"
  marca_dagua:
    arquivo: assets/logo.png           # se não existir, um logo é gerado automaticamente
    posicao: inferior_direito          # superior_esquerdo | superior_direito | inferior_esquerdo | inferior_direito
    opacidade: 0.8
    escala: 0.08                       # fração da largura do vídeo
    margem: 40
  filtro_cor: {filtro}                 # nenhum | cinematico | quente | frio | noir
  destaque_posicao: inferior           # inferior | superior | centro

voz:
  motor: auto                          # auto (Kokoro local -> Edge-TTS) | kokoro | edge
  kokoro_voz: {kokoro_voz}             # pm_alex | pm_santa | pf_dora
  edge_voz: {edge_voz}                 # pt-BR-AntonioNeural | pt-BR-FranciscaNeural | pt-BR-ThalitaMultilingualNeural
  velocidade: {velocidade}
  pronuncias: {{}}                     # ex.: {{"FBI": "éfe bê í", "Nietzsche": "Nítche"}}

ritmo:
  bloco_min_seg: {bloco_min}
  bloco_max_seg: {bloco_max}
  transicao: {transicao}               # fade | dissolve | fadeblack | smoothleft | slideleft | circleopen | corte
  transicao_seg: {transicao_seg}
  animacao_seg: {animacao_seg}         # segundos animados dos cartões antes de congelar o quadro
  movimento_camera: {movimento}        # suave | dinamico
  pausa_inicio_seg: 0.15
  pausa_fim_seg: 0.3

musica:
  pasta: trilhas                       # coloque aqui MP3/WAV livres de direitos
  volume_db: -20
  ducking: true                        # abaixa a música durante a fala
  gerar_ambiente_se_vazio: true

shorts:
  max_shorts: 3
  duracao_min: 30
  duracao_max: 58
  legenda_altura: 0.66                 # posição vertical da legenda (0 = topo, 1 = base)
  enquadramento: "4:3"                 # 4:3 (recorte leve) | 16:9 (vídeo inteiro)

publicacao:
  fuso_horario: America/Sao_Paulo
  horarios_video: [{horarios_video}]
  horarios_shorts: [{horarios_shorts}]
  dias_semana: [seg, ter, qua, qui, sex, sab, dom]
  antecedencia_min_horas: 3
  intervalo_min_minutos: 60
  publicar_shorts: true
  enviar_legendas: true
  declarar_conteudo_sintetico: true
  feito_para_criancas: false
  licenca: youtube

automacao:
  videos_por_execucao: 1
  revisao_humana_obrigatoria: true     # false = publica sem passar pelo painel
  horario_execucao: "03:00"
  max_pendentes_revisao: 3
  intervalo_minutos: 60                # canais de cortes: de quanto em quanto tempo procurar lives novas

cortes:                                # usado quando tipo_canal: cortes
  max_cortes_por_live: 5
  duracao_min: 20
  duracao_max: 58
  enquadramento: auto                  # auto (segue o rosto; sem rosto = fundo desfocado) | rosto | centro | desfoque
  musica_volume_db: -20                # volume da trilha sob a fala/jogo; -99 desliga a música
  gravar_ao_vivo: false                # true = grava enquanto a live acontece; false = espera a live terminar
  max_horas_ao_vivo: 6                 # limite da gravação ao vivo (transmissões 24 h são sempre recusadas)
  layout: moldura                      # moldura = modelo do canal (foto + @ no topo, vídeo inteiro no centro) | vertical = 9:16 no rosto
  modelo: padrao                       # posição da foto e do @: padrao (esquerda) | direita | centro (topo, centralizada)
  cor_fundo: "#000000"                 # fundo da moldura (preto)
  brilho_fundo: false                  # true = brilho suave nas cores do canal atrás do topo
  foco_foto: "45% 30%"                 # parte da foto do canal que aparece no círculo (horizontal vertical)
  traduzir: true                       # fala em outra língua vira legenda (e gancho/título) em português
  idioma_legenda: pt
  layout_twitch_kick: auto             # lives da Twitch/Kick: auto (acha a webcam) | dividido | moldura | vertical
  hd_por_trecho: true                  # analisa uma cópia leve da live e baixa só os trechos escolhidos em HD
  zoom_reacoes: true                   # zoom seco de 1 s nos picos (grito, risada, explosão)
  realce: true                         # contraste, cor e nitidez leves (a live chega comprimida)
  tratar_voz: true                     # tira o ronco grave e comprime a voz antes de normalizar o volume
  hashtags_fixas: []                   # ex.: [MeuJogo, Gameplay, Clips] — as mesmas em todos os Shorts do canal
  hashtags_titulo: [shorts]            # vão no fim do título de cada Short
  tags_fixas: []                       # palavras-chave somadas às de cada corte
  convite_link: ""                     # link que vai em todos os Shorts (ex.: seu curso/programa de cortes)
  convite_texto: "✂️ Quer fazer cortes como estes e postar no seu canal também? Comece por aqui: {{link}}"
"""


def resolver_paleta(descricao: str) -> dict:
    """Aceita nome de paleta, lista de cores hex ou descrição em linguagem natural."""
    descricao = (descricao or "").strip()
    if descricao in PALETAS:
        return dict(PALETAS[descricao])
    cores = re.findall(r"#[0-9a-fA-F]{6}\b", descricao)
    if len(cores) >= 3:
        base = dict(PALETAS["sombria_vermelha"])
        nomes = ["fundo", "primaria", "secundaria", "texto", "texto_suave"]
        for nome, cor in zip(nomes, cores):
            base[nome] = cor.lower()
        return base
    normal = util.sem_acentos(descricao.lower())
    for chaves, nome in _CORES_NATURAIS:
        if any(c in normal for c in chaves):
            return dict(PALETAS[nome])
    return dict(PALETAS["sombria_vermelha"])


def inferir_estilo(nicho: str, ritmo: str) -> str:
    n = util.sem_acentos(nicho.lower())
    if any(p in n for p in ("motiva", "frase", "reflex", "filosof", "estoic", "citac")):
        return "tipografia"
    if any(p in n for p in ("infantil", "crianc", "fabula", "conto", "lenda", "anima")):
        return "animacao_2d"
    if any(p in n for p in ("ciencia", "financ", "dinheiro", "tecnolog", "econom", "explica", "dados",
                            "saude", "psicolog", "curiosidade")):
        return "explicativo"
    return "documentario"


def criar_canal(canal_id: str, nome: str, nicho: str, tom: str, paleta: str, ritmo: str,
                estilo: str | None = None, handle: str = "", publico: str = "", duracao_min: int = 8,
                tipo: str = "dark") -> Canal:
    if tipo not in ("dark", "cortes"):
        raise ValueError("Tipo de canal inválido: use dark ou cortes")
    oculto = (canal_id or "").startswith("_")  # canais com "_" não aparecem na listagem (ex.: testes)
    canal_id = ("_" if oculto else "") + util.slugify(canal_id or nome, 40).replace("-", "_")
    pasta = PASTA_CANAIS / canal_id
    if (pasta / "identidade.yaml").exists():
        raise FileExistsError(f"O canal '{canal_id}' já existe em {pasta}")
    ritmo_norm = util.sem_acentos((ritmo or "medio").lower())
    chave_ritmo = "rapido" if "rapid" in ritmo_norm or "dinam" in ritmo_norm else (
        "lento" if "lent" in ritmo_norm or "calm" in ritmo_norm or "contempl" in ritmo_norm else "medio")
    cfg_ritmo = RITMOS[chave_ritmo]
    estilo = estilo if estilo in ESTILOS else inferir_estilo(nicho, chave_ritmo)
    cores = resolver_paleta(paleta)
    tom_norm = util.sem_acentos(tom.lower())
    feminina = any(p in tom_norm for p in ("feminin", "mulher", "narradora"))
    filtro = "cinematico" if estilo == "documentario" else "nenhum"
    fonte_titulos = {"documentario": "Anton", "explicativo": "Archivo Black", "animacao_2d": "Bebas Neue",
                     "tipografia": "Bebas Neue"}[estilo]
    if "elegant" in tom_norm or "sofistic" in tom_norm:
        fonte_titulos = "DM Serif Display"
    velocidade = 1.08 if chave_ritmo == "rapido" else (0.95 if chave_ritmo == "lento" else 1.0)
    j = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731 — string YAML segura
    texto = MODELO_IDENTIDADE.format(
        nome=nome, nome_j=j(nome), nicho_j=j(nicho), publico_j=j(publico or "público geral adulto"),
        handle_j=j(handle), tom_j=j(tom), duracao=int(duracao_min), estilo=estilo, tipo_canal=tipo,
        cta_j=j("Se inscreva e ative o sininho para não perder o próximo vídeo."),
        categoria=24 if tipo == "cortes" else (28 if estilo == "explicativo" else 27),
        fonte_titulos=fonte_titulos, filtro=filtro,
        kokoro_voz="pf_dora" if feminina else "pm_alex",
        edge_voz="pt-BR-FranciscaNeural" if feminina else "pt-BR-AntonioNeural",
        velocidade=velocidade, bloco_min=cfg_ritmo["bloco_min_seg"], bloco_max=cfg_ritmo["bloco_max_seg"],
        transicao=cfg_ritmo["transicao"], transicao_seg=cfg_ritmo["transicao_seg"],
        animacao_seg=cfg_ritmo["animacao_seg"], movimento=cfg_ritmo["movimento_camera"],
        horarios_video='"18:00"',
        horarios_shorts='"10:00", "13:00", "17:00", "20:00"' if tipo == "cortes" else '"12:00", "15:00", "21:00"',
        **cores)
    for sub in ("assets", "trilhas", "roteiros", "credenciais"):
        (pasta / sub).mkdir(parents=True, exist_ok=True)
    (pasta / "identidade.yaml").write_text(texto, encoding="utf-8")
    (pasta / "fila.txt").write_text(
        "# Fila de temas do canal — um tema por linha (o primeiro é o próximo a ser produzido).\n"
        "# Linhas com # são ignoradas. Roteiro pronto: roteiro: arquivo.txt | tema opcional\n"
        "#   (o arquivo fica em canais/<canal>/roteiros/)\n", encoding="utf-8")
    (pasta / "trilhas" / "LEIA-ME.txt").write_text(
        "Coloque aqui trilhas sonoras livres de direitos (MP3, WAV, OGG ou M4A).\n"
        "Sugestão: Biblioteca de Áudio do YouTube (studio.youtube.com > Biblioteca de áudio).\n"
        "Sem trilhas, a esteira gera um ambiente sonoro sutil automaticamente.\n", encoding="utf-8")
    canal = Canal.carregar(canal_id)
    from .historico import Historico

    Historico(canal.caminho_db)  # cria o banco vazio
    return canal
