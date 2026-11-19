/* Aba "Agendados": gestão do que já foi para o YouTube (agendados, publicados, retirados) e da fila de envio
   (Shorts prontos esperando o YouTube liberar), com o dia previsto. Um Short pode sair da agenda por aqui. */
import { $, api, avisar, comCarregando, confirmar, el, postar } from "./base.js";
import { canal } from "./estado.js";

const ATUALIZAR_MS = 60000;
let ultimo = 0;
let assinatura = "";

const DIA = new Intl.DateTimeFormat("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
const HORA = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });

function quando(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "sem data" : `${DIA.format(d)} · ${HORA.format(d)}`;
}

function origem(s) {
  return [s.dono || s.live, s.trecho ? `trecho ${s.trecho.replace("-", " até ")}` : "",
    s.rodada > 1 ? `rodada ${s.rodada}` : "", `Short ${s.indice}`].filter(Boolean).join(" · ");
}

function linha(s, { data, pill, acoes = [] }) {
  return el("li", { class: "item-agenda" },
    s.video ? el("video", { class: "mini", src: s.video, preload: "metadata", muted: true, playsinline: true,
      onmouseenter: (ev) => ev.currentTarget.play().catch(() => {}), onmouseleave: (ev) => ev.currentTarget.pause() })
      : el("div", { class: "mini" }),
    el("div", { class: "info" },
      el("span", { class: "data", text: data }),
      el("strong", { class: "titulo", text: s.titulo || "Short" }),
      el("span", { class: "pequeno", text: origem(s) }),
      s.motivo ? el("span", { class: "pequeno motivo", text: `Motivo: ${s.motivo}` }) : null),
    el("div", { class: "lado" }, pill, ...acoes));
}

function botaoYoutube(s) {
  return s.url ? el("a", { class: "botao pequeno", href: s.url, target: "_blank", rel: "noopener", text: "Ver no YouTube" }) : null;
}

function botaoRetirar(s) {
  const botao = el("button", { class: "botao pequeno perigo", type: "button", text: "Tirar da agenda" });
  botao.addEventListener("click", async () => {
    const ok = await confirmar(`Tirar “${s.titulo}” da agenda?`, { ok: "Tirar da agenda", perigo: true,
      detalhe: "O Short fica privado no YouTube (nada é apagado) e o horário dele fica livre para o próximo." });
    if (!ok) return;
    const [canalId, pasta] = s.projeto.split("/");
    comCarregando(botao, "Tirando…", async () => {
      try {
        await postar(`/api/agenda/${encodeURIComponent(canalId)}/${encodeURIComponent(pasta)}/${s.indice}/retirar`);
        avisar("Short fora da agenda (ficou privado).", "ok");
        renderAgendados(true);
      } catch (e) { avisar(e.message, "erro"); }
    });
  });
  return botao;
}

function secao(titulo, itens, vazio, montar) {
  return el("section", { class: "card secao-agenda" },
    el("h2", { class: "titulo-gta" }, titulo, " ", el("span", { class: "contador", text: String(itens.length) })),
    itens.length ? el("ul", { class: "lista lista-agenda-gestao" }, itens.map(montar)) : el("p", { class: "pequeno", text: vazio }));
}

export async function renderAgendados(forcar = false) {
  const raiz = $("#agendados");
  const c = canal();
  if (!c) { raiz.replaceChildren(el("div", { class: "vazio" }, el("strong", { text: "Nenhum canal ainda" }))); return; }
  if (!forcar && Date.now() - ultimo < ATUALIZAR_MS) return;
  ultimo = Date.now();
  let d;
  try {
    d = await api(`/api/agenda?canal=${encodeURIComponent(c.id)}`);
  } catch (e) {
    raiz.replaceChildren(el("div", { class: "aviso erro", text: e.message }));
    return;
  }
  const nova = JSON.stringify(d);
  if (nova === assinatura && !forcar) return;
  assinatura = nova;
  const horario = (d.horarios || []).join(", ");
  raiz.replaceChildren(...[
    el("div", { class: "resumo-agenda" },
      el("div", { class: "numero" }, el("strong", { text: String(d.agendados.length) }), "agendados"),
      el("div", { class: "numero" }, el("strong", { text: String(d.fila.length) }), "na fila de envio"),
      el("div", { class: "numero" }, el("strong", { text: String(d.publicados.length) }), "já publicados"),
      el("div", { class: "numero" }, el("strong", { text: String(d.retirados.length) }), "fora da agenda")),
    horario ? el("p", { class: "pequeno", text: `Horários de publicação deste canal: ${horario}. Para mudar, use "Seus canais de publicação", acima.` }) : null,
    secao("Agendados no YouTube", d.agendados, "Nada agendado ainda.",
      (s) => linha(s, { data: quando(s.quando), pill: el("span", { class: "pill agendado", text: "Agendado" }),
        acoes: [botaoYoutube(s), botaoRetirar(s)] })),
    secao("Na fila de envio", d.fila,
      "Nenhum Short esperando. Os prontos sobem sozinhos quando o YouTube libera o limite de envios do dia.",
      (s) => linha(s, { data: s.previsto ? `previsto ${quando(s.previsto)}` : "na fila",
        pill: el("span", { class: "pill aprovado", text: "Esperando envio", title: s.espera || "" }),
        acoes: s.espera ? [el("span", { class: "pequeno espera", text: s.espera })] : [] })),
    secao("Já publicados", d.publicados, "Nenhum publicado ainda.",
      (s) => linha(s, { data: quando(s.quando), pill: el("span", { class: "pill publicado", text: "No ar" }),
        acoes: [botaoYoutube(s), botaoRetirar(s)] })),
    secao("Fora da agenda", d.retirados, "Nenhum Short retirado.",
      (s) => linha(s, { data: quando(s.quando), pill: el("span", { class: "pill erro", text: "Privado" }), acoes: [botaoYoutube(s)] })),
  ].filter(Boolean));
}
