"use strict";
/* Esteira de Cortes — painel. Dados: GET /api/painel (canal, cortes, agenda, músicas) e GET /api/lives
   (lives do canal vinculado). Todo texto vindo de fora entra via textContent (nada de innerHTML). */

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const estado = {
  dados: null, lives: null, canalId: null, assinaturas: new Map(), sujos: new Set(), abertos: new Set(),
  timer: null, dispensados: new Set(), conectando: false, abriu: false, carregandoLives: false,
};
const PASSOS = ["Baixar a live", "Escolher momentos", "Editar os Shorts", "Agendar"];
const ESTADOS_ANDAMENTO = ["em_producao", "publicando", "aprovado", "aguardando_roteiro"];
const ESTADOS_EDITAVEIS = ["pendente_revisao", "reprovado", "erro_publicacao"];
const ROTULOS = {
  pendente_revisao: "Esperando sua aprovação", reprovado: "Esperando correção", agendado: "Agendado",
  erro: "Deu erro", erro_publicacao: "Erro ao agendar", publicando: "Enviando ao YouTube",
  aprovado: "Aprovado", em_producao: "Em produção", aguardando_roteiro: "Esperando a IA", descartado: "Descartado",
};
const IDIOMAS = { en: "inglês", es: "espanhol", fr: "francês", de: "alemão", it: "italiano", ja: "japonês", ko: "coreano" };

/* ------------------------------------------------------------ utilidades */
function el(tag, attrs = {}, ...filhos) {
  const no = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") no.className = v;
    else if (k === "text") no.textContent = v;
    else if (k === "style") no.setAttribute("style", v);
    else if (k.startsWith("on")) no.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled", "selected"].includes(k)) no[k] = v;
    else no.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) no.append(f);
  return no;
}

async function api(url, opcoes = {}) {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opcoes });
  let corpo = null;
  try { corpo = await r.json(); } catch (_) { /* sem corpo */ }
  if (!r.ok) {
    const d = corpo && corpo.detail;
    const erro = new Error((d && (d.mensagem || (typeof d === "string" ? d : ""))) || `Erro ${r.status}`);
    erro.dados = d && typeof d === "object" ? d : {};
    throw erro;
  }
  return corpo;
}
const postar = (url, corpo = {}) => api(url, { method: "POST", body: JSON.stringify(corpo) });

let toastTimer;
function avisar(texto, tipo = "") {
  const t = $("#toast");
  t.textContent = texto;
  t.className = `toast visivel ${tipo}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 4500);
}

function dataCurta(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const dia = d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }).replace(".", "");
  return `${dia} · ${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}
function duracaoTexto(seg) {
  seg = Math.round(Number(seg) || 0);
  if (!seg) return "";
  const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = seg % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function proximaExecucao(texto) {
  const m = /(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})/.exec(texto || "");
  return m ? `${m[3]}/${m[2]} às ${m[4]}` : "";
}
function normalizarHora(texto) {
  const bruto = String(texto || "").trim();
  if (!bruto) return null;
  const partes = bruto.split(":").map((x) => x.replace(/\D/g, ""));
  if (partes.some((x) => x === "") || partes.length > 3) return null;
  while (partes.length < 3) partes.unshift("0");
  return partes.map((x) => x.padStart(2, "0")).join(":");
}
const idCard = (id) => "live-" + id.replace(/[^\w-]/g, "_");
const canal = () => (estado.dados ? estado.dados.canais.find((c) => c.id === estado.canalId) || estado.dados.canais[0] : null);
const nomeMusica = (nome) => (nome || "").replace(/^synthwave_/, "").replace(/\.\w+$/, "").replace(/_/g, " ");

/* ------------------------------------------------------------ ciclo de atualização */
async function carregar() {
  try {
    estado.dados = await api("/api/painel");
    estado.canalId = (canal() || {}).id || null;
    render();
    if (!estado.lives && !estado.carregandoLives && canal()) carregarLives(false);
    fecharAbertura();
  } catch (_) {
    $("#canal-chip-texto").textContent = "Painel sem conexão — tentando de novo…";
  }
  agendarAtualizacao();
}
function agendarAtualizacao() {
  clearTimeout(estado.timer);
  const d = estado.dados;
  const ocupado = d && (d.envios.some((e) => e.estado === "rodando") ||
    d.projetos.some((p) => ESTADOS_ANDAMENTO.includes(p.estado) || p.publicando || p.shorts.some((s) => s.refazendo)) ||
    d.canais.some((c) => c.youtube.em_andamento));
  estado.timer = setTimeout(carregar, ocupado ? 2500 : 15000);
}
function fecharAbertura() {
  if (estado.abriu) return;
  estado.abriu = true;
  setTimeout(() => $("#abertura").classList.add("saindo"), 700);
}
function render() {
  renderTopo();
  renderAndamento();
  renderCortes();
  renderAgenda();
  renderMusicas();
  renderAuto();
  renderYoutube();
  if (estado.lives) renderLives();
}

/* ------------------------------------------------------------ topo */
function renderTopo() {
  const c = canal();
  const chip = $("#canal-chip");
  chip.className = "canal-chip";
  const foto = $("#canal-foto");
  if (c && c.avatar) {
    if (foto.tagName !== "IMG") {
      const img = el("img", { id: "canal-foto", alt: "", src: c.avatar });
      foto.replaceWith(img);
    } else if (foto.getAttribute("src") !== c.avatar) foto.src = c.avatar;
  }
  if (!c) { chip.classList.add("alerta"); $("#canal-chip-texto").textContent = "Nenhum canal de cortes"; return; }
  const yt = c.youtube || {};
  if (yt.em_andamento) { chip.classList.add("alerta"); $("#canal-chip-texto").textContent = "Conectando o YouTube…"; }
  else if (yt.conectado) { chip.classList.add("ok"); $("#canal-chip-texto").textContent = yt.handle || yt.nome || c.nome; }
  else { chip.classList.add("alerta"); $("#canal-chip-texto").textContent = "Conectar YouTube"; }
  $("#frase-heroi").textContent = c.revisao
    ? "As lives do seu canal aparecem aqui. Escolha uma: a equipe corta os melhores momentos, edita no modelo do canal, legenda, põe música e deixa os Shorts prontos para você aprovar."
    : "As lives do seu canal aparecem aqui. Escolha uma: a equipe corta os melhores momentos, edita, legenda, põe música e já agenda os Shorts no seu canal.";
}

/* ------------------------------------------------------------ lives do canal vinculado */
async function carregarLives(atualizar) {
  const c = canal();
  if (!c) return;
  estado.carregandoLives = true;
  const grade = $("#lista-lives");
  if (!estado.lives || atualizar) {
    grade.replaceChildren(...[1, 2, 3].map(() => el("div", { class: "esqueleto esqueleto-live" })));
  }
  const botao = $("#btn-atualizar-lives");
  botao.disabled = true;
  botao.replaceChildren(el("span", { class: "giro" }), "Procurando…");
  try {
    estado.lives = await api(`/api/lives?canal=${encodeURIComponent(c.id)}${atualizar ? "&atualizar=true" : ""}`);
    renderLives(true);
  } catch (e) {
    grade.replaceChildren(el("div", { class: "vazio" }, el("strong", { text: "Não consegui ver as lives" }), e.message));
  } finally {
    estado.carregandoLives = false;
    botao.disabled = false;
    botao.textContent = "↻ Procurar lives novas";
  }
}

let assinaturaLives = "";
function renderLives(forcar = false) {
  const grade = $("#lista-lives");
  const lives = estado.lives || [];
  const rodandoAgora = (estado.dados ? estado.dados.envios : []).filter((e) => e.estado === "rodando").map((e) => e.url).sort();
  // estado atual de cada live a partir dos projetos (mais novo que a lista de lives)
  const porLive = {};
  for (const p of (estado.dados ? estado.dados.projetos : [])) {
    const m = /[?&]v=([\w-]+)/.exec(p.live.url || "");
    if (m) porLive[m[1]] = { id: p.id, estado: p.estado };
  }
  const assinatura = JSON.stringify([lives, rodandoAgora, porLive]);
  // não redesenha à toa (perderia o clique ou o trecho que você está digitando)
  if (!forcar && (assinatura === assinaturaLives || grade.contains(document.activeElement))) return;
  assinaturaLives = assinatura;
  grade.replaceChildren();
  if (!lives.length) {
    grade.append(el("div", { class: "vazio" }, el("strong", { text: "Nenhuma live no canal ainda" }),
      "Quando você fizer uma live, ela aparece aqui para cortar."));
    return;
  }
  const rodando = new Set((estado.dados ? estado.dados.envios : []).filter((e) => e.estado === "rodando").map((e) => e.url));
  for (const live of lives) {
    const projeto = porLive[live.id] || (estado.dados ? null : live.projeto);
    const ini = el("input", { type: "text", placeholder: "início", inputmode: "numeric", "aria-label": "Início do trecho" });
    const fim = el("input", { type: "text", placeholder: "fim", inputmode: "numeric", "aria-label": "Fim do trecho" });
    const trecho = el("details", {},
      el("summary", { class: "pequeno", text: "Só um pedaço da live?" }),
      el("div", { class: "linha-trecho", style: "margin-top:6px" }, ini, el("span", { class: "pequeno", text: "até" }), fim));
    const emAndamento = rodando.has(`https://www.youtube.com/watch?v=${live.id}`) || (projeto && ESTADOS_ANDAMENTO.includes(projeto.estado));
    const acoes = el("div", { class: "acoes" });
    if (projeto && !emAndamento) {
      acoes.append(el("button", { class: "botao pequeno", type: "button", text: "Ver os cortes",
        onclick: () => { location.hash = `#/projeto/${projeto.id}`; destacarDoEndereco(); } }));
    } else if (emAndamento) {
      acoes.append(el("span", { class: "linha-progresso" }, el("span", { class: "giro" }), "Sendo cortada…"));
    } else {
      const botao = el("button", { class: "botao cta", type: "button", text: "✂ Cortar esta live" });
      botao.addEventListener("click", () => cortarLive(live, ini.value, fim.value, botao));
      acoes.append(botao);
    }
    grade.append(el("article", { class: "card live-card" },
      el("div", { class: "capa", style: live.miniatura ? `background-image:url("${live.miniatura.replace(/"/g, "")}")` : "" },
        live.duracao ? el("span", { class: "duracao", text: duracaoTexto(live.duracao) }) : null),
      el("div", { class: "corpo" },
        el("h3", { text: live.titulo || "Live" }),
        el("div", { class: "pequeno" },
          projeto ? el("span", { class: `pill ${projeto.estado}`, text: ROTULOS[projeto.estado] || projeto.estado })
            : el("span", { class: "pill nova", text: "Ainda não cortada" }),
          live.visualizacoes != null ? ` · ${Number(live.visualizacoes).toLocaleString("pt-BR")} visualizações` : ""),
        projeto || emAndamento ? null : trecho,
        acoes)));
  }
}

async function cortarLive(live, ini, fim, botao) {
  const c = canal();
  let trecho = "";
  if (String(ini).trim() || String(fim).trim()) {
    const a = normalizarHora(ini), b = normalizarHora(fim);
    if (!a || !b) return avisar("Preencha o começo e o fim do trecho, ex.: 30:00 até 1:10:00.", "erro");
    trecho = `${a}-${b}`;
  }
  botao.disabled = true;
  botao.replaceChildren(el("span", { class: "giro" }), "Conferindo…");
  try {
    await postar("/api/cortes/processar", { canal: c.id, id: live.id, trecho });
    avisar("Começou! Acompanhe em “Em andamento”.", "ok");
    await carregar();
    renderLives(true);
    $("#sec-andamento").scrollIntoView({ block: "start" });
  } catch (e) {
    const d = e.dados || {};
    if (d.codigo === "repetida" && d.projeto) { location.hash = `#/projeto/${d.projeto}`; destacarDoEndereco(); }
    avisar(e.message, "erro");
    botao.disabled = false;
    botao.textContent = "✂ Cortar esta live";
  }
}

/* ------------------------------------------------------------ em andamento */
function passos(atual) {
  return el("div", { class: "passos" }, PASSOS.map((nome, i) =>
    el("div", { class: `passo ${i < atual ? "feito" : i === atual ? "atual" : ""}`, text: nome })));
}

function renderAndamento() {
  const d = estado.dados;
  const lista = $("#lista-andamento");
  lista.replaceChildren();
  const ativos = d.projetos.filter((p) => ESTADOS_ANDAMENTO.includes(p.estado) || p.publicando);
  const idsTodos = new Set(d.projetos.map((p) => p.id));
  let n = 0;
  for (const p of ativos) {
    const etapa = p.estado === "publicando" || p.estado === "aprovado" ? 3 : p.etapa;
    const linha = p.estado === "aguardando_roteiro"
      ? "Esperando a IA escolher os momentos — peça ao Claude: “escolha os cortes da live”."
      : p.progresso || "Trabalhando…";
    lista.append(el("div", { class: "card tarefa" },
      el("div", { class: "tarefa-topo" },
        el("div", {}, el("h3", { text: p.live.titulo || "Live" }), el("div", { class: "pequeno", text: p.fonte.nome || p.live.canal })),
        el("span", { class: `pill ${p.estado}`, text: ROTULOS[p.estado] || p.estado_texto })),
      passos(etapa),
      el("div", { class: "linha-progresso" }, el("span", { class: "giro" }), el("span", { text: linha }))));
    n++;
  }
  for (const e of d.envios) {
    if (estado.dispensados.has(e.id) || (e.projeto && idsTodos.has(e.projeto))) continue;
    if (e.estado === "rodando") {
      lista.append(el("div", { class: "card tarefa" },
        el("div", { class: "tarefa-topo" },
          el("div", {}, el("h3", { text: e.titulo || "Live enviada" }), el("div", { class: "pequeno", text: e.dono || "" })),
          el("span", { class: "pill em_producao", text: "Começando" })),
        passos(0),
        el("div", { class: "linha-progresso" }, el("span", { class: "giro" }), el("span", { text: e.linha || "Lendo a live…" }))));
      n++;
    } else if (e.estado === "erro") {
      lista.append(el("div", { class: "card tarefa falhou" },
        el("div", { class: "tarefa-topo" },
          el("div", {}, el("h3", { text: e.titulo || "Live enviada" })),
          el("span", { class: "pill erro", text: "Não deu certo" })),
        el("p", { class: "pequeno", text: e.linha || "O processamento parou. Tente de novo." }),
        el("div", { class: "acoes" }, el("button", { class: "botao pequeno", type: "button", text: "Dispensar",
          onclick: () => { estado.dispensados.add(e.id); renderAndamento(); } }))));
      n++;
    }
  }
  $("#sec-andamento").classList.toggle("oculto", n === 0);
  $("#qtd-andamento").textContent = n;
}

/* ------------------------------------------------------------ seus cortes (atualiza só o que mudou) */
function renderCortes() {
  const lista = $("#lista-cortes");
  const projetos = estado.dados.projetos.filter((p) => !ESTADOS_ANDAMENTO.includes(p.estado) && !p.publicando);
  const vazio = lista.querySelector(".vazio");
  if (!projetos.length) {
    if (!vazio) lista.replaceChildren(el("div", { class: "vazio" }, el("strong", { text: "Nenhum corte ainda" }),
      "Escolha uma live lá em cima. Os Shorts prontos aparecem aqui."));
    estado.assinaturas.clear();
    return;
  }
  if (vazio) vazio.remove();
  const ids = new Set(projetos.map((p) => p.id));
  for (const card of [...lista.querySelectorAll("[data-projeto]")]) {
    if (!ids.has(card.dataset.projeto)) { card.remove(); estado.assinaturas.delete(card.dataset.projeto); }
  }
  let anterior = null;
  for (const p of projetos) {
    const assinatura = JSON.stringify({ ...p, progresso: "" });
    let card = lista.querySelector(`[data-projeto="${CSS.escape(p.id)}"]`);
    const ocupado = estado.sujos.has(p.id) || [...estado.abertos].some((k) => k.startsWith(p.id + "#"));
    if (!card || (estado.assinaturas.get(p.id) !== assinatura && !ocupado)) {
      const novo = cardProjeto(p);
      if (card) card.replaceWith(novo); else lista.append(novo);
      card = novo;
      estado.assinaturas.set(p.id, assinatura);
    } else if (ocupado) {
      atualizarSobreposicoes(card, p);  // mantém o que você está editando; só mostra "refazendo"
    }
    if (anterior && anterior.nextElementSibling !== card) anterior.after(card);
    anterior = card;
  }
  destacarDoEndereco();
}

function atualizarSobreposicoes(card, p) {
  for (const s of p.shorts) {
    const fig = card.querySelector(`.short[data-indice="${s.indice}"] .tela`);
    if (!fig) continue;
    const atual = fig.querySelector(".refazendo");
    if (s.refazendo && !atual) fig.append(sobreposicaoRefazendo());
    if (!s.refazendo && atual) atual.remove();
  }
}
const sobreposicaoRefazendo = () => el("div", { class: "refazendo" }, el("span", { class: "giro" }), el("span", { text: "Refazendo este Short…" }));

function cardProjeto(p) {
  const editavel = ESTADOS_EDITAVEIS.includes(p.estado);
  const card = el("article", { class: "card", id: idCard(p.id), "data-projeto": p.id });
  const quando = new Date(p.criado_em);
  const subtitulo = [p.live.canal || p.fonte.nome, isNaN(quando) ? "" : `cortada em ${quando.toLocaleDateString("pt-BR")}`,
    p.trecho ? `trecho ${p.trecho.replace("-", " até ")}` : ""].filter(Boolean).join(" · ");
  card.append(el("div", { class: "projeto-topo" },
    el("div", {},
      el("h3", {}, p.live.url ? el("a", { href: p.live.url, target: "_blank", rel: "noopener", text: p.live.titulo || "Live" }) : (p.live.titulo || "Live")),
      el("div", { class: "pequeno", text: subtitulo })),
    el("span", { class: `pill ${p.estado}`, text: ROTULOS[p.estado] || p.estado_texto })));
  if (["erro", "erro_publicacao"].includes(p.estado) && p.detalhe) card.append(el("div", { class: "aviso erro", text: p.detalhe }));
  else if (p.alertas.length && p.estado !== "agendado") card.append(el("div", { class: "aviso", text: p.alertas.join(" ") }));
  if (p.shorts.length) card.append(el("div", { class: "shorts" }, p.shorts.map((s) => cardShort(p, s, editavel))));

  const acoes = el("div", { class: "acoes" });
  if (editavel && p.shorts.length) {
    acoes.append(
      el("button", { class: "botao cta", type: "button", text: p.estado === "erro_publicacao" ? "Tentar agendar de novo" : "Aprovar e agendar", onclick: () => aprovar(p, card) }),
      el("button", { class: "botao", type: "button", text: "Salvar textos", onclick: () => salvarTextos(p, card) }));
  }
  if (p.estado === "agendado") acoes.append(el("button", { class: "botao perigo", type: "button", text: "Cancelar agendamento", onclick: () => cancelar(p) }));
  if (p.estado === "erro") acoes.append(el("button", { class: "botao turquesa", type: "button", text: "Tentar de novo", onclick: () => tentar(p) }));
  if (p.estado !== "agendado") acoes.append(el("button", { class: "botao perigo", type: "button", text: "Descartar", onclick: () => descartar(p) }));
  card.append(acoes);
  return card;
}

function cardShort(p, s, editavel) {
  const chave = `${p.id}#${s.indice}`;
  const sujar = () => estado.sujos.add(p.id);
  const yt = s.youtube || {};
  const fig = el("figure", { class: `short ${s.selecionado ? "" : "fora"}`, "data-indice": s.indice });
  const tela = el("div", { class: "tela" }, el("video", { src: s.video ? `${s.video}#t=1` : "", controls: true, preload: "metadata", playsinline: true }));
  if (s.refazendo) tela.append(sobreposicaoRefazendo());
  fig.append(tela);
  const legenda = el("figcaption");
  if (editavel) {
    legenda.append(el("textarea", { rows: 3, maxlength: 100, "aria-label": `Título do Short ${s.indice}`, "data-campo": "titulo", value: s.titulo, oninput: sujar }));
    legenda.append(el("label", { class: "marcar" },
      el("input", { type: "checkbox", "data-campo": "selecionado", checked: s.selecionado,
        onchange: (ev) => { sujar(); fig.classList.toggle("fora", !ev.target.checked); } }), "Publicar este"));
  } else {
    legenda.append(el("div", { class: "titulo-fixo", text: s.titulo }));
  }
  const meta = el("div", { class: "meta" }, el("span", { class: "etiqueta", text: s.duracao ? `${Math.round(s.duracao)} s` : "" }));
  if (s.trilha) meta.append(el("span", { class: "etiqueta", text: s.trilha === "nenhuma" ? "sem música" : `♫ ${nomeMusica(s.trilha)}` }));
  if (s.traduzida_de) meta.append(el("span", { class: "etiqueta traducao", text: `traduzido do ${IDIOMAS[s.traduzida_de] || s.traduzida_de}` }));
  if (yt.publicar_em) meta.append(el("span", { class: "quando", text: `Sai ${dataCurta(yt.publicar_em)}` }));
  if (yt.url) meta.append(el("a", { href: yt.url, target: "_blank", rel: "noopener", text: "Ver no YouTube" }));
  if (!s.selecionado && !editavel) meta.append(el("span", { class: "etiqueta", text: "não publicado" }));
  legenda.append(meta);
  if (s.erro_refazer) legenda.append(el("div", { class: "aviso erro", text: `Não deu para refazer: ${s.erro_refazer}` }));
  if (editavel) {
    const editor = editorShort(p, s, chave, fig);
    const botao = el("button", { class: "botao pequeno turquesa", type: "button", text: estado.abertos.has(chave) ? "Fechar edição" : "✎ Editar este Short" });
    botao.addEventListener("click", () => {
      const abrir = editor.classList.contains("oculto");
      editor.classList.toggle("oculto", !abrir);
      botao.textContent = abrir ? "Fechar edição" : "✎ Editar este Short";
      if (abrir) estado.abertos.add(chave); else estado.abertos.delete(chave);
    });
    legenda.append(botao);
    fig.append(legenda, editor);
  } else {
    fig.append(legenda);
  }
  return fig;
}

function editorShort(p, s, chave, fig) {
  const trilhas = (canal() || {}).trilhas || [];
  const gancho = el("input", { type: "text", maxlength: 80, value: s.gancho || "", "aria-label": "Gancho do vídeo" });
  const ajusteIni = el("input", { type: "number", step: "0.5", value: "0", "aria-label": "Ajuste do início em segundos" });
  const ajusteFim = el("input", { type: "number", step: "0.5", value: "0", "aria-label": "Ajuste do fim em segundos" });
  const musica = el("select", { "aria-label": "Música do Short" },
    el("option", { value: "nenhuma", text: "Sem música", selected: s.trilha === "nenhuma" }),
    trilhas.map((t) => el("option", { value: t.nome, text: `${t.original ? "★ " : ""}${nomeMusica(t.nome)}`, selected: s.trilha === t.nome })));
  const descricao = el("textarea", { rows: 4, value: s.descricao || "", "aria-label": "Descrição", oninput: () => estado.sujos.add(p.id) });
  const tags = el("input", { type: "text", value: (s.tags || []).join(", "), "aria-label": "Tags", oninput: () => estado.sujos.add(p.id) });
  const refazer = el("button", { class: "botao cta pequeno", type: "button", text: "↻ Refazer o vídeo" });
  refazer.addEventListener("click", async () => {
    refazer.disabled = true;
    refazer.replaceChildren(el("span", { class: "giro" }), "Enviando…");
    try {
      await postar(`/api/projetos/${p.id}/shorts/${s.indice}/refazer`, {
        gancho: gancho.value.trim(), ajuste_inicio: Number(ajusteIni.value) || 0, ajuste_fim: Number(ajusteFim.value) || 0,
        trilha: musica.value,
      });
      avisar(`Refazendo o Short ${s.indice}… leva cerca de 1 minuto.`, "ok");
      estado.abertos.delete(chave);
      estado.sujos.delete(p.id);
      fig.querySelector(".tela").append(sobreposicaoRefazendo());
      carregar();
    } catch (e) {
      avisar(e.message, "erro");
    } finally {
      refazer.disabled = false;
      refazer.textContent = "↻ Refazer o vídeo";
    }
  });
  const salvar = el("button", { class: "botao pequeno", type: "button", text: "Salvar descrição e tags" });
  salvar.addEventListener("click", async () => {
    try {
      const titulo = fig.querySelector('[data-campo="titulo"]');
      await api(`/api/projetos/${p.id}/metadados`, { method: "PUT", body: JSON.stringify({ shorts: [{
        indice: s.indice, titulo: titulo ? titulo.value : s.titulo, descricao: descricao.value,
        tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean),
        selecionado: (fig.querySelector('[data-campo="selecionado"]') || { checked: true }).checked,
      }] }) });
      estado.sujos.delete(p.id);
      avisar("Descrição e tags salvas.", "ok");
    } catch (e) { avisar(e.message, "erro"); }
  });
  return el("div", { class: `editor ${estado.abertos.has(chave) ? "" : "oculto"}` },
    el("label", { class: "campo" }, "Gancho (texto grande no topo)", gancho,
      el("span", { class: "dica", text: "Use *palavra* para pintar uma palavra na cor de destaque." })),
    el("div", { class: "lado-a-lado" },
      el("label", { class: "campo" }, "Começo (± segundos)", ajusteIni),
      el("label", { class: "campo" }, "Fim (± segundos)", ajusteFim)),
    el("label", { class: "campo" }, "Música", musica),
    el("div", { class: "acoes", style: "margin-top:0" }, refazer),
    el("label", { class: "campo" }, "Descrição do YouTube", descricao),
    el("label", { class: "campo" }, "Tags (separe por vírgula)", tags),
    el("div", { class: "acoes", style: "margin-top:0" }, salvar));
}

function edicoesDoCard(card) {
  return [...card.querySelectorAll(".short")].map((fig) => ({
    indice: Number(fig.dataset.indice),
    titulo: (fig.querySelector('[data-campo="titulo"]') || {}).value,
    selecionado: (fig.querySelector('[data-campo="selecionado"]') || { checked: true }).checked,
  }));
}

async function salvarTextos(p, card, silencioso = false) {
  try {
    await api(`/api/projetos/${p.id}/metadados`, { method: "PUT", body: JSON.stringify({ shorts: edicoesDoCard(card) }) });
    estado.sujos.delete(p.id);
    if (!silencioso) avisar("Textos salvos.", "ok");
    return true;
  } catch (e) { avisar(e.message, "erro"); return false; }
}

async function aprovar(p, card) {
  const shorts = edicoesDoCard(card);
  const marcados = shorts.filter((s) => s.selecionado).length;
  if (!marcados) return avisar("Marque pelo menos um Short para publicar.", "erro");
  if (!confirm(`Agendar ${marcados} Short(s) no YouTube?`)) return;
  try {
    if (p.estado === "erro_publicacao") {
      await salvarTextos(p, card, true);
      await postar(`/api/projetos/${p.id}/publicar`);
    } else {
      await postar(`/api/projetos/${p.id}/aprovar`, { shorts });
    }
    estado.sujos.delete(p.id);
    avisar("Enviando ao YouTube…", "ok");
    carregar();
  } catch (e) { avisar(e.message, "erro"); }
}

async function cancelar(p) {
  if (!confirm("Tirar estes Shorts da agenda? Eles ficam privados no YouTube (nada é apagado).")) return;
  try { const r = await postar(`/api/projetos/${p.id}/cancelar`); avisar(`${(r.privados || []).length} Short(s) tirados da agenda.`, "ok"); carregar(); }
  catch (e) { avisar(e.message, "erro"); }
}
async function tentar(p) {
  try { await postar(`/api/projetos/${p.id}/tentar`); avisar("Recomeçando…", "ok"); carregar(); }
  catch (e) { avisar(e.message, "erro"); }
}
async function descartar(p) {
  if (!confirm("Descartar estes cortes? Eles somem da lista e não serão publicados.")) return;
  try {
    await postar(`/api/projetos/${p.id}/descartar`);
    estado.sujos.delete(p.id);
    avisar("Cortes descartados.");
    await carregar();
    carregarLives(false);
  } catch (e) { avisar(e.message, "erro"); }
}

function destacarDoEndereco() {
  const m = location.hash.match(/^#\/projeto\/(.+)$/);
  if (!m) return;
  const card = document.getElementById(idCard(decodeURIComponent(m[1])));
  if (card && !card.classList.contains("destaque")) {
    document.querySelectorAll(".destaque").forEach((x) => x.classList.remove("destaque"));
    card.classList.add("destaque");
    card.scrollIntoView({ block: "start" });
  }
}

/* ------------------------------------------------------------ agenda e músicas */
function renderAgenda() {
  const lista = $("#lista-agenda");
  lista.replaceChildren();
  const itens = estado.dados.agenda.slice(0, 10);
  if (!itens.length) { lista.append(el("li", { class: "pequeno", text: "Nada agendado ainda." })); return; }
  for (const a of itens) {
    lista.append(el("li", {},
      el("span", { class: "data", text: dataCurta(a.quando) }),
      el("span", { class: "titulo" }, a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener", text: a.titulo }) : a.titulo)));
  }
}

let assinaturaMusicas = "";
function renderMusicas() {
  const c = canal();
  const trilhas = c ? c.trilhas || [] : [];
  const assinatura = JSON.stringify(trilhas);
  if (assinatura === assinaturaMusicas) return;  // não reinicia o player à toa
  assinaturaMusicas = assinatura;
  const lista = $("#lista-musicas");
  lista.replaceChildren();
  if (!trilhas.length) { lista.append(el("li", { class: "pequeno", text: "Nenhuma música ainda. Clique em “Compor trilhas originais”." })); return; }
  for (const t of trilhas) {
    lista.append(el("li", {},
      el("div", { class: "linha" },
        el("strong", { text: nomeMusica(t.nome) }),
        el("span", {}, t.original ? el("span", { class: "selo-original", text: "ORIGINAL" }) : null, " ",
          el("button", { class: "botao pequeno perigo", type: "button", text: "Remover", onclick: () => removerMusica(t) }))),
      el("audio", { src: t.url, controls: true, preload: "none" }),
      t.credito ? el("div", { class: "pequeno", text: `Crédito: ${t.credito}` }) : null));
  }
}

async function removerMusica(t) {
  if (!confirm(`Remover a música “${nomeMusica(t.nome)}” do canal?`)) return;
  try { await postar(`/api/canais/${canal().id}/trilhas/remover`, { nome: t.nome }); avisar("Música removida."); carregar(); }
  catch (e) { avisar(e.message, "erro"); }
}

async function enviarMusica(ev) {
  const arquivo = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!arquivo) return;
  const credito = prompt("Crédito da música para a descrição (deixe vazio se a licença não pedir):", "") || "";
  avisar("Enviando a música…");
  try {
    const r = await fetch(`/api/canais/${canal().id}/trilhas?nome=${encodeURIComponent(arquivo.name)}&credito=${encodeURIComponent(credito)}`,
      { method: "POST", body: arquivo, headers: { "Content-Type": "application/octet-stream" } });
    const corpo = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((corpo.detail && corpo.detail.mensagem) || "Não consegui enviar a música.");
    avisar("Música adicionada ao canal.", "ok");
    carregar();
  } catch (e) { avisar(e.message, "erro"); }
}

async function comporTrilhas() {
  const botao = $("#btn-compor");
  botao.disabled = true;
  botao.replaceChildren(el("span", { class: "giro" }), "Compondo…");
  try {
    const r = await postar(`/api/canais/${canal().id}/trilhas/gerar`);
    avisar(r.criadas.length ? `${r.criadas.length} trilha(s) nova(s) criada(s).` : "O canal já tem todas as trilhas originais.", "ok");
    carregar();
  } catch (e) { avisar(e.message, "erro"); }
  finally { botao.disabled = false; botao.textContent = "♫ Compor trilhas originais"; }
}

/* ------------------------------------------------------------ modo automático */
function renderAuto() {
  const c = canal();
  const vigia = $("#chave-vigia"), revisao = $("#chave-revisao");
  vigia.disabled = revisao.disabled = !c;
  if (!c) return;
  vigia.checked = c.automacao.ativa;
  const prox = proximaExecucao(c.automacao.proxima);
  $("#texto-vigia").textContent = c.automacao.ativa
    ? `Procura live nova a cada ${c.automacao.intervalo} min${prox ? ` (próxima: ${prox})` : ""} e corta sozinho.`
    : "Desligado: só corta as lives que você escolher aqui.";
  revisao.checked = !c.revisao;
  $("#texto-revisao").textContent = c.revisao ? "Desligado: os Shorts esperam você aprovar aqui." : "Os Shorts vão direto para a agenda do YouTube.";
}
async function alternar(caminho, corpo, chave) {
  chave.disabled = true;
  try { await postar(`/api/canais/${canal().id}/${caminho}`, corpo); avisar("Pronto, ajuste salvo.", "ok"); }
  catch (e) { chave.checked = !chave.checked; avisar(e.message, "erro"); }
  finally { chave.disabled = false; carregar(); }
}

/* ------------------------------------------------------------ YouTube */
function renderYoutube() {
  const c = canal();
  const corpo = $("#youtube-corpo");
  if (!c) return;
  const yt = c.youtube || {};
  if (estado.conectando && !yt.em_andamento && yt.conectado && !yt.erro) {
    estado.conectando = false;
    avisar(`YouTube conectado: ${yt.nome}`, "ok");
    carregarLives(true);
  }
  corpo.replaceChildren();
  if (yt.em_andamento && yt.url) {
    corpo.append(
      el("ol", {},
        el("li", { text: "Clique no botão abaixo (abre o Google numa aba nova)." }),
        el("li", { text: "Escolha a conta e o canal do YouTube." }),
        el("li", { text: "Se aparecer “O Google não verificou este app”, clique em Avançado → Acessar." }),
        el("li", { text: "Marque as permissões e clique em Continuar." })),
      el("a", { class: "botao cta", href: yt.url, target: "_blank", rel: "noopener", text: "Abrir a página do Google" }),
      el("p", { class: "pequeno", text: "Esta janela atualiza sozinha quando terminar." }));
  } else if (yt.em_andamento) {
    corpo.append(el("p", { class: "linha-progresso" }, el("span", { class: "giro" }), "Preparando o link do Google…"));
  } else {
    if (yt.erro) corpo.append(el("p", { class: "aviso erro", text: yt.erro }));
    corpo.append(yt.conectado
      ? el("p", {}, "Vinculado a ", el("strong", { text: yt.nome || "seu canal" }), yt.handle ? ` (${yt.handle})` : "", ". As lives dele aparecem na página.")
      : el("p", { text: "Conecte o canal do YouTube: as lives dele aparecem aqui para cortar e os Shorts são publicados nele." }));
    if (yt.conectado) corpo.append(el("p", { class: "pequeno", text: "Enquanto o app do Google estiver em modo Teste, a conexão vence a cada 7 dias. Se algo falhar, reconecte aqui." }));
    corpo.append(el("button", { class: "botao cta", type: "button", text: yt.conectado ? "Reconectar" : "Conectar canal do YouTube", onclick: conectarYoutube }));
  }
}
async function conectarYoutube() {
  try { estado.conectando = true; await postar(`/api/canais/${canal().id}/youtube`); carregar(); }
  catch (e) { estado.conectando = false; avisar(e.message, "erro"); }
}

/* ------------------------------------------------------------ início */
document.addEventListener("DOMContentLoaded", () => {
  $("#btn-atualizar-lives").addEventListener("click", () => carregarLives(true));
  $("#arquivo-musica").addEventListener("change", enviarMusica);
  $("#btn-compor").addEventListener("click", comporTrilhas);
  $("#chave-vigia").addEventListener("change", (ev) => alternar("automacao", { ativa: ev.target.checked }, ev.target));
  $("#chave-revisao").addEventListener("change", (ev) => alternar("revisao", { obrigatoria: !ev.target.checked }, ev.target));
  $("#canal-chip").addEventListener("click", () => { renderYoutube(); $("#dialogo-youtube").showModal(); });
  $("#btn-fechar-youtube").addEventListener("click", () => $("#dialogo-youtube").close());
  window.addEventListener("hashchange", destacarDoEndereco);
  $("#lista-lives").replaceChildren(...[1, 2, 3].map(() => el("div", { class: "esqueleto esqueleto-live" })));
  carregar();
});
