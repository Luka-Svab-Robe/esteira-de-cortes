/* Aba APIs: o app do YouTube (Google Cloud) e a autorização de cada canal de cortes. Sem isso a esteira não agenda. */
import { $, api, avisar, comCarregando, el, gravar } from "./base.js";
import { abrirYoutube } from "./canal.js";
import { estado, guardarPreferencia } from "./estado.js";

let dados = null;

/* Tela inteira: ao abrir a aba e depois de salvar o app (o formulário é refeito). */
export async function renderApis() {
  const raiz = $("#apis-app");
  raiz.replaceChildren(el("div", { class: "esqueleto", style: "height:200px" }));
  try {
    dados = await api("/api/apis/youtube");
  } catch (e) {
    raiz.replaceChildren(el("div", { class: "aviso erro", text: e.message }));
    return;
  }
  raiz.replaceChildren(secaoApp(dados));
  desenharResumoECanais();
}

/* Só o estado dos canais (depois que o login do YouTube termina). Não refaz o formulário: o que foi digitado fica. */
export async function atualizarApis() {
  try {
    dados = await api("/api/apis/youtube");
  } catch (_) {
    return;
  }
  desenharResumoECanais();
}

function desenharResumoECanais() {
  $("#apis-resumo").replaceChildren(resumo(dados));
  $("#apis-canais").replaceChildren(secaoCanais(dados));
}

function resumo(d) {
  const faltas = [];
  if (d.app.erro) faltas.push(d.app.erro);
  if (!d.app.configurado && !d.app.erro) faltas.push("cole o ID e o segredo do app do Google");
  if (!d.canais.some((c) => c.conectado)) faltas.push("conecte pelo menos um canal do YouTube");
  return el("section", { class: "card", "aria-live": "polite" },
    d.pronto
      ? el("p", {}, el("span", { class: "etiqueta ok", text: "pronto" }), " A esteira já pode agendar os Shorts nos canais conectados.")
      : el("div", {}, el("strong", { text: "Ainda falta para agendar:" }),
          el("ul", { class: "pequeno" }, ...faltas.map((f) => el("li", { text: f })))));
}

function secaoApp(d) {
  const app = d.app;
  const caixa = el("section", { class: "card secao-conta" },
    el("h2", { class: "titulo-gta", text: "App do Google (YouTube)" }),
    el("p", { class: "pequeno", text: "Criado uma vez no Google Cloud e usado por todos os canais deste computador. O passo a passo está em credenciais/LEIA-ME.md." }));
  if (!d.pode_editar_app) {
    caixa.append(el("p", { class: "pequeno", text: app.configurado
      ? "Configurado pelo administrador."
      : "Ainda não configurado. Peça ao administrador para colar o ID e o segredo do app." }));
    return caixa;
  }
  const id = el("input", { type: "text", autocomplete: "off", spellcheck: "false",
    placeholder: app.configurado ? app.client_id : "123456789-abc.apps.googleusercontent.com" });
  const segredo = el("input", { type: "password", autocomplete: "new-password", spellcheck: "false",
    placeholder: app.configurado ? app.segredo : "GOCSPX-…" });
  const salvar = el("button", { class: "botao cta", type: "button", text: "Salvar app do Google" });
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    try {
      await gravar("/api/apis/youtube/app", { client_id: id.value, client_secret: segredo.value });
      avisar("App do Google salvo.", "ok");
      await renderApis();
    } catch (e) { avisar(e.message, "erro"); }
  }));
  caixa.append(
    el("div", { class: "grade-campos" },
      el("label", { class: "campo" }, "ID do cliente", id),
      el("label", { class: "campo" }, "Segredo do cliente", segredo)),
    el("div", { class: "acoes" }, salvar));
  return caixa;
}

function secaoCanais(d) {
  const caixa = el("section", { class: "card secao-conta" },
    el("h2", { class: "titulo-gta", text: "Canais do YouTube" }),
    el("p", { class: "pequeno", text: "Cada canal é autorizado uma vez, com o login da conta do YouTube dele. Depois, os horários são escolhidos em Agendados." }));
  if (!d.canais.length) {
    caixa.append(el("p", { class: "pequeno", text: "Você ainda não tem canal de cortes. Crie um em Conta → Seus canais." }));
    return caixa;
  }
  caixa.append(el("ul", { class: "lista" }, ...d.canais.map((c) => linhaCanal(c, d.app.configurado))));
  return caixa;
}

function linhaCanal(c, appConfigurado) {
  const botao = el("button", { class: "botao pequeno turquesa", type: "button",
    text: c.conectado ? "Reconectar" : "Conectar YouTube", disabled: !appConfigurado });
  botao.addEventListener("click", () => {
    estado.canalId = c.id;
    guardarPreferencia("canal", c.id);
    abrirYoutube();  // mesma janela de login usada em Agendados e Conta
  });
  const situacao = appConfigurado
    ? (c.conectado ? `YouTube: ${c.handle || c.canal_youtube || "conectado"}` : "Autorize o login da conta do YouTube deste canal.")
    : "Configure o app do Google acima antes de conectar.";
  return el("li", { class: "canal-conta" },
    el("div", { class: "canal-conta-topo" },
      el("div", {},
        el("strong", { text: c.nome }),
        el("span", { class: `etiqueta ${c.conectado ? "ok" : "ruim"}`, text: c.conectado ? "conectado" : "não conectado" }),
        el("div", { class: "pequeno", text: situacao })),
      botao));
}
