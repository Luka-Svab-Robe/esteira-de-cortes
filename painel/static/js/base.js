/* Base do painel: criação de elementos (sempre textContent, nunca innerHTML), chamadas à API com o cabeçalho
   anti-CSRF, avisos, confirmação própria (sem window.confirm) e formatadores. */

export const $ = (sel, raiz = document) => raiz.querySelector(sel);
export const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...filhos) {
  const no = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") no.className = v;
    else if (k === "text") no.textContent = v;
    else if (k === "style") no.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") no.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled", "selected"].includes(k)) no[k] = v;
    else no.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) no.append(f);
  return no;
}

/* ------------------------------------------------------------ API */
let aoPerderSessao = () => {};
export function quandoPerderSessao(fn) { aoPerderSessao = fn; }

export async function api(url, opcoes = {}) {
  const metodo = (opcoes.method || "GET").toUpperCase();
  const cabecalhos = { ...(opcoes.headers || {}) };
  if (metodo !== "GET") cabecalhos["X-Esteira"] = "1";
  if (typeof opcoes.body === "string") cabecalhos["Content-Type"] = "application/json";
  const r = await fetch(url, { ...opcoes, headers: cabecalhos, credentials: "same-origin" });
  let corpo = null;
  try { corpo = await r.json(); } catch (_) { /* sem corpo */ }
  if (r.status === 401 && corpo && corpo.detail && corpo.detail.codigo === "sessao") aoPerderSessao();
  if (!r.ok) {
    const d = corpo && corpo.detail;
    const erro = new Error((d && (d.mensagem || (typeof d === "string" ? d : ""))) || "Não deu certo agora. Tente de novo.");
    erro.dados = d && typeof d === "object" ? d : {};
    erro.status = r.status;
    throw erro;
  }
  return corpo;
}
export const postar = (url, corpo = {}) => api(url, { method: "POST", body: JSON.stringify(corpo) });
export const gravar = (url, corpo = {}) => api(url, { method: "PUT", body: JSON.stringify(corpo) });

/* ------------------------------------------------------------ avisos e confirmação */
let timerAviso;
export function avisar(texto, tipo = "") {
  const t = $("#toast");
  t.textContent = texto;
  t.className = `toast visivel ${tipo}`;
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => (t.className = "toast"), 4500);
}

export function confirmar(texto, { ok = "Confirmar", perigo = false, detalhe = "" } = {}) {
  return new Promise((resolver) => {
    const dialogo = el("dialog", { class: "dialogo" });
    const fechar = (valor) => { dialogo.close(); dialogo.remove(); resolver(valor); };
    dialogo.append(...[
      el("h2", { class: "titulo-gta", text: "Confirmar" }),
      el("p", { text: texto }),
      detalhe ? el("p", { class: "pequeno", text: detalhe }) : null,
      el("div", { class: "acoes" },
        el("button", { class: `botao ${perigo ? "perigo" : "cta"}`, type: "button", text: ok, onclick: () => fechar(true) }),
        el("button", { class: "botao", type: "button", text: "Voltar", onclick: () => fechar(false) })),
    ].filter(Boolean));
    dialogo.addEventListener("cancel", () => fechar(false));
    document.body.append(dialogo);
    dialogo.showModal();
  });
}

/* botão com "girando…" enquanto a ação roda */
export async function comCarregando(botao, rotulo, acao) {
  const original = [...botao.childNodes].map((n) => n.cloneNode(true));
  botao.disabled = true;
  botao.replaceChildren(el("span", { class: "giro" }), rotulo);
  try { return await acao(); }
  finally { botao.disabled = false; botao.replaceChildren(...original); }
}

/* ------------------------------------------------------------ formatadores */
export const ROTULOS = {
  pendente_revisao: "Esperando sua aprovação", reprovado: "Esperando correção", agendado: "Agendado",
  erro: "Precisa de atenção", erro_publicacao: "Erro ao agendar", publicando: "Enviando ao YouTube",
  aprovado: "Aprovado", em_producao: "Em produção", aguardando_roteiro: "Esperando a IA",
  aguardando_etapa: "Esperando você", descartado: "Descartado",
};
export const IDIOMAS = { en: "inglês", es: "espanhol", fr: "francês", de: "alemão", it: "italiano", ja: "japonês", ko: "coreano" };
export const MODELOS = [
  { id: "padrao", nome: "Clássico", dica: "Foto e @ à esquerda" },
  { id: "direita", nome: "Espelhado", dica: "Foto e @ à direita" },
  { id: "centro", nome: "Destaque", dica: "Foto grande no centro do topo" },
];

export function dataCurta(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const dia = d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }).replace(".", "");
  return `${dia} · ${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}
export function relogio(seg) {
  seg = Math.max(0, Math.round(Number(seg) || 0));
  const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = seg % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
export function segundos(texto) {
  const partes = String(texto || "").trim().split(":").map((x) => x.trim());
  if (!partes.length || partes.some((x) => !/^\d+(\.\d+)?$/.test(x)) || partes.length > 3) return null;
  return partes.reduce((total, x) => total * 60 + Number(x), 0);
}
export function horaCompleta(texto) {
  const s = segundos(texto);
  if (s === null) return null;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60);
  return [h, m, r].map((x) => String(x).padStart(2, "0")).join(":");
}
export const nomeMusica = (nome) => (nome || "").replace(/^synthwave_/, "").replace(/\.\w+$/, "").replace(/_/g, " ");
export const projetoUrl = (p) => `/api/projetos/${p.id}`;
