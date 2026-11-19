/* Painel de revisão — Esteira Canal Dark (JavaScript puro, sem build). */
"use strict";

const estado = { projetos: [], atual: null, dados: null, thumb: 1, timer: null, sujo: false, canais: [] };
const $ = (sel) => document.querySelector(sel);

function el(tag, props, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e && typeof v !== "string") e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : document.createTextNode(String(f)));
  return e;
}

function avisar(texto, tipo = "info") {
  const t = el("div", { class: `aviso-toast ${tipo}`, text: texto });
  $("#avisos").append(t);
  setTimeout(() => t.remove(), tipo === "erro" ? 9000 : 4500);
}

async function api(caminho, opcoes = {}) {
  const resp = await fetch(caminho, { headers: { "Content-Type": "application/json" }, ...opcoes });
  let corpo = null;
  try { corpo = await resp.json(); } catch (e) { corpo = null; }
  if (!resp.ok) throw new Error((corpo && corpo.detail) || `Erro ${resp.status}`);
  return corpo;
}

const tempo = (s) => {
  if (s === null || s === undefined) return "–";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(x).padStart(2, "0");
};
const dataCurta = (iso) => { try { return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }); } catch (e) { return iso || ""; } };
const MODOS = { cartao_animado: "Cartão animado", video_banco: "Vídeo de banco", imagem_ia: "Imagem IA", ilustracao_2d: "Ilustração 2D", tipografia: "Tipografia" };

/* ------------------------------------------------------------------ lista */
async function carregarCanais() {
  try {
    const canais = await api("/api/canais");
    estado.canais = canais;
    const sel = $("#filtro-canal");
    canais.forEach((c) => sel.append(el("option", { value: c.id, text: `${c.nome} (${c.fila} na fila)` })));
  } catch (e) { avisar("Não foi possível carregar os canais: " + e.message, "erro"); }
}

async function carregarLista() {
  const params = new URLSearchParams();
  if ($("#filtro-canal").value) params.set("canal", $("#filtro-canal").value);
  if ($("#filtro-estado").value) params.set("estado", $("#filtro-estado").value);
  try {
    estado.projetos = await api("/api/projetos?" + params.toString());
  } catch (e) { avisar("Erro ao listar projetos: " + e.message, "erro"); return; }
  const lista = $("#lista");
  lista.replaceChildren();
  if (!estado.projetos.length) { lista.append(el("p", { class: "vazio", text: "Nenhum projeto com este filtro." })); return; }
  estado.projetos.forEach((p) => {
    const capa = p.capa ? el("img", { src: p.capa, alt: "", loading: "lazy" }) : el("div", { class: "sem-capa" });
    lista.append(el("button", {
      class: "item-projeto" + (estado.atual === p.id ? " ativo" : ""), "data-id": p.id,
      onclick: () => { location.hash = "#/projeto/" + p.id; $("#lateral").classList.remove("aberta"); },
    }, capa, el("div", {},
      el("div", { class: "titulo", text: p.titulo || p.tema }),
      el("div", { class: "meta" }, el("span", { class: `estado estado-${p.estado}`, text: p.estado_texto })),
      el("div", { class: "meta", text: `${p.canal} • ${dataCurta(p.criado_em)}${p.duracao ? " • " + tempo(p.duracao) : ""}` }))));
  });
}

/* ------------------------------------------------------------- detalhes */
async function abrirProjeto(id, silencioso = false) {
  estado.atual = id;
  let dados;
  try { dados = await api("/api/projetos/" + id); }
  catch (e) { if (!silencioso) avisar("Projeto não encontrado: " + e.message, "erro"); return; }
  const primeiraVez = !estado.dados || estado.dados.resumo.id !== id;
  estado.dados = dados;
  $("#boas-vindas").classList.add("oculto");
  $("#revisao").classList.remove("oculto");
  document.querySelectorAll(".item-projeto").forEach((b) => b.classList.toggle("ativo", b.dataset.id === id));
  renderCabecalho(dados);
  renderDecisao(dados);
  if (primeiraVez || !estado.sujo) {
    renderVideo(dados, primeiraVez);
    renderShorts(dados);
    renderThumbs(dados);
    renderMetadados(dados);
    renderFontes(dados);
    renderCenas(dados);
    estado.sujo = false;
  }
  renderEventos(dados);
  agendarAtualizacao(dados);
}

function renderCabecalho(d) {
  $("#p-id").textContent = d.resumo.id;
  $("#p-titulo").textContent = d.metadados.titulo || d.resumo.tema;
  const est = $("#p-estado");
  est.className = `estado estado-${d.status.estado}`;
  est.textContent = d.estados[d.status.estado] || d.status.estado;
  const cortes = d.tipo === "cortes";
  const shorts = d.shorts || [];
  $("#p-duracao").textContent = cortes
    ? `${shorts.length} Short(s) • ${tempo(shorts.reduce((t, s) => t + (s.duracao || 0), 0))} no total`
    : "Duração " + tempo(d.metadados.duracao_segundos);
  const loud = (d.status.etapas && d.status.etapas.montagem && d.status.etapas.montagem.loudness) || null;
  $("#p-loud").textContent = cortes ? "Áudio -14 LUFS" : loud ? `${loud.integrado_lufs} LUFS • pico ${loud.pico_dbtp} dBTP` : "Loudness –";
  const alertas = $("#alertas");
  alertas.replaceChildren();
  (d.status.alertas || []).forEach((a) => alertas.append(el("div", { class: "alerta" }, el("b", { text: a.etapa + ": " }), a.mensagem)));
  if (d.status.detalhe_estado && ["erro", "erro_publicacao"].includes(d.status.estado))
    alertas.append(el("div", { class: "alerta" }, el("b", { text: "erro: " }), d.status.detalhe_estado));
}

function renderDecisao(d) {
  const st = d.status.estado;
  const podeDecidir = ["pendente_revisao", "reprovado", "erro_publicacao"].includes(st);
  $("#btn-aprovar").disabled = !podeDecidir || !d.video;
  $("#btn-reprovar").disabled = !["pendente_revisao", "reprovado", "erro_publicacao", "aprovado"].includes(st);
  $("#btn-salvar").disabled = ["publicando", "agendado"].includes(st);
  // envio interrompido (ex.: painel fechado no meio do upload): permite retomar
  const interrompido = st === "publicando" && !d.publicando;
  $("#btn-republicar").classList.toggle("oculto", !(st === "erro_publicacao" || interrompido));
  const textos = {
    pendente_revisao: "Revise o vídeo, os Shorts, a capa e os textos. Ao aprovar, o envio agendado ao YouTube começa automaticamente.",
    reprovado: "Este vídeo foi reprovado. Peça ao agente de IA para aplicar a correção; ele volta para cá quando estiver pronto.",
    aprovado: "Aprovado. O envio ao YouTube está sendo preparado…",
    publicando: "Enviando ao YouTube… você pode fechar esta página; o envio continua.",
    agendado: "Tudo certo! O vídeo e os Shorts estão agendados no YouTube.",
    erro_publicacao: "A publicação falhou. Veja o alerta acima, corrija (ex.: conectar o canal) e tente novamente.",
    em_producao: "Este vídeo ainda está sendo produzido.",
    aguardando_roteiro: "Aguardando o agente de IA escrever o roteiro.",
  };
  if (d.tipo === "cortes") Object.assign(textos, {
    pendente_revisao: "Assista aos Shorts, ajuste títulos, descrições e tags e desmarque os que não devem subir. Ao aprovar, cada Short é agendado no YouTube nos horários do canal.",
    reprovado: "Reprovado. Peça ao agente de IA para ajustar os cortes; eles voltam para cá quando estiverem prontos.",
    agendado: "Tudo certo! Os Shorts estão agendados no YouTube.",
    em_producao: "A equipe ainda está trabalhando nesta live (download, análise ou edição).",
    aguardando_roteiro: "Aguardando o agente de IA escolher os melhores momentos da live.",
  });
  $("#decisao-texto").textContent = textos[st] || "";
  const yt = $("#youtube");
  yt.replaceChildren();
  const info = d.status.youtube || {};
  if (info.video) yt.append(el("div", {}, "🎬 Vídeo: ", el("a", { href: info.video.url, target: "_blank", rel: "noopener", text: info.video.url }), ` — ${dataCurta(info.video.publicar_em)}`));
  (info.shorts || []).forEach((s) => yt.append(el("div", {}, `📱 Short ${s.indice}: `, el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.url }), ` — ${dataCurta(s.publicar_em)}`)));
  const rep = $("#reprovacoes");
  rep.replaceChildren();
  (d.status.reprovacoes || []).slice(-4).reverse().forEach((r) => rep.append(el("div", { class: "reprovacao" + (r.resolvida ? " resolvida" : "") },
    el("b", { text: (r.resolvida ? "Corrigido" : "Reprovado") + " em " + dataCurta(r.em) + ": " }), r.motivo)));
}

function renderVideo(d, recarregar) {
  const v = $("#video");
  const cortes = d.tipo === "cortes";
  v.closest(".cartao").classList.toggle("oculto", !d.video);
  $("#thumbs").closest(".cartao").classList.toggle("oculto", cortes);
  $("#m-titulo").closest(".cartao").classList.toggle("oculto", cortes);
  $("#cenas").closest(".cartao").classList.toggle("oculto", !d.cenas.length);
  const origem = $("#origem-live") || el("p", { id: "origem-live", class: "origem-live" });
  origem.replaceChildren();
  if (cortes && d.live) {
    origem.append("Live original: ", el("a", { href: d.live.url, target: "_blank", rel: "noopener", text: d.live.titulo || d.live.url }),
      ` — ${d.fonte ? d.fonte.nome + " (" + (d.fonte.tipo === "proprio" ? "canal próprio" : "fonte autorizada") + ")" : ""}`);
    $("#p-titulo").after(origem);
  } else origem.remove();
  if (!d.video) { v.removeAttribute("src"); v.load(); return; }
  if (recarregar || v.dataset.src !== d.video) { v.src = d.video; v.dataset.src = d.video; }
}

function renderShorts(d) {
  const caixa = $("#shorts");
  caixa.replaceChildren();
  $("#n-shorts").textContent = d.shorts.length ? `(${d.shorts.length} gerado${d.shorts.length > 1 ? "s" : ""} — marque os que serão publicados)` : "(nenhum Short gerado)";
  d.shorts.forEach((s) => {
    caixa.append(el("div", { class: "short" },
      el("video", { src: s.url + "#t=1", controls: true, preload: "metadata", playsinline: true }),
      el("label", { class: "linha" }, el("input", { type: "checkbox", checked: s.selecionado, "data-short": s.indice, onchange: () => (estado.sujo = true) }),
        `Publicar • ${tempo(s.duracao)} • ` + (s.blocos && s.blocos.length ? `blocos ${s.blocos[0]}–${s.blocos[s.blocos.length - 1]}` : `aos ${tempo(s.inicio)} da live`)),
      el("input", { value: s.titulo, maxlength: 100, "data-short-titulo": s.indice, title: "Título", oninput: () => (estado.sujo = true) }),
      d.tipo === "cortes" ? el("textarea", { rows: 5, "data-short-desc": s.indice, title: "Descrição", oninput: () => (estado.sujo = true) }, s.descricao || "") : null,
      d.tipo === "cortes" ? el("input", { value: (s.tags || []).join(", "), "data-short-tags": s.indice, title: "Tags (separe por vírgula)", oninput: () => (estado.sujo = true) }) : null));
  });
}

function renderThumbs(d) {
  const caixa = $("#thumbs");
  caixa.replaceChildren();
  estado.thumb = Number(d.metadados.thumbnail_escolhida || 1);
  d.thumbnails.forEach((t) => {
    const botao = el("button", { class: "thumb" + (t.indice === estado.thumb ? " escolhida" : ""), type: "button", title: t.frase,
      onclick: () => { estado.thumb = t.indice; estado.sujo = true; document.querySelectorAll(".thumb").forEach((b) => b.classList.toggle("escolhida", b === botao)); } },
      el("img", { src: t.url, alt: "Capa " + t.indice }), el("span", { class: "selo", text: `${t.indice} • ${t.template}` }));
    caixa.append(botao);
  });
  if (!d.thumbnails.length) caixa.append(el("p", { class: "vazio", text: "Nenhuma capa gerada ainda." }));
}

function renderMetadados(d) {
  const m = d.metadados;
  $("#m-titulo").value = m.titulo || "";
  $("#m-descricao").value = m.descricao_base || "";
  $("#m-tags").value = (m.tags || []).join(", ");
  $("#m-hashtags").value = (m.hashtags || []).join(", ");
  $("#m-final").textContent = m.descricao_final || "";
  const caps = $("#m-capitulos");
  caps.replaceChildren();
  (m.capitulos || []).forEach((c, i) => caps.append(el("div", { class: "capitulo" }, el("code", { text: c.tempo || "–" }),
    el("input", { value: c.titulo, "data-capitulo": i, "data-bloco": c.bloco, oninput: () => (estado.sujo = true) }))));
  $("#aviso-capitulos").textContent = m.capitulos_validos ? "(válidos para o YouTube)" : (m.aviso_capitulos ? "— " + m.aviso_capitulos : "");
  const frases = $("#m-frases");
  frases.replaceChildren();
  (m.frases_thumbnail || []).forEach((f, i) => frases.append(el("input", { value: f, "data-frase": i, oninput: () => (estado.sujo = true) })));
  contarCampos();
}

function contarCampos() {
  $("#cont-titulo").textContent = `(${$("#m-titulo").value.length}/100)`;
  $("#cont-tags").textContent = $("#m-tags").value.length;
}

function renderFontes(d) {
  const ol = $("#fontes");
  ol.replaceChildren();
  if (!d.fontes.length) ol.append(el("li", { class: "vazio", text: "Nenhuma fonte registrada." }));
  d.fontes.forEach((f) => ol.append(el("li", { class: f.citada ? "citada" : "" }, el("a", { href: f.url, target: "_blank", rel: "noopener noreferrer", text: f.titulo || f.url }))));
}

function renderEventos(d) {
  const ul = $("#eventos");
  ul.replaceChildren();
  (d.status.eventos || []).forEach((ev) => ul.append(el("li", {}, el("time", { text: dataCurta(ev.em) }), ev.mensagem)));
}

function renderCenas(d) {
  const caixa = $("#cenas");
  caixa.replaceChildren();
  d.cenas.forEach((c) => {
    const tags = [el("span", { class: "num", text: `Cena ${c.id}` }), el("span", { class: "tag", text: MODOS[c.modo] || c.modo })];
    if (c.inicio !== null && c.inicio !== undefined) tags.push(el("span", { class: "tag", text: `${tempo(c.inicio)} • ${(c.duracao || 0).toFixed(1)}s` }));
    if (c.potencial_short) tags.push(el("span", { class: "tag viral", text: "viral" }));
    if (c.wer !== null && c.wer !== undefined) tags.push(el("span", { class: "tag" + (c.alerta_pronuncia ? " ruim" : ""), text: `pronúncia ${(100 - c.wer * 100).toFixed(0)}%` }));
    caixa.append(el("div", { class: "cena" },
      c.previa ? el("img", { src: c.previa, alt: "", loading: "lazy", title: "Ir para esta cena no vídeo", onclick: () => irPara(c.inicio) }) : null,
      el("div", { class: "corpo" },
        el("div", { class: "topo-cena" }, tags),
        c.capitulo ? el("div", { class: "tag", text: "📑 " + c.capitulo }) : null,
        c.destaque ? el("div", { class: "destaque", text: "“" + c.destaque + "”" }) : null,
        el("div", { class: "texto", text: c.narracao }),
        el("button", { class: "ajustar", type: "button", text: "Ajustar esta cena", onclick: () => abrirReprovacao(`Cena ${c.id}: `) }))));
  });
}

function irPara(segundos) {
  if (segundos === null || segundos === undefined) return;
  const v = $("#video");
  v.currentTime = segundos + 0.05;
  v.scrollIntoView({ behavior: "smooth", block: "center" });
  v.play().catch(() => {});
}

function agendarAtualizacao(d) {
  clearTimeout(estado.timer);
  if (["aprovado", "publicando", "em_producao"].includes(d.status.estado) || d.publicando)
    estado.timer = setTimeout(() => abrirProjeto(d.resumo.id, true).then(carregarLista), 3000);
}

/* --------------------------------------------------------------- edições */
function coletarEdicoes() {
  const d = estado.dados;
  const lista = (txt) => txt.split(",").map((s) => s.trim().replace(/^#/, "")).filter(Boolean);
  const capitulos = Array.from(document.querySelectorAll("[data-capitulo]")).map((i) => ({ bloco: Number(i.dataset.bloco), titulo: i.value.trim() }));
  const frases = Array.from(document.querySelectorAll("[data-frase]")).map((i) => i.value.trim());
  const shorts = d.shorts.map((s) => {
    const item = { indice: s.indice, selecionado: document.querySelector(`[data-short="${s.indice}"]`).checked,
      titulo: document.querySelector(`[data-short-titulo="${s.indice}"]`).value.trim() };
    const desc = document.querySelector(`[data-short-desc="${s.indice}"]`), tags = document.querySelector(`[data-short-tags="${s.indice}"]`);
    if (desc) item.descricao = desc.value;
    if (tags) item.tags = lista(tags.value);
    return item;
  });
  return { titulo: $("#m-titulo").value.trim(), descricao_base: $("#m-descricao").value, tags: lista($("#m-tags").value),
    hashtags: lista($("#m-hashtags").value), capitulos, frases_thumbnail: frases, thumbnail_escolhida: estado.thumb, shorts };
}

async function salvar() {
  try {
    const r = await api(`/api/projetos/${estado.atual}/metadados`, { method: "PUT", body: JSON.stringify(coletarEdicoes()) });
    estado.sujo = false;
    $("#m-final").textContent = r.metadados.descricao_final || "";
    avisar("Alterações salvas.", "ok");
  } catch (e) { avisar("Erro ao salvar: " + e.message, "erro"); }
}

async function aprovar() {
  const titulo = $("#m-titulo").value.trim();
  const cortes = estado.dados.tipo === "cortes";
  if (!titulo && !cortes) { avisar("O título não pode ficar vazio.", "erro"); return; }
  const marcados = document.querySelectorAll("[data-short]:checked").length;
  if (cortes && !marcados) { avisar("Marque pelo menos um Short para publicar.", "erro"); return; }
  if (!confirm(cortes ? `Aprovar e agendar ${marcados} Short(s) no YouTube?` : `Aprovar “${titulo}” e agendar no YouTube com a capa ${estado.thumb}?`)) return;
  $("#btn-aprovar").disabled = true;
  try {
    await api(`/api/projetos/${estado.atual}/aprovar`, { method: "POST", body: JSON.stringify(coletarEdicoes()) });
    estado.sujo = false;
    avisar("Aprovado! O envio agendado ao YouTube começou.", "ok");
    await abrirProjeto(estado.atual, true);
    carregarLista();
  } catch (e) { avisar("Não foi possível aprovar: " + e.message, "erro"); $("#btn-aprovar").disabled = false; }
}

async function republicar() {
  try {
    await api(`/api/projetos/${estado.atual}/publicar`, { method: "POST" });
    avisar("Nova tentativa de publicação iniciada.", "ok");
    await abrirProjeto(estado.atual, true);
  } catch (e) { avisar(e.message, "erro"); }
}

function abrirReprovacao(prefixo = "") {
  const dlg = $("#dialogo-reprovar");
  $("#motivo").value = prefixo;
  dlg.querySelectorAll(".etapas-sel input").forEach((c) => (c.checked = false));
  dlg.showModal();
  $("#motivo").focus();
}

async function confirmarReprovacao(ev) {
  ev.preventDefault();
  const motivo = $("#motivo").value.trim();
  if (motivo.length < 5) { avisar("Descreva o ajuste com um pouco mais de detalhe.", "erro"); return; }
  const etapas = Array.from(document.querySelectorAll(".etapas-sel input:checked")).map((c) => c.value);
  const blocos = Array.from(motivo.matchAll(/cena\s*(\d+)/gi)).map((m) => Number(m[1]));
  try {
    if (estado.sujo) await api(`/api/projetos/${estado.atual}/metadados`, { method: "PUT", body: JSON.stringify(coletarEdicoes()) });
    const r = await api(`/api/projetos/${estado.atual}/reprovar`, { method: "POST", body: JSON.stringify({ motivo, etapas, blocos }) });
    $("#dialogo-reprovar").close();
    avisar("Reprovação registrada. Peça ao agente: “corrija o vídeo reprovado”. " + (r.reprovacao.sugestao_agente || ""), "ok");
    estado.sujo = false;
    await abrirProjeto(estado.atual, true);
    carregarLista();
  } catch (e) { avisar("Erro ao reprovar: " + e.message, "erro"); }
}

/* --------------------------------------------------------- equipe de cortes */
function renderConexao() {
  const c = estado.canais.find((x) => x.id === $("#live-canal").value);
  const caixa = $("#conexao-youtube");
  caixa.replaceChildren();
  $("#btn-enviar-live").disabled = !c;
  $("#btn-conectar").disabled = !c || !!(c.youtube || {}).em_andamento;
  if (!c) {
    caixa.textContent = "Ainda não há canal de cortes. Peça ao agente de IA: \"crie um canal de cortes\".";
    return;
  }
  const yt = c.youtube || {};
  $("#btn-conectar").textContent = yt.conectado ? "Trocar canal do YouTube" : "Conectar canal do YouTube";
  if (yt.em_andamento) caixa.textContent = "Aguardando você autorizar o canal na janela do Google que abriu…";
  else if (yt.erro) caixa.textContent = "Não foi possível conectar: " + yt.erro;
  else if (yt.conectado) caixa.append("YouTube conectado: ", el("b", { text: yt.nome || "canal autorizado" }));
  else caixa.textContent = "Nenhum canal do YouTube conectado ainda. Conecte para a IA conseguir publicar depois da sua aprovação.";
}

async function abrirLive() {
  await carregarCanais2();
  const sel = $("#live-canal");
  sel.replaceChildren();
  estado.canais.filter((c) => c.tipo === "cortes").forEach((c) => sel.append(el("option", { value: c.id, text: c.nome })));
  renderConexao();
  $("#dialogo-live").showModal();
}

async function carregarCanais2() { try { estado.canais = await api("/api/canais"); } catch (e) { /* mantém a lista anterior */ } }

async function conectarYoutube() {
  const canal = $("#live-canal").value;
  if (!canal) return;
  try {
    await api(`/api/canais/${canal}/youtube`, { method: "POST" });
    avisar("Uma janela do Google vai abrir: escolha a conta do canal e autorize.", "ok");
    const vigiar = setInterval(async () => {
      await carregarCanais2();
      renderConexao();
      const c = estado.canais.find((x) => x.id === canal);
      if (!c || !c.youtube.em_andamento) clearInterval(vigiar);
    }, 3000);
  } catch (e) { avisar(e.message, "erro"); }
}

async function enviarLive(ev) {
  ev.preventDefault();
  const corpo = { canal: $("#live-canal").value, url: $("#live-url").value.trim(), trecho: $("#live-trecho").value.trim() };
  try {
    const r = await api("/api/cortes/processar", { method: "POST", body: JSON.stringify(corpo) });
    $("#dialogo-live").close();
    $("#live-url").value = ""; $("#live-trecho").value = "";
    avisar(r.mensagem, "ok");
  } catch (e) { avisar(e.message, "erro"); }
}

/* --------------------------------------------------------------- roteador */
function rota() {
  const m = location.hash.match(/^#\/projeto\/([\w-]+\/[\w-]+)/);
  if (m) abrirProjeto(m[1]);
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#filtro-canal").addEventListener("change", carregarLista);
  $("#filtro-estado").addEventListener("change", carregarLista);
  $("#atualizar").addEventListener("click", () => { carregarLista(); if (estado.atual) abrirProjeto(estado.atual, true); });
  $("#abrir-lista").addEventListener("click", () => $("#lateral").classList.toggle("aberta"));
  $("#btn-salvar").addEventListener("click", salvar);
  $("#btn-aprovar").addEventListener("click", aprovar);
  $("#btn-reprovar").addEventListener("click", () => abrirReprovacao());
  $("#btn-republicar").addEventListener("click", republicar);
  $("#btn-nova-live").addEventListener("click", abrirLive);
  $("#cancelar-live").addEventListener("click", () => $("#dialogo-live").close());
  $("#btn-conectar").addEventListener("click", conectarYoutube);
  $("#live-canal").addEventListener("change", renderConexao);
  $("#form-live").addEventListener("submit", enviarLive);
  $("#cancelar-reprovar").addEventListener("click", () => $("#dialogo-reprovar").close());
  $("#form-reprovar").addEventListener("submit", confirmarReprovacao);
  document.querySelectorAll(".sugestoes button").forEach((b) => b.addEventListener("click", () => { $("#motivo").value += b.dataset.texto; $("#motivo").focus(); }));
  ["#m-titulo", "#m-descricao", "#m-tags", "#m-hashtags"].forEach((s) => $(s).addEventListener("input", () => { estado.sujo = true; contarCampos(); }));
  window.addEventListener("hashchange", rota);
  window.addEventListener("beforeunload", (e) => { if (estado.sujo) { e.preventDefault(); e.returnValue = ""; } });
  await carregarCanais();
  if (location.hash.startsWith("#/projeto/")) $("#filtro-estado").value = "";
  await carregarLista();
  rota();
});
