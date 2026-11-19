/* Aba Agendados → "Seus canais de publicação": cadastrar os canais do YouTube onde os Shorts são postados,
   conectar cada um e escolher quando ele publica (horários e dias da semana). */
import { $, avisar, comCarregando, el, gravar, postar } from "./base.js";
import { renderAgendados } from "./agendados.js";
import { abrirYoutube } from "./canal.js";
import { estado, guardarPreferencia } from "./estado.js";

const DIAS = [["seg", "Seg"], ["ter", "Ter"], ["qua", "Qua"], ["qui", "Qui"], ["sex", "Sex"], ["sab", "Sáb"], ["dom", "Dom"]];
const HORA = /^\d{2}:\d{2}$/;
let recarregar = async () => {};
let assinatura = "";

export function ligarPublicacao({ aoMudar }) {
  recarregar = aoMudar;
  montarCadastro();
}

/* ------------------------------------------------------------ cadastrar um canal */
function montarCadastro() {
  const raiz = $("#cadastrar-canal");
  if (!raiz) return;
  const nome = el("input", { type: "text", maxlength: "60", placeholder: "ex.: Cortes do Fulano", "aria-label": "Nome do canal" });
  const nicho = el("input", { type: "text", maxlength: "80", placeholder: "ex.: games, podcast, humor", "aria-label": "Assunto do canal" });
  const botao = el("button", { class: "botao cta", type: "submit", text: "＋ Cadastrar canal" });
  const form = el("form", { class: "form-cadastro", novalidate: true },
    el("div", { class: "grade-campos" },
      el("label", { class: "campo" }, "Nome do canal (como está no YouTube)", nome),
      el("label", { class: "campo" }, "Assunto (opcional)", nicho)),
    el("div", { class: "acoes" }, botao,
      el("span", { class: "pequeno", text: "Depois é só conectar o YouTube dele e escolher os horários." })));
  form.addEventListener("submit", (evento) => {
    evento.preventDefault();
    comCarregando(botao, "Cadastrando…", async () => {
      try {
        const r = await postar("/api/canais/novo", { nome: nome.value, nicho: nicho.value });
        estado.canalId = r.canal;
        guardarPreferencia("canal", r.canal);
        nome.value = nicho.value = "";
        avisar("Canal cadastrado! Agora conecte o YouTube dele e escolha os horários.", "ok");
        await recarregar();
        renderCanaisPublicacao(true);
        renderAgendados(true);
      } catch (e) {
        avisar(e.message, "erro");
      }
    });
  });
  raiz.replaceChildren(form);
}

/* ------------------------------------------------------------ lista de canais com a agenda de cada um */
export function renderCanaisPublicacao(forcar = false) {
  const lista = $("#lista-canais-publicacao");
  if (!lista) return;
  const canais = estado.dados ? estado.dados.canais : [];
  const nova = JSON.stringify(canais.map((c) => [c.id, c.nome, c.avatar, c.horarios, c.dias, c.youtube]));
  if (!forcar && (nova === assinatura || lista.contains(document.activeElement))) return;
  assinatura = nova;
  $("#caixa-cadastro").open = !canais.length;  // sem canal nenhum, o cadastro já aparece aberto
  if (!canais.length) {
    lista.replaceChildren(el("div", { class: "vazio" }, el("strong", { text: "Nenhum canal cadastrado" }),
      "Cadastre abaixo o canal do YouTube onde os Shorts vão ser postados."));
    return;
  }
  lista.replaceChildren(...canais.map(cartaoCanal));
}

function cartaoCanal(c) {
  const yt = c.youtube || {};
  let horarios = [...(c.horarios || [])].sort();
  const dias = new Set(c.dias && c.dias.length ? c.dias : DIAS.map(([d]) => d));

  const chips = el("div", { class: "horarios" });
  const desenharHorarios = () => chips.replaceChildren(...(horarios.length
    ? horarios.map((h) => el("span", { class: "horario" }, h,
      el("button", { type: "button", title: "Tirar este horário", "aria-label": `Tirar ${h}`, text: "✕",
        onclick: () => { horarios = horarios.filter((x) => x !== h); desenharHorarios(); } })))
    : [el("span", { class: "pequeno", text: "Nenhum horário: adicione pelo menos um." })]));
  desenharHorarios();

  const novaHora = el("input", { type: "time", value: "20:00", "aria-label": `Novo horário de ${c.nome}` });
  const adicionar = el("button", { class: "botao pequeno turquesa", type: "button", text: "＋ Adicionar horário" });
  adicionar.addEventListener("click", () => {
    const h = novaHora.value;
    if (!HORA.test(h)) return avisar("Escolha um horário, ex.: 20:00.", "erro");
    if (!horarios.includes(h)) horarios = [...horarios, h].sort();
    desenharHorarios();
  });

  const botoesDias = el("div", { class: "dias", role: "group", "aria-label": `Dias em que ${c.nome} publica` },
    ...DIAS.map(([id, rotulo]) => {
      const b = el("button", { class: `dia ${dias.has(id) ? "ativo" : ""}`, type: "button", "aria-pressed": String(dias.has(id)), text: rotulo });
      b.addEventListener("click", () => {
        if (dias.has(id)) dias.delete(id);
        else dias.add(id);
        b.classList.toggle("ativo", dias.has(id));
        b.setAttribute("aria-pressed", String(dias.has(id)));
      });
      return b;
    }));

  const salvar = el("button", { class: "botao cta pequeno", type: "button", text: "Salvar agenda" });
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    try {
      await gravar(`/api/canais/${encodeURIComponent(c.id)}/agenda`, { horarios, dias: [...dias] });
      avisar(`Agenda de ${c.nome} salva.`, "ok");
      await recarregar();
      renderCanaisPublicacao(true);
      renderAgendados(true);
    } catch (e) {
      avisar(e.message, "erro");
    }
  }));

  const conectar = el("button", { class: `botao pequeno ${yt.conectado ? "" : "cta"}`, type: "button",
    text: yt.conectado ? "YouTube" : "Conectar YouTube",
    onclick: () => { estado.canalId = c.id; guardarPreferencia("canal", c.id); abrirYoutube(); } });

  return el("article", { class: "canal-publi" },
    el("div", { class: "canal-publi-topo" },
      el("div", { class: "canal-publi-nome" },
        c.avatar ? el("img", { src: c.avatar, alt: "", class: "avatar" }) : el("div", { class: "avatar-letra", text: (c.nome || "?").slice(0, 1).toUpperCase() }),
        el("div", {}, el("strong", { text: c.nome }),
          el("div", { class: "pequeno", text: yt.conectado ? `YouTube conectado: ${yt.handle || yt.nome}`
            : "YouTube ainda não conectado: os Shorts deste canal não podem ser agendados." }))),
      conectar),
    el("div", { class: "campo-agenda" }, el("span", { class: "rotulo-agenda", text: "Horários de publicação" }), chips,
      el("div", { class: "linha-hora" }, novaHora, adicionar)),
    el("div", { class: "campo-agenda" }, el("span", { class: "rotulo-agenda", text: "Dias da semana" }), botoesDias),
    el("div", { class: "acoes" }, salvar,
      el("span", { class: "pequeno", text: "Um Short por horário livre, nos dias marcados. Vale para os próximos envios." })));
}
