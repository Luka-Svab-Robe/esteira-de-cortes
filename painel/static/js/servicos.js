/* Vitrine de serviços: cada agente da equipe é um cartão. Você escolhe o serviço e a live, e ele roda
   aquela etapa (ou abre o editor manual dela). */
import { $, ROTULOS, api, avisar, dataCurta, el, gravar } from "./base.js";
import { comporTrilhas } from "./canal.js";
import { canal, estado } from "./estado.js";
import { irParaProjeto } from "./lives.js";

let conexoes = null;
async function iaConectada() {
  if (conexoes === null) {
    try { conexoes = await api("/api/conexoes"); } catch (_) { conexoes = []; }
  }
  const tem = (chave) => conexoes.some((c) => c.chave === chave && c.configurado);
  if (tem("ANTHROPIC_API_KEY")) return "Claude conectado";
  if (tem("OPENAI_API_KEY") || tem("OPENAI_BASE_URL")) return "IA externa conectada";
  return "IA local ou sugestões automáticas";
}
export function esquecerConexoes() { conexoes = null; }

const contar = (filtro) => (estado.dados ? estado.dados.projetos.filter(filtro).length : 0);
const SERVICOS = [
  { id: "olheiro", nome: "Olheiro", papel: "Encontra as lives", icone: "🔭",
    descricao: "Lista as lives dos canais que você integrou (Twitch, Kick ou YouTube) e avisa quais ainda não foram cortadas.",
    status: () => `${((canal() || {}).fontes || []).length} canal(is) integrado(s)`, acao: "Ver os canais",
    executar: () => { location.hash = "#/canais"; } },
  { id: "analista", nome: "Analista", papel: "Escolhe os momentos", icone: "🎯", etapa: "momentos",
    descricao: "Ouve a live, mede as reações do chat e escolhe os trechos com mais potencial. Você pode ajustar à mão.",
    status: () => `${contar((p) => p.estado === "aguardando_etapa")} live(s) esperando você`, acao: "Escolher momentos" },
  { id: "tradutor", nome: "Tradutor", papel: "Tudo em português", icone: "🌎", alternavel: true,
    descricao: "Fala em outra língua vira legenda, gancho e título em português, no ritmo da fala.",
    status: () => `${(estado.dados ? estado.dados.projetos : []).flatMap((p) => p.shorts).filter((s) => s.traduzida_de).length} Short(s) traduzido(s)` },
  { id: "editor", nome: "Editor", papel: "Monta os Shorts", icone: "🎬", etapa: "editar",
    descricao: "Corta cada momento, encaixa no modelo do canal, legenda palavra por palavra e mixa a música.",
    status: () => `${(estado.dados ? estado.dados.projetos : []).reduce((n, p) => n + p.shorts.length, 0)} Short(s) feitos`, acao: "Editar Shorts" },
  { id: "compositor", nome: "Compositor", papel: "Trilhas originais", icone: "🎹",
    descricao: "Compõe trilhas synthwave próprias, sem dono no Content ID: livres para monetizar.",
    status: () => `${((canal() || {}).trilhas || []).length} trilha(s) no canal`, acao: "Compor trilhas",
    executar: (botao) => comporTrilhas(botao) },
  { id: "redator", nome: "Redator", papel: "Títulos e descrições", icone: "✍️", etapa: "textos", status: iaConectada,
    descricao: "Sugere ganchos, títulos, descrições e tags com IA. Você escolhe a opção e pode editar tudo.", acao: "Melhorar textos" },
  { id: "revisor", nome: "Revisor", papel: "Checagem final", icone: "🔎", etapa: "revisao",
    descricao: "Confere duração, limites do YouTube, crédito, palavras censuradas e música de cada Short.", acao: "Conferir Shorts",
    status: () => `${contar((p) => p.estado === "pendente_revisao")} live(s) para revisar` },
  { id: "agendador", nome: "Agendador", papel: "Publica no horário", icone: "📅", etapa: "publicar",
    descricao: "Depois da sua aprovação, agenda os Shorts nos horários do canal. Nada vai ao ar sem você.",
    status: () => { const a = (estado.dados || { agenda: [] }).agenda[0]; return a ? `Próximo: ${dataCurta(a.quando)}` : "Nada agendado"; },
    acao: "Aprovar e agendar" },
];

export async function renderServicos() {
  const grade = $("#vitrine");
  const cartoes = SERVICOS.map((s) => {
    const status = el("span", { class: "status-servico", text: "…" });
    Promise.resolve(s.status()).then((t) => (status.textContent = t)).catch(() => (status.textContent = ""));
    const rodape = el("div", { class: "rodape-servico" }, status);
    if (s.alternavel) rodape.append(chaveTradutor());
    else {
      const botao = el("button", { class: "botao cta pequeno", type: "button", text: s.acao });
      botao.addEventListener("click", () => (s.executar ? s.executar(botao) : escolherLive(s)));
      rodape.append(botao);
    }
    return el("article", { class: `servico servico-${s.id}` },
      el("div", { class: "icone-servico", "aria-hidden": "true", text: s.icone }),
      el("h3", { class: "titulo-gta", text: s.nome }),
      el("div", { class: "papel", text: s.papel }),
      el("p", { class: "pequeno", text: s.descricao }),
      rodape);
  });
  grade.replaceChildren(...cartoes);
}

function chaveTradutor() {
  const c = canal();
  const entrada = el("input", { type: "checkbox", "aria-label": "Traduzir falas", checked: Boolean(c && (c.cortes_padrao || {}).traduzir !== false), disabled: !c });
  entrada.addEventListener("change", async () => {
    try {
      await gravar(`/api/canais/${c.id}/padroes`, { traduzir: entrada.checked });
      avisar(entrada.checked ? "Tradutor ligado nos próximos cortes." : "Tradutor desligado nos próximos cortes.", "ok");
    } catch (e) { entrada.checked = !entrada.checked; avisar(e.message, "erro"); }
  });
  return el("label", { class: "chave" }, entrada, el("span"));
}

/* escolha da live para o serviço (abre a etapa dela no pipeline) */
function escolherLive(servico) {
  const projetos = estado.dados ? estado.dados.projetos : [];
  const dialogo = el("dialog", { class: "dialogo" });
  const fechar = () => { dialogo.close(); dialogo.remove(); };
  dialogo.append(el("h2", { class: "titulo-gta", text: servico.nome }),
    el("p", { class: "pequeno", text: projetos.length ? "Em qual live?" : "Nenhuma live cortada ainda. Comece pelo Olheiro: escolha uma live e toque em ✂ Cortar." }),
    el("div", { class: "lista-escolha" }, projetos.map((p) => el("button", { class: "escolha", type: "button", onclick: () => {
      fechar();
      irParaProjeto(p.id, servico.etapa);
    } }, el("strong", { text: p.live.titulo || "Live" }), el("span", { class: `pill ${p.estado}`, text: ROTULOS[p.estado] || p.estado_texto })))),
    el("div", { class: "acoes" }, el("button", { class: "botao", type: "button", text: "Fechar", onclick: fechar })));
  dialogo.addEventListener("cancel", fechar);
  document.body.append(dialogo);
  dialogo.showModal();
}
