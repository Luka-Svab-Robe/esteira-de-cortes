/* Etapa "Textos": cada Short com vídeo, título, descrição e tags (com ✨ IA), "publicar este" e o editor
   para refazer o vídeo (gancho, ± segundos, música e modelo visual). Rascunhos ficam em memória até salvar. */
import { IDIOMAS, MODELOS, api, avisar, comCarregando, dataCurta, el, nomeMusica, postar, projetoUrl } from "./base.js";
import { campoComIA } from "./copiloto.js";
import { canal, estado } from "./estado.js";

export function rascunho(p, s) {
  if (!estado.rascunhos.has(p.id)) estado.rascunhos.set(p.id, {});
  const doProjeto = estado.rascunhos.get(p.id);
  if (!doProjeto[s.indice]) doProjeto[s.indice] = { titulo: s.titulo, descricao: s.descricao, tags: [...(s.tags || [])], selecionado: s.selecionado };
  return doProjeto[s.indice];
}
export const temRascunho = (p) => estado.rascunhos.has(p.id) && Object.keys(estado.rascunhos.get(p.id)).length > 0;

export function edicoes(p) {
  return p.shorts.map((s) => ({ indice: s.indice, ...rascunho(p, s) }));
}

export async function salvarTextos(p) {
  await api(`${projetoUrl(p)}/metadados`, { method: "PUT", body: JSON.stringify({ shorts: edicoes(p) }) });
  estado.rascunhos.delete(p.id);
}

export const sobreposicaoRefazendo = () =>
  el("div", { class: "refazendo" }, el("span", { class: "giro" }), el("span", { text: "Refazendo este Short…" }));

export function painelTextos(p, editavel, aoMudar) {
  if (!p.shorts.length) return el("p", { class: "pequeno", text: "Os Shorts aparecem aqui depois da edição." });
  const grade = el("div", { class: "shorts" }, p.shorts.map((s) => cardShort(p, s, editavel, aoMudar)));
  if (!editavel) return grade;
  const salvar = el("button", { class: "botao cta", type: "button", text: "💾 Salvar textos" });
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    try { await salvarTextos(p); avisar("Textos salvos.", "ok"); aoMudar(); } catch (e) { avisar(e.message, "erro"); }
  }));
  return el("div", {}, grade, el("div", { class: "acoes" }, salvar));
}

function cardShort(p, s, editavel, aoMudar) {
  const yt = s.youtube || {};
  const r = rascunho(p, s);
  const fig = el("figure", { class: `short ${r.selecionado ? "" : "fora"}`, "data-indice": s.indice });
  const tela = el("div", { class: "tela" }, el("video", { src: s.video ? `${s.video}#t=1` : "", controls: true, preload: "metadata", playsinline: true }));
  if (s.refazendo) tela.append(sobreposicaoRefazendo());
  fig.append(tela);
  const legenda = el("figcaption");
  const contexto = () => ({ canal: p.canal, projeto: p.id.split("/")[1], indice: s.indice, gancho: s.gancho, titulo: r.titulo });
  if (editavel) {
    const titulo = el("textarea", { rows: 2, maxlength: 100, value: r.titulo, oninput: (ev) => (r.titulo = ev.target.value) });
    const descricao = el("textarea", { rows: 4, value: r.descricao, oninput: (ev) => (r.descricao = ev.target.value) });
    const tags = el("input", { type: "text", value: r.tags.join(", "),
      oninput: (ev) => (r.tags = ev.target.value.split(",").map((t) => t.trim()).filter(Boolean)) });
    legenda.append(
      campoComIA("Título", titulo, "titulo", contexto),
      el("label", { class: "marcar" },
        el("input", { type: "checkbox", checked: r.selecionado, onchange: (ev) => { r.selecionado = ev.target.checked; fig.classList.toggle("fora", !r.selecionado); } }),
        "Publicar este"),
      el("details", { class: "mais-textos" }, el("summary", { text: "Descrição e tags" }),
        campoComIA("Descrição", descricao, "descricao", contexto),
        campoComIA("Tags (separe por vírgula)", tags, "tags", contexto)));
  } else {
    legenda.append(el("div", { class: "titulo-fixo", text: s.titulo }));
  }
  const meta = el("div", { class: "meta" }, el("span", { class: "etiqueta", text: s.duracao ? `${Math.round(s.duracao)} s` : "" }));
  if (s.trilha) meta.append(el("span", { class: "etiqueta", text: s.trilha === "nenhuma" ? "sem música" : `♫ ${nomeMusica(s.trilha)}` }));
  const modelo = MODELOS.find((m) => m.id === s.modelo);
  if (modelo) meta.append(el("span", { class: "etiqueta", text: `▣ ${modelo.nome}` }));
  if (s.traduzida_de) meta.append(el("span", { class: "etiqueta traducao", text: `traduzido do ${IDIOMAS[s.traduzida_de] || s.traduzida_de}` }));
  if (s.legenda_editada) meta.append(el("span", { class: "etiqueta ok", text: "✎ legenda ajustada" }));
  if (yt.publicar_em) meta.append(el("span", { class: "quando", text: `Sai ${dataCurta(yt.publicar_em)}` }));
  if (yt.url) meta.append(el("a", { href: yt.url, target: "_blank", rel: "noopener", text: "Ver no YouTube" }));
  legenda.append(meta);
  if (s.erro_refazer) legenda.append(el("div", { class: "aviso erro", text: `Não deu para refazer: ${s.erro_refazer}` }));
  fig.append(legenda);
  if (editavel) fig.append(editorVideo(p, s, fig, aoMudar));
  return fig;
}

function editorVideo(p, s, fig, aoMudar) {
  const c = canal() || {};
  const gancho = el("input", { type: "text", maxlength: 80, value: s.gancho || "" });
  const ajusteIni = el("input", { type: "number", step: "0.5", value: "0", "aria-label": "Ajuste do começo em segundos" });
  const ajusteFim = el("input", { type: "number", step: "0.5", value: "0", "aria-label": "Ajuste do fim em segundos" });
  const musica = el("select", { "aria-label": "Música" },
    el("option", { value: "nenhuma", text: "Sem música", selected: s.trilha === "nenhuma" }),
    (c.trilhas || []).map((t) => el("option", { value: t.nome, text: `${t.original ? "★ " : ""}${nomeMusica(t.nome)}`, selected: s.trilha === t.nome })));
  const modelo = el("select", { "aria-label": "Modelo visual" },
    MODELOS.map((m) => el("option", { value: m.id, text: `${m.nome} — ${m.dica}`, selected: (s.modelo || "padrao") === m.id })));
  const refazer = el("button", { class: "botao cta pequeno", type: "button", text: "↻ Refazer o vídeo" });
  refazer.addEventListener("click", () => comCarregando(refazer, "Enviando…", async () => {
    try {
      await postar(`${projetoUrl(p)}/shorts/${s.indice}/refazer`, { gancho: gancho.value.trim(),
        ajuste_inicio: Number(ajusteIni.value) || 0, ajuste_fim: Number(ajusteFim.value) || 0,
        trilha: musica.value, modelo: modelo.value });
      avisar(`Refazendo o Short ${s.indice}… leva cerca de 1 minuto.`, "ok");
      fig.querySelector(".tela").append(sobreposicaoRefazendo());
      aoMudar();
    } catch (e) { avisar(e.message, "erro"); }
  }));
  const contexto = () => ({ canal: p.canal, projeto: p.id.split("/")[1], indice: s.indice, titulo: s.titulo });
  return el("details", { class: "editor" },
    el("summary", { text: "✎ Mudar o vídeo deste Short" }),
    campoComIA("Gancho (texto grande no topo)", gancho, "gancho", contexto, "Use *palavra* para pintar uma palavra na cor de destaque."),
    el("div", { class: "lado-a-lado" },
      el("label", { class: "campo" }, "Começo (± segundos)", ajusteIni),
      el("label", { class: "campo" }, "Fim (± segundos)", ajusteFim)),
    el("label", { class: "campo" }, "Música", musica),
    el("label", { class: "campo" }, "Modelo visual", modelo),
    el("div", { class: "acoes" }, refazer));
}
