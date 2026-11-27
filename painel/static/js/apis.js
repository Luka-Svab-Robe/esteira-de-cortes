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

/* Passo a passo para criar o app no Google Cloud (texto do guia; a esteira usa o tipo "App para computador"). */
const PASSOS = [
  { titulo: "Criar um projeto no Google Cloud",
    texto: "Acesse o Google Cloud Console e faça login com a sua conta Google. No menu superior, clique no seletor de projetos, depois em Novo Projeto, dê um nome (ex: Automacao-YouTube) e clique em Criar." },
  { titulo: "Ativar a YouTube Data API v3", marca: ["obrigatório", "ruim"],
    texto: "Com o projeto selecionado, vá no menu lateral em APIs e serviços > Biblioteca. Pesquise por YouTube Data API v3, clique no resultado e aperte Ativar.",
    nota: "Obrigatório para evitar erro 403." },
  { titulo: "Configurar a Tela de permissão OAuth",
    texto: "No menu lateral, vá em APIs e serviços > Tela de permissão OAuth (ou Google Auth Platform > Público-alvo). Escolha o tipo Externo e clique em Criar. Preencha apenas o nome do aplicativo, o e-mail de suporte e o e-mail do desenvolvedor, e salve." },
  { titulo: "Adicionar seu e-mail como usuário de teste",
    texto: "Ainda na tela de permissão OAuth, vá na seção Usuários de teste (Test users), clique em Adicionar usuários e coloque o e-mail da conta dona do seu canal do YouTube.",
    alerta: "Sem isso, o login será bloqueado." },
  { titulo: "Gerar as credenciais OAuth 2.0",
    texto: "Vá em APIs e serviços > Credenciais > + Criar credenciais > ID do cliente OAuth.",
    itens: [
      { destaque: "Use este para a esteira", texto: "Se for rodar em um script local (Python/Node.js), escolha App para computador (Desktop app)." },
      { texto: "Se for usar em ferramentas como n8n ou Make, escolha Aplicativo da Web e cole a URI de redirecionamento que a ferramenta fornecer." },
    ],
    depois: "Clique em Criar e faça o download do arquivo JSON (que contém seu Client ID e Client Secret)." },
];

function guia(app) {
  return el("details", { class: "guia", open: !app.configurado },
    el("summary", {}, el("span", { text: "Passo a passo no Google Cloud" }), el("span", { class: "etiqueta", text: `${PASSOS.length} passos` })),
    el("ol", { class: "guia-passos" }, ...PASSOS.map((p, i) => passo(i + 1, p))),
    el("p", { class: "guia-fim" }, el("strong", { text: "Próximo passo:" }),
      " abra o JSON baixado e copie o client_id e o client_secret para os campos abaixo."));
}

function passo(numero, p) {
  return el("li", { class: "guia-passo" },
    el("span", { class: "guia-numero", "aria-hidden": "true", text: String(numero) }),
    el("div", { class: "guia-corpo" },
      el("div", { class: "guia-cabeca" }, el("strong", { text: p.titulo }),
        p.marca ? el("span", { class: `etiqueta ${p.marca[1]}`, text: p.marca[0] }) : null),
      el("p", { class: "guia-texto", text: p.texto }),
      p.itens ? el("ul", { class: "guia-itens" }, ...p.itens.map((i) =>
        el("li", {}, i.destaque ? el("span", { class: "guia-destaque", text: i.destaque }) : null, i.texto))) : null,
      p.depois ? el("p", { class: "guia-texto", text: p.depois }) : null,
      p.nota ? el("p", { class: "guia-nota", text: p.nota }) : null,
      p.alerta ? el("p", { class: "guia-alerta", text: p.alerta }) : null));
}

function secaoApp(d) {
  const app = d.app;
  const caixa = el("section", { class: "card secao-conta" },
    el("h2", { class: "titulo-gta", text: "App do Google (YouTube)" }),
    el("p", { class: "pequeno", text: "Criado uma vez no Google Cloud e usado por todos os canais deste computador." }));
  if (!d.pode_editar_app) {
    caixa.append(el("p", { class: "pequeno", text: app.configurado
      ? "Configurado pelo administrador."
      : "Ainda não configurado. Peça ao administrador para colar o ID e o segredo do app." }));
    return caixa;
  }
  caixa.append(guia(app));
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
