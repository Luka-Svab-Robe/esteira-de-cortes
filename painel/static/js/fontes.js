/* Aba Canais: os canais de onde vêm as lives (Twitch, Kick ou YouTube). Integre quantos quiser: cada um mostra as
   lives recentes para cortar e pode ser vigiado pelo Modo automático. O canal do YouTube conectado (selo no topo)
   serve só para postar os Shorts: ele não vira fonte sozinho. */
import { $, api, avisar, confirmar, el, postar } from "./base.js";
import { canal, estado } from "./estado.js";
import { PLATAFORMAS, cardLive } from "./lives.js";

const TIPOS = { proprio: "Seu canal", autorizado: "Criador autorizou" };
const ESQUELETOS = 3;
const abertos = new Set();  // links dos canais com as lives à mostra
let recarregar = async () => {};
let assinatura = "";

export function ligarFontes({ aoMudar }) {
  recarregar = aoMudar;
  montarFormulario();
}

/* troca de canal de cortes no seletor do topo: cada canal tem as próprias fontes */
export function esquecerLives() {
  abertos.clear();
  estado.lives = new Map();
}

/* "Cortar este link" recebeu o link de um canal: abre a aba Canais com o link já no campo de integrar */
export function integrarCanalPeloLink(url) {
  location.hash = "#/canais";
  const campo = $("#integrar-fonte input[type=url]");
  if (!campo) return;
  campo.value = url;
  setTimeout(() => campo.focus(), 50);
}

const enderecoCurto = (url) => String(url || "").replace(/^https?:\/\/(www\.|m\.)?/i, "").replace(/\/$/, "");
const vazio = (titulo, texto) => el("div", { class: "vazio" }, el("strong", { text: titulo }), texto);

/* ------------------------------------------------------------ integrar um canal */
function montarFormulario() {
  const raiz = $("#integrar-fonte");
  if (!raiz) return;
  let tipo = "autorizado";
  const link = el("input", { type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false", "aria-label": "Link do canal",
    placeholder: "twitch.tv/nome  ·  kick.com/nome  ·  youtube.com/@canal" });
  const botao = el("button", { class: "botao cta", type: "submit", text: "＋ Integrar canal" });
  const confirmo = el("input", { type: "checkbox" });
  const onde = el("input", { type: "text", maxlength: "200", placeholder: "ex.: regras do canal na Twitch, Discord, e-mail" });
  const vigiar = el("input", { type: "checkbox" });
  const autorizacao = el("div", { class: "bloco-autorizacao" },
    el("label", { class: "marcar" }, confirmo, "Confirmo que o criador permite cortes das lives (o crédito aparece no vídeo e na descrição)"),
    el("label", { class: "campo" }, "Onde ele autorizou? (opcional)", onde));
  const modos = el("div", { class: "modos", role: "radiogroup", "aria-label": "De quem é o canal" });
  for (const [id, titulo, texto] of [
    ["autorizado", "🤝 De outro criador", "Ele permite cortes. O crédito da live vai na tela e na descrição de cada Short."],
    ["proprio", "🎙 É um canal meu", "Lives suas: os Shorts saem sem crédito a terceiros."]]) {
    const b = el("button", { class: `modo ${id === tipo ? "escolhido" : ""}`, type: "button", role: "radio",
      "aria-checked": String(id === tipo) }, el("strong", { text: titulo }), el("span", { class: "pequeno", text: texto }));
    b.addEventListener("click", () => {
      tipo = id;
      for (const x of modos.children) { x.classList.toggle("escolhido", x === b); x.setAttribute("aria-checked", String(x === b)); }
      autorizacao.classList.toggle("oculto", id === "proprio");
    });
    modos.append(b);
  }
  const form = el("form", { class: "form-fonte", novalidate: true },
    el("div", { class: "form-link" }, link, botao), modos, autorizacao,
    el("label", { class: "marcar" }, vigiar, "Vigiar este canal no Modo automático (corta sozinho as lives novas dele)"));
  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const c = canal();
    if (!c) return avisar("Crie seu canal de cortes na aba Conta antes.", "erro");
    let url = link.value.trim();
    if (!url) return avisar("Cole o link do canal primeiro.", "erro");
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    if (tipo === "autorizado" && !confirmo.checked) return avisar("Confirme que o criador permite cortes das lives.", "erro");
    const detalhe = onde.value.trim();
    const texto = tipo === "autorizado"
      ? `Confirmado no painel em ${new Date().toLocaleDateString("pt-BR")}: o criador permite cortes com crédito${detalhe ? ` (${detalhe})` : ""}.`
      : "";
    botao.disabled = true;
    botao.replaceChildren(el("span", { class: "giro" }), "Lendo o canal…");
    try {
      const r = await postar("/api/fontes", { canal: c.id, url, tipo, autorizacao: texto, monitorar: vigiar.checked });
      avisar(`${r.fonte.nome} foi integrado. As lives dele aparecem abaixo.`, "ok");
      link.value = ""; onde.value = ""; confirmo.checked = false; vigiar.checked = false;
      abertos.add(r.fonte.url);
      await recarregar();
      carregarLives(c, r.fonte);
    } catch (e) {
      avisar(e.message, "erro");
    } finally {
      botao.disabled = false;
      botao.textContent = "＋ Integrar canal";
    }
  });
  raiz.replaceChildren(form);
}

/* ------------------------------------------------------------ lista de canais integrados */
function projetosPorLive(c) {
  const mapa = {};
  for (const p of estado.dados ? estado.dados.projetos : []) {
    if (p.canal === c.id && p.live.id && !mapa[p.live.id]) mapa[p.live.id] = p;
  }
  return mapa;
}

const livesRodando = () => new Set((estado.dados ? estado.dados.envios : []).filter((e) => e.estado === "rodando").map((e) => e.url));

export function renderFontes(forcar = false) {
  const raiz = $("#lista-fontes");
  if (!raiz) return;
  const c = canal();
  const fontes = c ? c.fontes || [] : [];
  $("#qtd-fontes").textContent = String(fontes.length);
  const rodando = livesRodando();
  const porLive = c ? projetosPorLive(c) : {};
  const nova = JSON.stringify([c && c.id, fontes, [...abertos], [...estado.lives.entries()], [...rodando].sort(),
    Object.entries(porLive).map(([k, p]) => [k, p.id, p.estado])]);
  if (!forcar && (nova === assinatura || raiz.contains(document.activeElement))) return;
  assinatura = nova;
  if (!c) return raiz.replaceChildren(vazio("Nenhum canal de cortes ainda", "Crie seu canal de cortes na aba Conta."));
  if (!fontes.length) {
    return raiz.replaceChildren(vazio("Nenhum canal integrado",
      "Cole acima o link de um canal da Twitch, da Kick ou do YouTube. Dá para integrar quantos você quiser."));
  }
  raiz.replaceChildren(...fontes.map((f) => cartaoFonte(c, f, rodando, porLive)));
}

function cartaoFonte(c, f, rodando, porLive) {
  const aberto = abertos.has(f.url);
  const vigiar = el("input", { type: "checkbox", checked: f.monitorar, "aria-label": `Vigiar ${f.nome} no Modo automático` });
  vigiar.addEventListener("change", () => alternarVigia(c, f, vigiar));
  const ver = el("button", { class: "botao pequeno turquesa", type: "button", "aria-expanded": String(aberto),
    text: aberto ? "▴ Esconder lives" : "▾ Ver lives" });
  ver.addEventListener("click", () => {
    if (aberto) abertos.delete(f.url);
    else abertos.add(f.url);
    if (!aberto && !estado.lives.has(f.url)) carregarLives(c, f);
    else renderFontes(true);
  });
  const procurar = aberto ? el("button", { class: "botao pequeno", type: "button", text: "↻ Procurar lives novas",
    onclick: () => carregarLives(c, f, true) }) : null;
  const remover = el("button", { class: "botao pequeno perigo", type: "button", text: "Remover", onclick: () => removerFonte(c, f) });
  return el("article", { class: "card fonte" },
    el("div", { class: "fonte-topo" },
      el("div", { class: "fonte-nome" },
        el("h3", { text: f.nome }),
        el("div", { class: "pequeno" },
          el("span", { class: `selo-plataforma ${f.plataforma}`, text: PLATAFORMAS[f.plataforma] || f.plataforma }), " ",
          el("span", { class: "pill nova", text: TIPOS[f.tipo] || f.tipo }), " · ",
          el("a", { href: f.url, target: "_blank", rel: "noopener noreferrer", text: enderecoCurto(f.url) }))),
      el("div", { class: "fonte-vigia" },
        el("span", { class: "pequeno", text: f.monitorar ? "Vigiado" : "Não vigiado" }),
        el("label", { class: "chave", title: "Vigiar no Modo automático" }, vigiar, el("span")))),
    f.autorizacao ? el("p", { class: "pequeno autorizacao", text: `Autorização: ${f.autorizacao}` }) : null,
    el("div", { class: "acoes" }, ver, procurar, remover),
    aberto ? gradeLives(f, rodando, porLive) : null);
}

function gradeLives(f, rodando, porLive) {
  const info = estado.lives.get(f.url);
  if (!info || info.carregando) {
    return el("div", { class: "lives-grade" }, ...Array.from({ length: ESQUELETOS }, () => el("div", { class: "esqueleto esqueleto-live" })));
  }
  if (info.erro) return vazio("Não consegui ver as lives", info.erro);
  if (!info.lista.length) return vazio("Nenhuma live salva ainda", "Quando ele fizer uma live, ela aparece aqui para cortar.");
  return el("div", { class: "lives-grade" }, ...info.lista.map((live) => cardLive(live, porLive[live.id] || live.projeto, rodando)));
}

async function carregarLives(c, f, atualizar = false) {
  estado.lives.set(f.url, { carregando: true });
  renderFontes(true);
  try {
    const lista = await api(`/api/lives?canal=${encodeURIComponent(c.id)}&fonte=${encodeURIComponent(f.url)}` +
      (atualizar ? "&atualizar=true" : ""));
    estado.lives.set(f.url, { lista });
  } catch (e) {
    estado.lives.set(f.url, { erro: e.message, lista: [] });
  }
  renderFontes(true);
}

async function alternarVigia(c, f, chave) {
  chave.disabled = true;
  try {
    await postar("/api/fontes/monitorar", { canal: c.id, url: f.url, monitorar: chave.checked });
    avisar(chave.checked ? `O Modo automático vai vigiar ${f.nome}.` : `${f.nome} não é mais vigiado.`, "ok");
  } catch (e) {
    chave.checked = !chave.checked;
    avisar(e.message, "erro");
  } finally {
    chave.disabled = false;
    await recarregar();
  }
}

async function removerFonte(c, f) {
  const ok = await confirmar(`Tirar ${f.nome} dos seus canais?`, { ok: "Remover", perigo: true,
    detalhe: "As lives dele deixam de aparecer aqui e o Modo automático para de vigiá-lo. Os Shorts já feitos continuam." });
  if (!ok) return;
  try {
    await postar("/api/fontes/remover", { canal: c.id, url: f.url });
    abertos.delete(f.url);
    estado.lives.delete(f.url);
    avisar(`${f.nome} saiu dos seus canais.`, "ok");
    await recarregar();
  } catch (e) {
    avisar(e.message, "erro");
  }
}
