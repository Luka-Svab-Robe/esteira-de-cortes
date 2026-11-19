/* Cortar por link (Twitch, Kick ou YouTube), o cartão de cada live (usado na aba Canais) e a janela
   "Como você quer cortar?" (tudo automático ou passo a passo, com layout e opções). */
import { $, MODELOS, ROTULOS, avisar, el, horaCompleta, postar, relogio } from "./base.js";
import { canal, estado, ESTADOS_TRABALHANDO } from "./estado.js";

let aoComecar = () => {};
export function quandoComecar(fn) { aoComecar = fn; }

/* rodando = links das lives que a equipe está baixando/cortando agora */
export function cardLive(live, projeto, rodando) {
  const trabalhando = rodando.has(live.url) || (projeto && ESTADOS_TRABALHANDO.includes(projeto.estado));
  const acoes = el("div", { class: "acoes" });
  if (projeto && !trabalhando) {
    acoes.append(el("button", { class: "botao pequeno", type: "button", text: "Abrir o pipeline", onclick: () => irParaProjeto(projeto.id) }));
  } else if (trabalhando) {
    acoes.append(el("span", { class: "linha-progresso" }, el("span", { class: "giro" }), "A equipe está trabalhando nela"));
  } else {
    acoes.append(el("button", { class: "botao cta", type: "button", text: "✂ Cortar esta live", onclick: () => abrirOpcoes(live) }));
  }
  return el("article", { class: "card live-card" },
    el("div", { class: "capa", style: live.miniatura ? `background-image:url("${live.miniatura.replace(/"/g, "")}")` : "" },
      live.duracao ? el("span", { class: "duracao", text: relogio(live.duracao) }) : null),
    el("div", { class: "corpo" },
      el("h3", { text: live.titulo || "Live" }),
      el("div", { class: "pequeno" },
        projeto ? el("span", { class: `pill ${projeto.estado}`, text: ROTULOS[projeto.estado] || projeto.estado_texto })
          : el("span", { class: "pill nova", text: "Ainda não cortada" }),
        live.visualizacoes != null ? ` · ${Number(live.visualizacoes).toLocaleString("pt-BR")} visualizações` : ""),
      acoes));
}

/* ------------------------------------------------------------ cortar por link */
export const PLATAFORMAS = { twitch: "Twitch", kick: "Kick", youtube: "YouTube" };
const LAYOUTS = [
  ["auto", "🤖 Automático", "Acha a câmera do streamer: webcam no canto vira tela dividida, câmera cheia vira vertical, só jogo vai na moldura."],
  ["dividido", "📺 Tela dividida", "Câmera do streamer em cima, jogo embaixo e o gancho na emenda."],
  ["moldura", "🖼 Moldura do canal", "Foto e @ do canal no topo e a live inteira no centro."],
  ["vertical", "📱 Vertical no rosto", "Recorte 9:16 que acompanha o rosto (bom para bate-papo)."],
];

export function ligarLink() {
  const form = $("#form-link");
  if (!form) return;
  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const campo = $("#link-live"), botao = $("#btn-link-live");
    let url = campo.value.trim();
    if (!url) return avisar("Cole o link da live primeiro.", "erro");
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    const c = canal();
    if (!c) return avisar("Crie ou escolha um canal antes.", "erro");
    botao.disabled = true;
    botao.replaceChildren(el("span", { class: "giro" }), "Lendo o link…");
    try {
      const live = await conferirLink(c, url);
      if (live) abrirOpcoes(live);
    } finally {
      botao.disabled = false;
      botao.textContent = "✂ Cortar este link";
    }
  });
}

async function conferirLink(c, url, autorizacao = "") {
  try {
    const r = await postar("/api/cortes/conferir", { canal: c.id, url, autorizacao });
    if (autorizacao) avisar(`${r.dono} entrou nas suas fontes.`, "ok");
    return r;
  } catch (e) {
    const d = e.dados || {};
    if (d.codigo === "fonte_nao_autorizada" && !autorizacao) {
      const texto = await pedirAutorizacao(d.dono || "Esse criador", url);
      return texto ? conferirLink(c, url, texto) : null;
    }
    if (d.codigo === "repetida" && d.projeto) irParaProjeto(d.projeto);
    avisar(e.message, "erro");
    return null;
  }
}

function plataformaDoLink(url) {
  if (/(^|\.)twitch\.tv\//i.test(url.replace(/^https?:\/\//i, ""))) return "twitch";
  if (/(^|\.)kick\.com\//i.test(url.replace(/^https?:\/\//i, ""))) return "kick";
  return "youtube";
}

function pedirAutorizacao(dono, url) {
  return new Promise((resolver) => {
    const dialogo = el("dialog", { class: "dialogo" });
    const fechar = (valor) => { dialogo.close(); dialogo.remove(); resolver(valor); };
    const confirmo = el("input", { type: "checkbox" });
    const onde = el("input", { type: "text", maxlength: "200", placeholder: "ex.: regras do canal na Twitch, Discord, e-mail" });
    const ok = el("button", { class: "botao cta", type: "button", text: "Confirmar e continuar", disabled: true });
    confirmo.addEventListener("change", () => { ok.disabled = !confirmo.checked; });
    ok.addEventListener("click", () => {
      const dia = new Date().toLocaleDateString("pt-BR");
      const detalhe = onde.value.trim();
      fechar(`Confirmado no painel em ${dia}: ${dono} permite cortes com crédito${detalhe ? ` (${detalhe})` : ""}.`);
    });
    const plataforma = PLATAFORMAS[plataformaDoLink(url)];
    dialogo.append(
      el("h2", { class: "titulo-gta", text: `Cortar a live de ${dono}?` }),
      el("p", { text: `${dono} (${plataforma}) ainda não está nas suas fontes. Corte só lives de criadores que permitem cortes: ` +
        "o crédito da live aparece no vídeo e na descrição, e cortar sem permissão pode render strike no seu canal." }),
      el("label", { class: "marcar" }, confirmo, `Confirmo que ${dono} permite cortes das lives`),
      el("label", { class: "campo" }, "Onde ele autorizou? (opcional)", onde),
      el("div", { class: "acoes" }, ok, el("button", { class: "botao", type: "button", text: "Cancelar", onclick: () => fechar(null) })));
    dialogo.addEventListener("cancel", () => fechar(null));
    document.body.append(dialogo);
    dialogo.showModal();
  });
}

export function irParaProjeto(id, etapa = null) {
  if (etapa) estado.abertos.set(id, etapa);
  location.hash = `#/painel/${encodeURIComponent(id)}`;
}

/* ------------------------------------------------------------ opções antes de cortar */
export function seletorModelo(atual, aoEscolher, canalId) {
  const grupo = el("div", { class: "modelos", role: "radiogroup", "aria-label": "Modelo visual" });
  for (const m of MODELOS) {
    const opcao = el("button", { class: `modelo ${m.id === atual ? "escolhido" : ""}`, type: "button", role: "radio",
      "aria-checked": String(m.id === atual), title: m.dica },
      el("img", { src: `/api/modelos/${encodeURIComponent(canalId)}/${m.id}.png`, alt: `Modelo ${m.nome}`, loading: "lazy" }),
      el("strong", { text: m.nome }), el("span", { class: "pequeno", text: m.dica }));
    opcao.addEventListener("click", () => {
      for (const b of grupo.children) { b.classList.remove("escolhido"); b.setAttribute("aria-checked", "false"); }
      opcao.classList.add("escolhido");
      opcao.setAttribute("aria-checked", "true");
      aoEscolher(m.id);
    });
    grupo.append(opcao);
  }
  return grupo;
}

function seletorLayout(atual, aoEscolher) {
  const grupo = el("div", { class: "modos", role: "radiogroup", "aria-label": "Layout dos Shorts" });
  for (const [id, titulo, texto] of LAYOUTS) {
    const b = el("button", { class: `modo ${id === atual ? "escolhido" : ""}`, type: "button", role: "radio",
      "aria-checked": String(id === atual) }, el("strong", { text: titulo }), el("span", { class: "pequeno", text: texto }));
    b.addEventListener("click", () => {
      for (const x of grupo.children) { x.classList.toggle("escolhido", x === b); x.setAttribute("aria-checked", String(x === b)); }
      aoEscolher(id);
    });
    grupo.append(b);
  }
  return grupo;
}

export function abrirOpcoes(live) {
  const c = canal();
  const padrao = c.cortes_padrao || {};
  const externa = live.plataforma === "twitch" || live.plataforma === "kick";
  const escolhas = { modo: "completo", modelo: c.modelo || "padrao", layout: externa ? "auto" : "" };
  const dialogo = el("dialog", { class: "dialogo largo" });
  const fechar = () => { dialogo.close(); dialogo.remove(); };
  const qtd = el("input", { type: "number", min: "1", max: "10", value: padrao.max_cortes_por_live || 5 });
  const dmin = el("input", { type: "number", min: "10", max: "60", value: padrao.duracao_min || 20 });
  const dmax = el("input", { type: "number", min: "15", max: "180", value: padrao.duracao_max || 58 });
  const traduzir = el("input", { type: "checkbox", checked: padrao.traduzir !== false });
  const ini = el("input", { type: "text", placeholder: "ex.: 30:00", inputmode: "numeric", "aria-label": "Início do trecho" });
  const fim = el("input", { type: "text", placeholder: "ex.: 1:10:00", inputmode: "numeric", "aria-label": "Fim do trecho" });
  const modos = el("div", { class: "modos", role: "radiogroup" },
    [["completo", "⚡ Tudo automático", "A equipe baixa, escolhe os momentos, edita e deixa os Shorts prontos para você aprovar."],
     ["etapas", "🎛 Passo a passo", "A equipe baixa a live e espera: você escolhe os momentos (à mão ou com a IA) e manda editar."]]
      .map(([id, titulo, texto]) => {
        const b = el("button", { class: `modo ${id === escolhas.modo ? "escolhido" : ""}`, type: "button", role: "radio" },
          el("strong", { text: titulo }), el("span", { class: "pequeno", text: texto }));
        b.addEventListener("click", () => {
          escolhas.modo = id;
          for (const x of modos.children) x.classList.toggle("escolhido", x === b);
        });
        return b;
      }));
  const comecar = el("button", { class: "botao cta", type: "button", text: "Começar" });
  comecar.addEventListener("click", async () => {
    let trecho = "";
    if (live.no_ar && !(ini.value.trim() && fim.value.trim())) {
      return avisar("A live ainda está no ar: preencha o começo e o fim de um trecho que já passou.", "erro");
    }
    if (ini.value.trim() || fim.value.trim()) {
      const a = horaCompleta(ini.value), b = horaCompleta(fim.value);
      if (!a || !b) return avisar("Preencha o começo e o fim do trecho, ex.: 30:00 até 1:10:00.", "erro");
      trecho = `${a}-${b}`;
    }
    comecar.disabled = true;
    comecar.replaceChildren(el("span", { class: "giro" }), "Conferindo a live…");
    try {
      await postar("/api/cortes/processar", { canal: c.id, url: live.url, trecho, modo: escolhas.modo, opcoes: {
        max_cortes_por_live: Number(qtd.value), duracao_min: Number(dmin.value), duracao_max: Number(dmax.value),
        modelo: escolhas.modelo, traduzir: traduzir.checked, ...(escolhas.layout ? { layout: escolhas.layout } : {}) } });
      fechar();
      avisar(escolhas.modo === "etapas" ? "Baixando a live. Depois é só escolher os momentos." : "Começou! Acompanhe a barra de progresso.", "ok");
      aoComecar();
    } catch (e) {
      const d = e.dados || {};
      if (d.codigo === "repetida" && d.projeto) { fechar(); irParaProjeto(d.projeto); }
      avisar(e.message, "erro");
      comecar.disabled = false;
      comecar.textContent = "Começar";
    }
  });
  dialogo.append(
    el("h2", { class: "titulo-gta", text: "Como você quer cortar?" }),
    el("p", { class: "pequeno" },
      live.plataforma ? el("span", { class: `selo-plataforma ${live.plataforma}`, text: PLATAFORMAS[live.plataforma] || live.plataforma }) : null,
      live.dono ? `${live.dono} · ` : "", live.titulo || "", live.duracao ? ` · ${relogio(live.duracao)}` : ""),
    live.no_ar
      ? el("p", { class: "pequeno", text: "🔴 A live ainda está no ar: diga abaixo, em \"Cortar só um pedaço da live?\", um trecho que já passou (só ele é baixado)." })
      : externa && live.duracao > 3 * 3600
        ? el("p", { class: "pequeno", text: "Live longa: para sair mais rápido, diga em \"Cortar só um pedaço da live?\" o trecho que interessa (só ele é baixado)." })
        : null,
    modos,
    el("h3", { class: "subtitulo", text: "Layout dos Shorts" }),
    seletorLayout(escolhas.layout || c.layout || "moldura", (l) => (escolhas.layout = l)),
    el("h3", { class: "subtitulo", text: "Modelo da moldura (quando o Short usar a moldura do canal)" }),
    seletorModelo(escolhas.modelo, (m) => (escolhas.modelo = m), c.id),
    el("h3", { class: "subtitulo", text: "Opções desta live" }),
    el("div", { class: "grade-campos" },
      el("label", { class: "campo" }, "Quantos Shorts (até)", qtd),
      el("label", { class: "campo" }, "Duração mínima (s)", dmin),
      el("label", { class: "campo" }, "Duração máxima (s)", dmax)),
    el("label", { class: "marcar" }, traduzir, "Traduzir para o português o que for falado em outra língua"),
    el("details", { open: live.no_ar ? true : null }, el("summary", { class: "pequeno", text: "Cortar só um pedaço da live?" }),
      el("div", { class: "linha-trecho" }, ini, el("span", { class: "pequeno", text: "até" }), fim)),
    el("div", { class: "acoes" }, comecar, el("button", { class: "botao", type: "button", text: "Voltar", onclick: fechar })));
  dialogo.addEventListener("cancel", fechar);
  document.body.append(dialogo);
  dialogo.showModal();
}
