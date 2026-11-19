/* "Suas lives": cada live cortada vira um cartão com o pipeline visual (Baixar → Momentos → Editar → Legenda →
   Textos → Revisão → Publicar), a barra de progresso real e o painel da etapa escolhida. Nada de log bruto na tela. */
import { $, ROTULOS, api, avisar, comCarregando, confirmar, el, postar, projetoUrl } from "./base.js";
import { ESTADOS_TRABALHANDO, canal, estado } from "./estado.js";
import { seletorModelo } from "./lives.js";
import { painelLegenda } from "./legenda.js";
import { painelMomentos } from "./momentos.js";
import { atualizarBarra, barraProgresso } from "./progresso.js";
import { edicoes, painelTextos, salvarTextos, sobreposicaoRefazendo, temRascunho } from "./textos.js";

export const ETAPAS = [
  { id: "baixar", nome: "Baixar", icone: "⬇" }, { id: "momentos", nome: "Momentos", icone: "◎" },
  { id: "editar", nome: "Editar", icone: "✂" }, { id: "legenda", nome: "Legenda", icone: "≡" },
  { id: "textos", nome: "Textos", icone: "✎" },
  { id: "revisao", nome: "Revisão", icone: "✓" }, { id: "publicar", nome: "Publicar", icone: "▶" },
];
const EDITAVEIS = ["aguardando_etapa", "pendente_revisao", "reprovado", "erro"];
const APROVADOS = ["aprovado", "publicando", "erro_publicacao", "agendado"];  // a gestão fica na aba Agendados
const assinaturas = new Map();
let recarregar = () => {};
export function quandoPrecisarRecarregar(fn) { recarregar = fn; }

/* ------------------------------------------------------------ situação do pipeline */
export function situacao(p) {
  const feitas = new Set();
  if (p.etapa >= 1) feitas.add("baixar");
  if (p.etapa >= 2) feitas.add("momentos");
  if (p.etapa >= 3 && !p.edicao_desatualizada && p.shorts.length) { feitas.add("editar"); feitas.add("legenda"); feitas.add("textos"); }
  if (["aprovado", "publicando", "agendado"].includes(p.estado)) { feitas.add("revisao"); }
  if (p.estado === "agendado") feitas.add("publicar");
  const atual = (ETAPAS.find((e) => !feitas.has(e.id)) || ETAPAS[ETAPAS.length - 1]).id;
  const rodando = p.trabalhando || ESTADOS_TRABALHANDO.includes(p.estado) || p.publicando;
  return { feitas, atual, rodando };
}

function etapaAberta(p, sit) {
  if (estado.abertos.has(p.id)) return estado.abertos.get(p.id);
  if (p.estado === "pendente_revisao" || p.estado === "reprovado") return "textos";
  if (p.estado === "agendado" || p.estado === "erro_publicacao") return "publicar";
  return sit.atual;
}

/* ------------------------------------------------------------ lista */
export function renderProjetos() {
  const lista = $("#lista-projetos");
  const d = estado.dados;
  // aqui ficam só as lives em edição: as aprovadas (agendadas ou esperando o envio) vão para a aba Agendados
  const projetos = d.projetos.filter((p) => !APROVADOS.includes(p.estado));
  const aprovadas = d.projetos.length - projetos.length;
  const link = $("#link-agendados");
  if (link) {
    link.classList.toggle("oculto", !aprovadas);
    link.textContent = `${aprovadas} live(s) aprovada(s), agendadas ou na fila de envio → aba Agendados`;
  }
  const comecando = d.envios.filter((e) => !estado.dispensados.has(e.id) && !(e.projeto && projetos.some((p) => p.id === e.projeto))
    && e.estado !== "concluido");
  $("#qtd-projetos").textContent = String(projetos.length);
  if (!projetos.length && !comecando.length) {
    if (!lista.querySelector(".vazio")) lista.replaceChildren(el("div", { class: "vazio" },
      el("strong", { text: "Nenhuma live cortada ainda" }), "Escolha uma live acima e toque em ✂ Cortar esta live."));
    assinaturas.clear();
    return;
  }
  lista.querySelector(".vazio")?.remove();
  for (const no of lista.querySelectorAll(":scope > .esqueleto")) no.remove();  // placeholder da primeira carga
  for (const no of lista.querySelectorAll("[data-envio]")) no.remove();
  for (const e of comecando) lista.prepend(cardComecando(e));
  const ids = new Set(projetos.map((p) => p.id));
  for (const card of lista.querySelectorAll("[data-projeto]")) {
    if (!ids.has(card.dataset.projeto)) { card.remove(); assinaturas.delete(card.dataset.projeto); }
  }
  let anterior = lista.querySelector("[data-envio]:last-of-type");
  for (const p of projetos) {
    let card = lista.querySelector(`[data-projeto="${CSS.escape(p.id)}"]`);
    const assinatura = JSON.stringify({ ...p, progresso: null, atualizado_em: null,
      shorts: p.shorts.map((s) => ({ ...s, refazendo: false })) });
    const emUso = card && (card.contains(document.activeElement) || temRascunho(p) || card.querySelector(".momentos-painel[data-sujo], .legenda-painel[data-sujo]"));
    if (!card || (assinaturas.get(p.id) !== assinatura && !emUso)) {
      const novo = cardProjeto(p);
      if (card) card.replaceWith(novo); else lista.append(novo);
      card = novo;
      assinaturas.set(p.id, assinatura);
    } else {
      atualizarAoVivo(card, p);
    }
    if (anterior ? anterior.nextElementSibling !== card : lista.firstElementChild !== card) {
      if (anterior) anterior.after(card); else lista.prepend(card);
    }
    anterior = card;
  }
  destacarDoEndereco();
}

function cardComecando(e) {
  const falhou = e.estado === "erro";
  return el("article", { class: `card projeto ${falhou ? "falhou" : ""}`, "data-envio": e.id },
    el("div", { class: "projeto-topo" },
      el("div", {}, el("h3", { text: e.titulo || "Live enviada" }), el("div", { class: "pequeno", text: e.dono || "" })),
      el("span", { class: `pill ${falhou ? "erro" : "em_producao"}`, text: falhou ? "Não deu certo" : "Começando" })),
    barraProgresso(e.progresso, { erro: falhou }),
    falhou ? el("div", { class: "acoes" }, el("button", { class: "botao pequeno", type: "button", text: "Dispensar",
      onclick: () => { estado.dispensados.add(e.id); renderProjetos(); } })) : null);
}

/* só o que muda sem você mexer: barra, rótulo e "refazendo" (não apaga o que você está digitando) */
function atualizarAoVivo(card, p) {
  const sit = situacao(p);
  const barra = card.querySelector(".progresso");
  if (barra) atualizarBarra(barra, p.progresso);
  for (const s of p.shorts) {
    const tela = card.querySelector(`.short[data-indice="${s.indice}"] .tela`);
    if (!tela) continue;
    const atual = tela.querySelector(".refazendo");
    if (s.refazendo && !atual) tela.append(sobreposicaoRefazendo());
    if (!s.refazendo && atual) atual.remove();
  }
  card.querySelectorAll(".etapa-botao").forEach((b) => b.classList.toggle("rodando", sit.rodando && b.dataset.etapa === sit.atual));
}

/* ------------------------------------------------------------ cartão de uma live */
function cardProjeto(p) {
  const sit = situacao(p);
  const aberta = etapaAberta(p, sit);
  const card = el("article", { class: "card projeto", "data-projeto": p.id, id: `projeto-${p.id.replace(/[^\w-]/g, "_")}` });
  const quando = new Date(p.criado_em);
  const subtitulo = [p.live.canal || p.fonte.nome, isNaN(quando) ? "" : `cortada em ${quando.toLocaleDateString("pt-BR")}`,
    p.trecho ? `trecho ${p.trecho.replace("-", " até ")}` : "",
    p.rodada > 1 ? `rodada ${p.rodada} (mais Shorts do mesmo trecho)` : ""].filter(Boolean).join(" · ");
  card.append(el("div", { class: "projeto-topo" },
    el("div", {},
      el("h3", {}, p.live.url ? el("a", { href: p.live.url, target: "_blank", rel: "noopener", text: p.live.titulo || "Live" }) : (p.live.titulo || "Live")),
      el("div", { class: "pequeno", text: subtitulo })),
    el("span", { class: `pill ${p.estado}`, text: ROTULOS[p.estado] || p.estado_texto })));
  if (sit.rodando || p.estado === "erro") card.append(barraProgresso(p.progresso, { erro: p.estado === "erro" }));
  else if (p.estado === "aguardando_etapa") card.append(el("div", { class: "aviso dica-etapa", text: `👉 ${p.detalhe || "Esperando você decidir a próxima etapa."}` }));
  if (p.estado === "erro_publicacao" && p.detalhe) card.append(el("div", { class: "aviso erro", text: p.detalhe }));

  const painel = el("div", { class: "etapa-painel" });
  const trilha = el("ol", { class: "pipeline" }, ETAPAS.map((etapa, i) => {
    const botao = el("button", { type: "button", "data-etapa": etapa.id,
      class: `etapa-botao ${sit.feitas.has(etapa.id) ? "feita" : ""} ${etapa.id === sit.atual ? "atual" : ""} ${etapa.id === aberta ? "aberta" : ""} ${sit.rodando && etapa.id === sit.atual ? "rodando" : ""}`,
      "aria-pressed": String(etapa.id === aberta) },
      el("span", { class: "bolinha", text: sit.feitas.has(etapa.id) ? "✓" : etapa.icone }),
      el("span", { class: "nome", text: `${i + 1}. ${etapa.nome}` }));
    botao.addEventListener("click", () => {
      estado.abertos.set(p.id, etapa.id);
      trilha.querySelectorAll(".etapa-botao").forEach((b) => { b.classList.toggle("aberta", b === botao); b.setAttribute("aria-pressed", String(b === botao)); });
      abrirEtapa(painel, p, etapa.id);
    });
    return el("li", {}, botao);
  }));
  card.append(trilha, painel);
  abrirEtapa(painel, p, aberta);
  const rodape = el("div", { class: "acoes rodape" });
  if (p.estado !== "agendado" && !sit.rodando) {
    rodape.append(el("button", { class: "botao pequeno perigo", type: "button", text: "Descartar esta live", onclick: () => descartar(p) }));
  }
  card.append(rodape);
  return card;
}

async function abrirEtapa(painel, p, etapa) {
  const editavel = EDITAVEIS.includes(p.estado) && !p.trabalhando;
  const aoMudar = (proxima) => { if (proxima) estado.abertos.set(p.id, proxima); recarregar(); };
  painel.replaceChildren(el("div", { class: "esqueleto", style: "height:80px" }));
  let conteudo;
  if (etapa === "baixar") conteudo = painelBaixar(p);
  else if (etapa === "momentos") conteudo = await painelMomentos(p, aoMudar);
  else if (etapa === "editar") conteudo = painelEditar(p, editavel, aoMudar);
  else if (etapa === "legenda") conteudo = painelLegenda(p, aoMudar);
  else if (etapa === "textos") conteudo = painelTextos(p, ["pendente_revisao", "reprovado", "erro_publicacao"].includes(p.estado), aoMudar);
  else if (etapa === "revisao") conteudo = await painelRevisao(p);
  else conteudo = painelPublicar(p, aoMudar);
  painel.replaceChildren(conteudo);
}

/* ------------------------------------------------------------ etapas simples */
function painelBaixar(p) {
  const feito = p.etapa >= 1;
  const caixa = el("div", { class: "etapa-texto" },
    el("p", { text: feito ? "✓ A live foi baixada com o chat e as legendas da plataforma." : p.estado === "erro"
      ? p.detalhe || "O download não terminou." : "O olheiro está baixando a live." }),
    p.live.duracao ? el("p", { class: "pequeno", text: `Duração da live: ${Math.round(p.live.duracao / 60)} min` }) : null);
  if (p.estado === "erro") {
    const botao = el("button", { class: "botao turquesa", type: "button", text: "↻ Tentar de novo" });
    botao.addEventListener("click", () => comCarregando(botao, "Recomeçando…", async () => {
      try { await postar(`${projetoUrl(p)}/tentar`); avisar("Recomeçando…", "ok"); recarregar(); } catch (e) { avisar(e.message, "erro"); }
    }));
    caixa.append(el("div", { class: "acoes" }, botao));
  }
  return caixa;
}

function painelEditar(p, editavel, aoMudar) {
  const c = canal() || {};
  let modelo = (p.opcoes && p.opcoes.modelo) || c.modelo || "padrao";
  const botao = el("button", { class: "botao cta", type: "button", text: p.shorts.length ? "↻ Refazer todos os Shorts" : "🎬 Editar os Shorts agora" });
  botao.disabled = !editavel || p.etapa < 2;
  botao.addEventListener("click", () => comCarregando(botao, "Enviando ao editor…", async () => {
    try {
      await postar(`${projetoUrl(p)}/etapas/edicao`, { opcoes: { modelo } });
      avisar("O editor começou. Acompanhe a barra de progresso.", "ok");
      aoMudar("editar");
    } catch (e) { avisar(e.message, "erro"); }
  }));
  return el("div", { class: "etapa-texto" },
    p.edicao_desatualizada ? el("div", { class: "aviso", text: "Você mudou os momentos: edite os Shorts de novo para aplicar." }) : null,
    el("p", { text: p.etapa < 2 ? "Escolha os momentos antes de editar." : p.shorts.length
      ? `✓ ${p.shorts.length} Short(s) editado(s): corte, enquadramento no modelo do canal, legenda palavra por palavra e música.`
      : "O editor corta cada momento, monta no modelo do canal, legenda e põe a música." }),
    el("h4", { class: "subtitulo", text: "Modelo visual" }),
    seletorModelo(modelo, (m) => (modelo = m), p.canal),
    el("div", { class: "acoes" }, botao));
}

async function painelRevisao(p) {
  if (!p.shorts.length) return el("p", { class: "pequeno", text: "A revisão automática roda quando os Shorts ficarem prontos." });
  let r;
  try { r = await api(`${projetoUrl(p)}/revisao`); } catch (e) { return el("div", { class: "aviso erro", text: e.message }); }
  const resumo = { ok: "✓ Tudo certo para publicar.", aviso: "Dá para publicar, mas veja os avisos.", erro: "Corrija os itens em vermelho antes de publicar." };
  return el("div", { class: "revisao" },
    el("p", { class: `resumo-revisao ${r.nivel}`, text: resumo[r.nivel] }),
    r.desatualizado ? el("div", { class: "aviso", text: "Os momentos mudaram depois da edição: os vídeos ainda são os antigos." }) : null,
    el("div", { class: "lista-revisao" }, r.shorts.map((s) => el("div", { class: `item-revisao ${s.nivel}` },
      el("strong", { text: `Short ${s.indice}` }),
      el("ul", {}, s.itens.map((i) => el("li", { class: i.nivel, text: i.texto })))))));
}

function painelPublicar(p, aoMudar) {
  const caixa = el("div", { class: "etapa-texto" });
  const marcados = edicoes(p).filter((s) => s.selecionado).length;
  const c = canal() || {};
  if (p.estado === "agendado") {
    caixa.append(el("p", { text: "✓ Os Shorts estão na agenda do YouTube." }),
      el("ul", { class: "lista agenda" }, p.shorts.filter((s) => s.youtube && s.youtube.publicar_em).map((s) =>
        el("li", {}, el("span", { class: "data", text: new Date(s.youtube.publicar_em).toLocaleString("pt-BR") }),
          el("span", { class: "titulo" }, s.youtube.url ? el("a", { href: s.youtube.url, target: "_blank", rel: "noopener", text: s.titulo }) : s.titulo)))));
    const cancelar = el("button", { class: "botao perigo", type: "button", text: "Tirar da agenda" });
    cancelar.addEventListener("click", async () => {
      if (!(await confirmar("Tirar estes Shorts da agenda?", { ok: "Tirar da agenda", perigo: true, detalhe: "Eles ficam privados no YouTube (nada é apagado)." }))) return;
      try { const r = await postar(`${projetoUrl(p)}/cancelar`); avisar(`${(r.privados || []).length} Short(s) tirados da agenda.`, "ok"); aoMudar(); }
      catch (e) { avisar(e.message, "erro"); }
    });
    caixa.append(el("div", { class: "acoes" }, cancelar));
    return caixa;
  }
  if (!["pendente_revisao", "reprovado", "erro_publicacao"].includes(p.estado)) {
    caixa.append(el("p", { class: "pequeno", text: "A publicação libera quando os Shorts estiverem prontos para revisar." }));
    return caixa;
  }
  caixa.append(el("p", { text: `${marcados} de ${p.shorts.length} Short(s) marcados para publicar.` }),
    el("p", { class: "pequeno", text: `Horários do canal: ${(c.horarios || []).join(", ") || "padrão"}. Nada vai ao ar sem você tocar no botão abaixo.` }));
  const aprovar = el("button", { class: "botao cta", type: "button", text: p.estado === "erro_publicacao" ? "Tentar agendar de novo" : "Aprovar e agendar" });
  aprovar.addEventListener("click", async () => {
    if (!marcados) return avisar("Marque pelo menos um Short em “Textos”.", "erro");
    if (!(await confirmar(`Agendar ${marcados} Short(s) no YouTube?`, { ok: "Agendar" }))) return;
    await comCarregando(aprovar, "Enviando…", async () => {
      try {
        if (p.estado === "erro_publicacao") { await salvarTextos(p); await postar(`${projetoUrl(p)}/publicar`); }
        else { await postar(`${projetoUrl(p)}/aprovar`, { shorts: edicoes(p) }); estado.rascunhos.delete(p.id); }
        avisar("Enviando ao YouTube…", "ok");
        aoMudar("publicar");
      } catch (e) { avisar(e.message, "erro"); }
    });
  });
  caixa.append(el("div", { class: "acoes" }, aprovar));
  return caixa;
}

async function descartar(p) {
  if (!(await confirmar("Descartar os cortes desta live?", { ok: "Descartar", perigo: true, detalhe: "Eles somem da lista e não serão publicados. A live pode ser cortada de novo depois." }))) return;
  try {
    await postar(`${projetoUrl(p)}/descartar`);
    estado.rascunhos.delete(p.id);
    avisar("Cortes descartados.");
    recarregar(true);
  } catch (e) { avisar(e.message, "erro"); }
}

export function destacarDoEndereco() {
  const m = location.hash.match(/^#\/painel\/(.+)$/);
  if (!m) return;
  const id = decodeURIComponent(m[1]);
  const card = document.querySelector(`[data-projeto="${CSS.escape(id)}"]`);
  if (card && !card.classList.contains("destaque")) {
    document.querySelectorAll(".destaque").forEach((x) => x.classList.remove("destaque"));
    card.classList.add("destaque");
    const etapa = estado.abertos.get(id);
    if (etapa) card.querySelector(`.etapa-botao[data-etapa="${etapa}"]`)?.click();
    card.scrollIntoView({ block: "start", behavior: "smooth" });
  }
}
