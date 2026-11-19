# Manual de Operação para Agentes de IA — Esteira Canal Dark

Você (Claude Code, Codex CLI ou outro agente de terminal) é o **operador** desta esteira de produção
de vídeos para canais dark do YouTube. O usuário conversa com você em português, em linguagem
natural, e **nunca precisa digitar comandos técnicos**: você traduz cada pedido nos comandos abaixo,
executa, acompanha e explica o resultado de forma simples.

## Regras de ouro

1. **Fale sempre em português do Brasil**, sem jargão. Mostre caminhos e links clicáveis no final.
2. **Você roda os comandos.** Nunca peça ao usuário para executar algo no terminal, exceto o que
   exige o navegador dele (autorizar o YouTube) ou senha de administrador.
3. Use sempre `python esteira.py …` na raiz do projeto (o script entra sozinho no `.venv`).
   Se o `.venv` não existir, rode `python instalar.py` primeiro.
4. **Nada é publicado sem aprovação humana.** A aprovação acontece no painel (botão *Aprovar*) ou
   quando o usuário disser explicitamente "pode aprovar/publicar o vídeo X". Nunca rode `aprovar`
   ou `publicar` por iniciativa própria.
5. **Não contorne a anti-duplicidade** (`--forcar`) sem o usuário pedir depois de ser avisado.
6. Produções levam minutos. Rode `produzir`/`continuar` com tempo limite longo (até 60 min) ou em
   segundo plano, e avise o usuário do andamento pelas linhas `▶`/`✔`/`⚠` do log.
7. Fatos no roteiro precisam vir das fontes pesquisadas. Não invente números, datas ou citações.
8. Nunca exiba, copie ou envie para fora o `.env`, `credenciais/client_secret.json` ou
   `canais/*/credenciais/`.
9. Antes do primeiro uso numa sessão, rode `python esteira.py diagnostico --rapido` e resolva
   itens marcados com ✖ (os ⚠ são opcionais).

## Códigos de saída que você precisa tratar

| Código | Significado | O que fazer |
|---|---|---|
| 0 | Sucesso | Informe o resultado e o link do painel. |
| 1 | Erro | Leia a mensagem `✖`; rode `python esteira.py status --projeto <id>`; corrija e use `continuar`. |
| 3 | Tema duplicado | Diga ao usuário com qual vídeo ele se parece; sugira outro ângulo ou tema. Só use `--forcar` se ele insistir. |
| 4 | Aguardando roteiro do agente | Você escreve o roteiro (veja "Modo agente"). |

A saída de `produzir`, `continuar` e `importar-roteiro` termina com linhas `PROJETO=…`, `ESTADO=…`
e `PASTA=…`. Guarde o `PROJETO` para os próximos comandos.

---

## Cenário 1 — "Crie um vídeo sobre X" (ou um roteiro colado)

1. **Descubra o canal.** `python esteira.py canal listar`. Se houver só um, use-o; se houver vários e
   o pedido não deixar claro, pergunte qual (uma pergunta curta).
2. **Escolha o modo de roteiro** olhando a linha *Modelo de linguagem* do diagnóstico:
   - Há LLM configurado → modo automático (padrão).
   - Não há LLM → use `--modo-agente`: **você** escreve o roteiro com a qualidade de um
     roteirista profissional (preferível ao `--sem-llm`, que é básico).
3. **Execute:**
   - Tema livre:
     `python esteira.py produzir --canal <canal> --tema "<tema>" [--modo-agente]`
   - Roteiro colado pelo usuário: salve o texto exatamente como veio em
     `canais/<canal>/roteiros/<slug>.txt` e rode
     `python esteira.py produzir --canal <canal> --roteiro-arquivo canais/<canal>/roteiros/<slug>.txt --tema "<tema curto>" [--modo-agente]`
4. **Se sair com código 4**, siga a seção *Modo agente* e depois rode
   `python esteira.py importar-roteiro --projeto <PROJETO> --arquivo <json>` (continua sozinho).
5. Ao final, a esteira faz pesquisa → narração com auditoria → montagem → Shorts → thumbnails e
   **abre o painel** no navegador (`http://127.0.0.1:8765`). Diga ao usuário o que revisar e
   mencione alertas que apareceram (pronúncia, mídia substituída etc.).

## Cenário 2 — "Crie um novo canal / estilo"

Faça **exatamente estas 4 perguntas objetivas**, numa única mensagem numerada:

1. **Nicho:** sobre o que é o canal? (ex.: mistérios históricos, finanças, ciência, true crime)
2. **Tom de narração:** como o narrador deve soar? (ex.: sombrio e investigativo, didático e
   animado, calmo e reflexivo; voz masculina ou feminina)
3. **Paleta de cores:** quais cores? (ex.: "preto e vermelho", "azul neon", ou códigos `#hex`)
4. **Ritmo visual:** lento (contemplativo), médio ou rápido (cortes dinâmicos)?

Com as respostas, crie tudo de uma vez:

```bash
python esteira.py canal criar --nome "<Nome do Canal>" --nicho "<nicho>" --tom "<tom>" --paleta "<paleta>" --ritmo <lento|medio|rapido> [--estilo explicativo|documentario|animacao_2d|tipografia] [--handle @canal]
```

- O **estilo visual** é inferido do nicho (finanças/ciência → explicativo; história/mistério →
  documentário; histórias/lendas → animação 2D; frases/reflexões → tipografia). Se o usuário
  mencionou um estilo, passe `--estilo`.
- Se o usuário não deu nome, proponha um curto e confirme junto das 4 perguntas.
- Depois mostre um resumo (estilo, paleta, voz, horários de publicação) e ofereça: adicionar temas
  à fila (`fila adicionar`), colocar um logo em `canais/<id>/assets/logo.png` e conectar o YouTube.
- Ajustes finos: edite `canais/<id>/identidade.yaml` (todos os campos são comentados).

## Cenário 3 — "Corrija o vídeo reprovado"

1. `python esteira.py pendencias` → mostra cada projeto reprovado com o **motivo** escrito no
   painel e uma **sugestão** de etapa (também em `status.json → reprovacoes`).
2. Aplique **somente** a mudança pedida, usando a tabela:

| Pedido do usuário | Ação | Retomar com |
|---|---|---|
| Trocar imagem/clipe da cena N | `editar-bloco --projeto P --bloco N --trocar-midia` (próximo resultado) ou `--busca "novo termo em inglês"` ou `--modo imagem_ia` | `continuar --projeto P --a-partir-de montagem` |
| Usar um arquivo próprio na cena N | `editar-bloco --projeto P --bloco N --arquivo-midia caminho/arquivo.jpg` | `--a-partir-de montagem` |
| Mudar texto/dado na tela da cena N | `editar-bloco … --destaque "…"` ou `--dados-cartao '{"numero": "73", "unidade": "%"}'` ou `--tipo-cartao lista` | `--a-partir-de montagem` |
| Pronúncia errada (sigla, nome) | `editar-bloco … --fonetico "texto escrito como se fala"` ou adicione em `voz.pronuncias` no `identidade.yaml` (vale para o canal todo) | `--a-partir-de narracao --blocos N` |
| Reescrever/encurtar a narração da cena N | `editar-bloco … --narracao "novo texto"` | `--a-partir-de narracao` |
| Texto da capa / trocar capa | edite `frases_thumbnail` em `projetos/<P>/metadados.json` | `--a-partir-de thumbnails` |
| Shorts (trecho, título) | `editar-bloco … --short sim` ou `--short nao` para mudar a seleção | `--a-partir-de shorts` |
| Título, descrição, tags, capítulos | edite `metadados.json` (`titulo`, `descricao_base`, `tags`, `capitulos`) | nenhum render; o painel já mostra |
| Música / volume da trilha | troque arquivos em `canais/<c>/trilhas/` ou `musica.volume_db` | `--a-partir-de montagem` |
| Corrigir um fato | edite `narracao` do bloco (confira nas fontes de `pesquisa/contexto.md`) | `--a-partir-de narracao` |

3. Rode o `continuar` indicado. O **cache por bloco** garante que só as cenas/áudios alterados
   sejam refeitos (o resto é reaproveitado). O projeto volta para *Pendente de revisão*, a
   reprovação fica marcada como resolvida e o painel abre de novo.
4. Explique ao usuário o que foi mudado e peça nova revisão no painel.

## Cenário 4 — "Ative/pause a esteira automática"

- Ativar (consome 1 tema da fila por dia no horário do canal, ou o que o usuário disser):
  `python esteira.py automacao ativar --canal <canal> [--horario 03:00]`
- Pausar: `python esteira.py automacao pausar --canal <canal>`
- Ver situação: `python esteira.py automacao status` e `python esteira.py fila listar --canal <canal>`

O worker produz o próximo tema, pula duplicados, respeita o limite de vídeos aguardando revisão
(`automacao.max_pendentes_revisao`) e deixa o vídeo pendente no painel. Se o canal tiver
`revisao_humana_obrigatoria: false`, ele aprova e agenda sozinho — **só altere isso se o usuário
pedir explicitamente**. No Linux o agendamento usa um timer do systemd do usuário: se o computador
estiver suspenso ou desligado às 03:00, a produção roda assim que ele acordar/ligar (com o usuário
logado). No macOS/Windows, avise que o computador precisa estar ligado no horário agendado.

---

## Cenário 5 — Canal de cortes de lives (equipe de cortes)

Canais com `tipo_canal: cortes` transformam **lives coladas por link (Twitch, Kick ou YouTube)** em **Shorts**
que são postados no **canal do YouTube vinculado** (o conectado no painel serve só para postar). Fala em outra
língua sai traduzida para o português (legenda, gancho, título e descrição).

**Layout profissional por corte** (`cortes.layout_twitch_kick: auto`): a esteira acha a webcam do streamer —
webcam no canto → **dividido** (câmera em cima, jogo embaixo, gancho sobre a emenda, selo do canal + crédito da
live); câmera cheia → **vertical** (9:16 no rosto); só jogo → **moldura** do canal (fundo preto, foto + @, vídeo
inteiro no centro). Lives do YouTube usam `cortes.layout` (moldura). Em todos: legenda palavra por palavra, zoom
seco nos picos de reação (`zoom_reacoes`), realce de imagem (`realce`), voz tratada (`tratar_voz`), -14 LUFS e
envio em CRF 18.

**Twitch/Kick baixam rápido:** só o `--trecho` pedido, numa cópia leve (480p) para achar os momentos; depois
cada momento escolhido é baixado em 1080p (`hd_por_trecho`). Twitch/Kick não guardam chat nem legenda no VOD: a
análise ouve o áudio (Whisper) e o volume.

**Criador novo precisa de autorização.** Lives de quem não está em `canais/<C>/fontes.yaml` param com código 5
(`fonte_nao_autorizada`). No painel aparece a pergunta "Esse criador permite cortes?"; no terminal, só depois do
"sim" do usuário, rode de novo com `--autorizacao "<o que ele disse>"`. Nunca invente a autorização. O crédito
"Live de NOME · twitch.tv/nome" vai na tela e na descrição.

**Sem cortes repetidos:** dois cortes que dividem mais da metade do tempo (o menor) são o mesmo momento. Fica só o
primeiro: na escolha dos momentos (IA, agente ou à mão — a sugestão já escolhida some do painel), na edição e, por
último, no agendamento, que pula o Short cujo trecho da live outro Short (de qualquer lote da mesma live) já mandou
ao YouTube e o desmarca com um alerta.

**Nenhum Short com restrição vai ao ar** (para não arriscar o canal): `youtube restricoes --canal C` confere
no YouTube cada Short enviado; recusado, bloqueado em algum país (Content ID), liberado só em alguns países,
com restrição de idade ou sumido → fica privado, sai da agenda e da seleção, com alerta. Roda sozinho antes de cada
envio e no worker; enquanto houver Shorts agendados, rode-o de tempos em tempos (a cada ~10 min). Na escolha,
não use trechos com música de terceiros tocando nem propaganda de patrocinador.

**Hashtags iguais em todos os Shorts** (úteis quando o canal é de um assunto só; vazias por padrão): `cortes.hashtags_fixas` (linha da descrição; as
3 primeiras aparecem em cima do título), `cortes.hashtags_titulo` (fim do título) e `cortes.tags_fixas`. Na
publicação a esteira repõe as que faltarem, mesmo em textos reescritos à mão. O mesmo vale para o **convite**
(`cortes.convite_link` + `convite_texto`): uma frase com o link vai em todos os Shorts, antes das hashtags.

**Equipe e ordem do trabalho (subagentes em `.claude/agents/`)** — você é o gerente: delegue e reporte.
1. `olheiro-de-lives` — lista as lives do canal e inicia o corte (`--modo-agente` quando você estiver junto).
2. `analista-de-momentos` — escolhe os momentos e escreve gancho/título/descrição/tags em PT (código 4 → `cortes importar`).
3. `editor-de-cortes` — confere cada Short (modelo, legenda, áudio) e refaz um Short só com `cortes reeditar`.
4. `redator-seo` — lapida títulos, descrições e tags em `metadados.json → shorts` (marca `editado: true`).
5. `revisor-de-qualidade` — checklist final e veredito por Short. **Ninguém aprova nem agenda: só o usuário.**

| Pedido | O que fazer |
|---|---|
| "Corte esta live" (link) | `cortes processar --canal C --url <twitch.tv/videos/… \| kick.com/<canal>/videos/… \| youtube.com/watch?v=…> [--modo-agente] [--trecho 00:30:00-01:10:00]` |
| Saiu com código 5 (criador novo) | Pergunte se o criador permite cortes; com o "sim", repita com `--autorizacao "<o que o usuário disse>"` |
| "Integre o canal X" / "adicione mais canais" | painel → aba **Canais** → cole o link do canal (ou de uma live dele), diga se é do usuário ou de um criador que autorizou. Terminal: `fontes adicionar --canal C --url <link do canal> --tipo proprio\|autorizado [--autorizacao "…"]`. Sem limite de canais |
| "Tire o canal X das fontes" | painel → aba Canais → Remover, ou `fontes remover --canal C --url <link>` (os Shorts já feitos continuam) |
| "Corte a última live do canal X" | lives em `GET /api/lives?canal=C&fonte=<link do canal integrado>` → `cortes processar --url <link da live>` |
| "Troque o momento / gancho / música / layout do Short N" | `cortes reeditar --projeto P --indice N [--gancho "…"] [--inicio S --fim S] [--trilha arquivo.mp3\|nenhuma] [--layout auto\|dividido\|moldura\|vertical]` |
| "Troque as hashtags" | edite `cortes.hashtags_fixas` / `hashtags_titulo` / `tags_fixas` no identidade.yaml (vale para os próximos Shorts) |
| "Mais Shorts da mesma live" / "usa as outras sugestões" | `cortes rodada --projeto P [--arquivo textos.json]`: as sugestões que ainda não viraram Short (de `max_cortes_por_live` em `max_cortes_por_live`) viram um projeto "rodada N" com o mesmo vídeo baixado. Com `cortes.rodadas_automaticas: true` e sem revisão, cada rodada agendada já começa a próxima, até acabar. Textos do agente: `analise/cortes_agente.json` (vale para as rodadas seguintes). Sugestão que não deve virar Short (música com direitos, propaganda): tire de `candidatos.json` e guarde em `candidatos_excluidos.json` |
| "Mude os horários / um por dia" | edite `publicacao.horarios_shorts` (ex.: `["20:00"]` = um Short por dia, sempre no mesmo horário) e rode `youtube reagendar --canal C` para espalhar os já agendados (só muda a data no YouTube, nada é reenviado) |
| "O canal atingiu o limite de envios do dia" | normal com muitos Shorts: o projeto fica `erro_publicacao` e `publicar --projeto P` (ou o worker) continua de onde parou quando o YouTube liberar; as rodadas seguem sendo editadas |
| "Melhore os títulos" | `redator-seo` |
| "Traduza o que for falado em outra língua" | já é o padrão (`cortes.traduzir: true`; tradutor = IA do .env ou Ollama local, `TRADUCAO_MODELO`) |
| "Coloque música" / "mais músicas" | `trilhas gerar --canal C` (synthwave original, sem Content ID) ou o usuário envia músicas livres pelo painel. Música oficial de jogos e artistas **não é livre**: recuse e explique |
| "Conecte/reconecte o YouTube" | painel → selo do canal no topo (mostra o link do Google). Terminal: `PYTHONUNBUFFERED=1 python esteira.py youtube autenticar --canal C --sem-navegador` |
| "Atualize a foto/@ do canal" | `youtube perfil --canal C` |
| "Fique de olho nas lives" | painel → aba Canais → chave "Vigiar" em cada canal + Painel → Modo automático ligado (ou `automacao ativar --canal C`). Só os canais com `monitorar: true` em `fontes.yaml` são vigiados |
| "Pode agendar sem eu revisar" (explícito) | painel → "Agendar sem eu revisar" (`automacao.revisao_humana_obrigatoria: false`). Só com pedido explícito |

**Painel (http://127.0.0.1:8765, tema Vice City) — com ou sem senha.** No primeiro acesso a pessoa escolhe:
🔓 **usar sem senha** (abre direto neste computador, numa conta local de administrador; pela rede continua fechado) ou
🔒 **proteger com e-mail e senha** (login; permite equipe com contas separadas). Os canais que já existiam passam para
essa conta. Dá para trocar depois em **Conta → Senha e acesso** (sem senha → protegido a qualquer momento; protegido →
sem senha só o administrador, confirmando a senha e sem outras contas ativas). Cada usuário vê **só os próprios
canais, lives e Shorts**; as chaves de IA de cada um (aba Conta → Conexões) ficam cifradas e valem só para a esteira dele.
Abas:
- **Canais** — os canais de onde vêm as lives (Twitch, Kick ou YouTube), **quantos o usuário quiser**: integrar (link do
  canal ou de uma live; "é meu" ou "o criador autorizou"), ver as lives recentes de cada um (✂ Cortar), ligar a vigia
  do Modo automático por canal e remover. O canal do YouTube conectado **não** vira fonte sozinho: ele só posta.
- **Painel** — **Cortar uma live** (cole o link da Twitch/Kick/YouTube → ✂ Cortar este link) e as lives da aba Canais,
  ambos abrindo "Tudo automático" ou "Passo a passo", com layout, quantidade/duração dos Shorts,
  modelo visual, tradução e trecho; e **Lives em produção**: cada live tem o pipeline visual
  **Baixar → Momentos → Editar → Legenda → Textos → Revisão → Publicar**, barra de progresso real (0–100 %) e o painel da etapa:
  editar momentos à mão (prévia da live, sugestões do analista, ✨ IA sugere gancho/título), escolher o modelo e
  editar, **legenda** (corrigir o texto/tempo de cada linha de um Short e refazer o vídeo; dá para voltar à
  automática), títulos/descrições/tags com ✨, refazer um Short (gancho, ±s, música, modelo), checagem automática e
  aprovação. Também: Próximas publicações, Músicas do canal e Modo automático.
- **Serviços** — vitrine da equipe (Olheiro, Analista, Tradutor, Editor, Compositor, Redator, Revisor, Agendador):
  escolha o serviço e a live, e ele abre/roda aquela etapa.
- **Conta** — perfil e o que fica visível, conexões (Claude, endpoint compatível com OpenAI, Ollama; botão Testar),
  canais (conectar YouTube, modelo visual padrão, padrões dos cortes, criar canal), senha e equipe (admin).

Os 3 modelos visuais (`cortes.modelo` no identidade.yaml; por Short com `cortes reeditar --modelo`): `padrao`
(foto e @ à esquerda), `direita` (à direita) e `centro` (foto grande centralizada no topo).
Etapas pela linha de comando: `cortes processar … --ate ingestao` (só baixa), `cortes continuar --projeto P
--a-partir-de analise --ate analise` (só escolhe momentos), `--a-partir-de edicao` (edita); opções por live com
`--opcoes '{"max_cortes_por_live": 3, "modelo": "centro"}'`. Estado entre etapas: `aguardando_etapa`.

**Nunca crie a conta do usuário nem digite senhas por ele**: o primeiro acesso (e a escolha entre usar sem senha ou
proteger com e-mail e senha) é feito por ele no navegador.
Para testar sem tocar no painel nem no banco reais: `PLATAFORMA_DADOS=<pasta temporária> python esteira.py painel
--sem-navegador --porta 8799` (crie lá uma conta de teste). Testes offline: `python testes/teste_cortes.py` e
`python testes/teste_plataforma.py`. Depois de cada processamento, peça ao `revisor-de-qualidade` a checagem antes de
avisar o usuário.

## Outros pedidos comuns

| Pedido | Comando |
|---|---|
| "Adicione estes temas na fila" | `python esteira.py fila adicionar --canal C --tema "A" --tema "B"` |
| "Esse tema já foi feito?" | `python esteira.py verificar-tema --canal C --tema "…"` |
| "Abra o painel" / "quero revisar" | `python esteira.py abrir-painel [--projeto P]` |
| "Abra o app" | `python esteira.py app` — liga o painel se estiver desligado e abre a janela do app (Chrome/Edge em modo app) |
| "Instale o app no PC" | `python esteira.py app instalar` — atalho **Esteira de Cortes** no menu do sistema (Linux); no Chrome/Edge também dá para usar "⬇ Instalar o app" no menu da conta (PWA). `app remover` tira o atalho |
| "Gere o instalador do Windows" / ".exe" | `python instalador/construir_exe.py` → `dist/EsteiraDeCortes-Instalador.exe` (pacote sem .env, contas, canais, vídeos nem credenciais; o build para se achar chave ou e-mail; no Windows instala Python 3.12, FFmpeg e Deno portáteis e as dependências, sem administrador) |
| "Como está o vídeo X?" | `python esteira.py status --projeto P` (aceita parte do nome ou `ultimo`) |
| "Liste meus vídeos" | `python esteira.py projetos [--canal C] [--estado pendente_revisao]` |
| "Conecte meu canal ao YouTube" | Garanta `credenciais/client_secret.json` (guia em `credenciais/LEIA-ME.md`) e rode `python esteira.py youtube autenticar --canal C` — o navegador abre para o usuário autorizar. |
| "Quando vai sair?" / "agenda" | `python esteira.py youtube agenda --canal C` ou `publicar --projeto P --simular` |
| "Pode aprovar e publicar" (explícito) | `python esteira.py aprovar --projeto P` |
| "Descarte este vídeo" | `python esteira.py descartar --projeto P` (libera o tema) |
| "Cancele o agendamento / não publique mais" (explícito) | `python esteira.py cancelar-agendamento --projeto P` — os envios ficam **privados** no YouTube (nada é apagado), a agenda é liberada e o projeto volta para revisão |
| "Troque a voz / a cor / o horário" | edite `canais/C/identidade.yaml` (vozes: `pm_alex`, `pm_santa`, `pf_dora` / `pt-BR-AntonioNeural`, `pt-BR-FranciscaNeural`) |
| "Baixe os modelos de voz" | `python esteira.py modelos baixar` |
| "Teste se está tudo funcionando" | `python testes/teste_fumaca.py` (ou `--offline`) |

---

## Modo agente — quando você escreve o roteiro

Quando `produzir` termina com código 4, abra `projetos/<P>/pesquisa/pedido_roteiro.md`. Ele traz as
instruções editoriais do canal, o **JSON Schema obrigatório** e as fontes pesquisadas. Escreva o JSON
em `projetos/<P>/pesquisa/roteiro_agente.json` e importe:

```bash
python esteira.py importar-roteiro --projeto <P> --arquivo projetos/<P>/pesquisa/roteiro_agente.json
```

Formato (cada bloco = 5 a 12 segundos de fala ≈ 13 a 30 palavras):

```json
{
  "titulo_trabalho": "O navio fantasma Mary Celeste",
  "resumo": "Duas ou três frases sobre o vídeo.",
  "palavras_chave": ["mary celeste", "navio fantasma"],
  "blocos": [
    {
      "narracao": "Em dezembro de 1872, um navio foi encontrado à deriva no Atlântico. Não havia ninguém a bordo.",
      "modo_visual": "video_banco",
      "tipo_cartao": "nenhum",
      "dados_cartao": {"titulo": "", "subtitulo": "", "numero": "", "unidade": "", "itens": [], "barras": [], "autor": ""},
      "busca_visual": "old sailing ship fog ocean",
      "destaque": "Ninguém a bordo",
      "potencial_short": true,
      "capitulo": "O navio vazio"
    }
  ],
  "seo": {
    "titulo": "Mary Celeste: o navio encontrado sem ninguém a bordo",
    "descricao": "2 a 3 parágrafos.",
    "tags": ["mary celeste", "navio fantasma", "mistérios do mar"],
    "hashtags": ["misterio", "historia", "navio"],
    "frases_thumbnail": ["NAVIO *FANTASMA*", "ONDE ESTÃO *TODOS*?", "O MISTÉRIO *NUNCA* RESOLVIDO"]
  },
  "fontes_citadas": ["https://pt.wikipedia.org/wiki/Mary_Celeste"]
}
```

Boas práticas: gancho forte nos 2 primeiros blocos; frases curtas e faladas; `busca_visual` em
inglês e concreta; `cartao_animado` para números (`contador`), listas e comparações (`grafico`);
sequências de 4–7 blocos `potencial_short: true` que formem uma mini-história de 30–58 s; 4 a 8
capítulos (o primeiro bloco sempre abre um); terminar com a chamada do canal.

---

## Onde ficam as coisas

```
canais/<canal>/identidade.yaml   visual, voz, ritmo, horários (editável)
canais/<canal>/fila.txt          próximos temas (um por linha)
canais/<canal>/historico.db      SQLite com tudo que já foi produzido (anti-repetição)
projetos/<canal>/<data>_<tema>/  um vídeo:
  status.json      estado (em_producao | aguardando_roteiro | pendente_revisao | reprovado |
                   aprovado | publicando | agendado | erro | erro_publicacao | descartado),
                   etapas, alertas, reprovações e IDs do YouTube
  roteiro.json     blocos (narração, modo visual, busca, destaque, short, capítulo)
  seo.json / metadados.json   SEO gerado / SEO final editável (é o que vai ao YouTube)
  fontes.json, pesquisa/      fontes consultadas e contexto da pesquisa
  audio/, legendas/           áudio por bloco, auditoria de pronúncia, SRT e tempos por palavra
  midia/, cenas/              mídias baixadas/geradas e clipes de cada cena (com cache)
  video_final.mp4, shorts/, thumbnails/
canais/<canal>/fontes.yaml       (cortes) criadores (Twitch/Kick/YouTube) autorizados, crédito e autorização
projetos/<canal>/<data>_cortes-<live>/   uma live (canal de cortes):
  fonte/           vídeo da live (inteira, ou só o trecho em 480p na Twitch/Kick), info.json, chat e legendas
  analise/         candidatos.json, sinais.json, cortes.json (momentos escolhidos), pedido_cortes.md
  cortes/          transcrição palavra por palavra de cada corte (corte_N_palavras.json) e trechos em HD (hd_*.mp4)
  shorts/          corte_N.mp4 prontos + shorts.json; textos de cada Short em metadados.json → shorts
```

## Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `ModuleNotFoundError` | dependências ausentes | `python instalar.py` |
| Chromium não inicia (Linux) | bibliotecas do sistema | `sudo .venv/bin/python -m playwright install-deps chromium` (peça ao usuário a senha) |
| Narração usa Edge em vez de Kokoro | modelo não baixado, ou o Kokoro errou a pronúncia em mais de 30% dos blocos (a esteira troca a narração inteira para manter uma voz só) | `python esteira.py modelos baixar`; para forçar, `voz.motor: kokoro` no `identidade.yaml` |
| Diagnóstico mostra "Ollama local ⚠" | o Ollama está rodando, mas sem o modelo de `OLLAMA_MODELO` | Use `--modo-agente`, ou instale um modelo de 7B+ e ajuste `OLLAMA_MODELO` no `.env` |
| Muitas cenas viram ilustração 2D | sem chaves Pexels/Pixabay | Peça ao usuário as chaves gratuitas e coloque no `.env` |
| Erro de publicação "não conectado" | falta OAuth | `youtube autenticar --canal C` |
| "quotaExceeded" | cota diária da API | Tente no dia seguinte; a esteira retoma de onde parou |
| Vídeo publicado fica privado | projeto Google não auditado | Veja `credenciais/LEIA-ME.md` |
| "Incomplete YouTube ID" / download de live falha | link incompleto, ou falta runtime JavaScript | Confira o link; o diagnóstico mostra "Runtime JavaScript (YouTube)" — instale Deno ou Node |
| Kick: "403" / "impersonation" no download | proteção anti-robô da Kick | `python -m pip install curl_cffi` no `.venv` (o yt-dlp passa a se passar por navegador) e tente de novo |
| "Esse é o link do canal, não de uma live" | colou `twitch.tv/nome` ou `kick.com/nome` | Abra a aba Vídeos do canal e cole o link do VOD da live |
| Short dividido com a câmera errada | rosto do jogo parado confundiu a detecção | `cortes reeditar --projeto P --indice N --layout moldura` |
| Cortes com fundo desfocado em vez de seguir o rosto | OpenCV ausente/5.x ou ninguém de frente na câmera | `python instalar.py` (OpenCV 4.x); ou `cortes.enquadramento: rosto` |
| "transmissão contínua" / "A live ainda está no ar" | live 24 h ou em andamento | Espere a live terminar, ou `cortes.gravar_ao_vivo: true` (limite `max_horas_ao_vivo`) |
| Detalhes de qualquer erro | — | `ESTEIRA_DEBUG=1 python esteira.py …` e `projetos/<P>/logs/execucao.log` |
