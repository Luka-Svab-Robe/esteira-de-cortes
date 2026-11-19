# Plataforma (com ou sem senha) — desenho e estado da implementação (login opcional em 19/11/2026)

Evolução do painel de cortes (FastAPI + JS puro) para uma plataforma com contas, pipeline por etapas,
vitrine de serviços, 3 templates e conexões por usuário. Mantém a esteira (`esteira/cortes/*`) como motor.

## Backend
- `painel/contas.py` ✅ — SQLite `dados/plataforma.db`: `usuarios` (scrypt, papel admin/usuario, perfil + visibilidade),
  `sessoes` (só hash SHA-256 do token, 30 dias deslizantes), `conexoes` (Fernet, chave em `dados/.chave_plataforma`),
  `canais_dono` (canal → usuário), `config` (`modo_acesso` = aberto|protegido e `usuario_aberto`).
  `ambiente_do_usuario()` vira env dos subprocessos da esteira.
- Modo de acesso ✅ — escolhido no primeiro acesso: **aberto** (conta local de administrador sem senha, e-mail
  interno `local@localhost`, que não entra pela tela de login; o middleware a usa só quando não há cookie e o pedido
  vem de 127.0.0.1/::1 pelo endereço local, então nomes liberados em `PAINEL_HOSTS` continuam pedindo login) ou
  **protegido** (e-mail e senha). Troca: `/api/conta/proteger` (aberto → protegido) e `/api/conta/sem-senha`
  (protegido → aberto: admin, senha atual, sem outras contas ativas, só neste computador). No modo aberto não se
  criam contas de equipe. Bancos antigos sem `config` e com contas são tratados como protegido.
- `painel/sessao.py` + `painel/servidor.py` ✅ — middleware de sessão (cookie `sessao`, HttpOnly, SameSite=Lax) que põe o usuário num
  ContextVar; rotas que mudam algo exigem o cabeçalho `X-Esteira: 1` (anti-CSRF). Rotas novas:
  `/api/sessao` (traz o `modo`), `/api/setup` (1º acesso: `modo` aberto ou admin com senha; adota canais sem dono),
  `/api/login`, `/api/logout`, `/api/conta/proteger`, `/api/conta/sem-senha`,
  `/api/conta` (perfil + visibilidade), `/api/conta/senha`, `/api/conexoes` (+ `/testar`), `/api/usuarios` (admin),
  `/api/canais/novo` (criar canal de cortes), etapas: `/api/projetos/{c}/{p}/etapas/{etapa}` (executar uma etapa),
  `/api/projetos/{c}/{p}/momentos` (GET/PUT edição manual dos cortes), `/api/copiloto` (sugestões de texto),
  `/api/projetos/{c}/{p}/revisao` (checagem automática), `/api/modelos/{c}/{modelo}.png` (prévias dos templates),
  `/api/canais/{c}/padroes` (modelo, quantidade/duração dos Shorts e tradução padrão do canal),
  `/api/canais/{c}/agenda` (PUT: horários `HH:MM` e dias da semana em `publicacao`, aba Agendados → Seus canais de publicação).
- Isolamento: `_canal_cortes()`/`_projeto()` checam o dono; envios guardam `usuario_id`; `/api/painel` só do usuário.
- Etapas da esteira: `cortes processar --ate ingestao`, `cortes continuar --a-partir-de analise|edicao --ate analise|edicao`;
  estado `aguardando_etapa` entre etapas.
- Progresso real (0–100 %): download (log do yt-dlp) 0–40, análise 40–50, edição 50–95 (Short k de N), final 95–100.
  A tela mostra só rótulo amigável + % (nada de log bruto).
- Templates: `short_moldura.html` com `dados.modelo` ∈ `padrao` (foto à esquerda, atual), `direita` (foto à direita),
  `centro` (foto centralizada no topo). Padrão do canal: `cortes.modelo`; por Short: `corte.modelo` / `reeditar --modelo`.
- Desempenho: GZip, ETag/304 no `/api/painel`, cache de `automacao.status()` (30 s), cache do resumo por mtime,
  cache longo para `/static` (versionado) e `/fontes`.

## Frontend (`painel/static/`, módulos ES sem build)
- Telas: Entrar / Primeiro acesso; abas **Painel** (lives, pipeline de cada live, cortes), **Serviços** (vitrine de
  cards: Olheiro, Analista, Tradutor, Editor, Compositor, Redator, Revisor, Agendador), **Conta** (perfil e
  visibilidade, senha, conexões, canais, usuários para admin).
- Pipeline visual: Baixar → Momentos → Editar → Legenda → Textos → Revisão → Publicar; cada etapa mostra estado, opções
  antes de executar e um editor manual com botão "✨ IA sugere" (copiloto).
- Progresso: animação + barra com %; sem logs na tela.

## Arquivos (implementado)
- Backend: `painel/contas.py`, `sessao.py`, `rotas_conta.py`, `rotas_etapas.py`, `rotas_legenda.py` (GET/PUT
  `/api/projetos/{c}/{p}/shorts/{n}/legenda`; a esteira usa `cortes/corte_N_legenda_editada.json` quando existe), `copiloto.py`, `progresso.py`, `servidor.py`.
- Esteira: `esteira/cortes/orquestrador.py` (`cfg_cortes`, `--ate`, `opcoes`, marcos de progresso),
  `edicao.py` (`MODELOS`, `html_moldura`, `previa_modelo`), `templates/cenas/short_moldura.html` (classes `modelo-*`).
- Frontend: `painel/static/js/` — `app.js` (sessão, abas, atualização com ETag), `base.js`, `estado.js`, `entrada.js`,
  `lives.js`, `projetos.js` (pipeline), `momentos.js`, `textos.js`, `copiloto.js`, `progresso.js`, `canal.js`,
  `servicos.js` (vitrine), `conta.js`, `legenda.js` (etapa Legenda).
- Testes: `testes/teste_plataforma.py` (contas, isolamento, etapas, copiloto, checagem, progresso) e `testes/teste_cortes.py`.

## Observações
- O copiloto usa, nesta ordem: a IA da conta do usuário → a IA do `.env` da plataforma → Ollama local → regras.
  Sem chave, o Ollama local (CPU) leva ~25 s por sugestão; com Claude, poucos segundos.
- As chaves do `.env` valem para todos os usuários que não cadastrarem as próprias (são da plataforma).

## App de PC
- PWA: `painel/static/manifest.webmanifest` (servido em `/manifest.webmanifest`), `painel/static/sw.js` (servido em `/sw.js`,
  escopo do site inteiro; rede primeiro, guarda só `/static` e `/fontes`, nunca `/api` nem `/arquivos`; mostra
  "O painel está desligado" se o servidor cair) e ícones em `painel/static/icones/`.
- `esteira/app_desktop.py` + `python esteira.py app [abrir|instalar|remover]`: abre o painel em janela de app
  (Chrome/Chromium/Edge/Brave `--app=`), liga o servidor se preciso e cria `~/.local/share/applications/esteira-de-cortes.desktop`.
