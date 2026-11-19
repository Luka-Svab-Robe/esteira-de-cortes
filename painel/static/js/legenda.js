/* Etapa "Legenda": escolha o Short, assista e corrija o texto (ou o tempo) de cada linha; o vídeo é refeito
   com a sua legenda. Dá para voltar à legenda automática a qualquer momento. */
import { IDIOMAS, api, avisar, comCarregando, confirmar, el, gravar, projetoUrl } from "./base.js";

const umaCasa = (v) => Math.round(Number(v) * 10) / 10;

export function painelLegenda(p, aoMudar) {
  const raiz = el("div", { class: "legenda-painel" });
  raiz.addEventListener("input", () => (raiz.dataset.sujo = "1"));  // você está mexendo: o painel não se redesenha
  if (!p.shorts.length) {
    raiz.append(el("p", { class: "pequeno", text: "A legenda aparece aqui depois que os Shorts forem editados." }));
    return raiz;
  }
  const area = el("div", { class: "legenda-area" });
  const escolha = el("div", { class: "chips", role: "tablist", "aria-label": "Escolha o Short" });
  const abrir = (indice) => {
    for (const b of escolha.children) b.classList.toggle("ativo", Number(b.dataset.indice) === indice);
    carregar(p, indice, area, raiz, aoMudar);
  };
  for (const s of p.shorts) {
    escolha.append(el("button", { class: "chip", type: "button", role: "tab", "data-indice": s.indice,
      text: `Short ${s.indice}${s.legenda_editada ? " ✎" : ""}`, onclick: async () => {
        if (raiz.dataset.sujo && !(await confirmar("Trocar de Short sem salvar as mudanças da legenda?", { ok: "Trocar" }))) return;
        delete raiz.dataset.sujo;
        abrir(s.indice);
      } }));
  }
  raiz.append(escolha, area);
  abrir(p.shorts[0].indice);
  return raiz;
}

async function carregar(p, indice, area, raiz, aoMudar) {
  area.replaceChildren(el("div", { class: "esqueleto", style: "height:220px" }));
  let dados;
  try { dados = await api(`${projetoUrl(p)}/shorts/${indice}/legenda`); }
  catch (e) { area.replaceChildren(el("div", { class: "aviso erro", text: e.message })); return; }
  const video = el("video", { class: "legenda-video", src: dados.video ? `${dados.video}#t=0.5` : "", controls: true, preload: "metadata", playsinline: true });
  const linhas = el("div", { class: "linhas-legenda" });
  const itens = [];
  const duracao = Number(dados.duracao) || 0;

  const adicionar = (f, depoisDe = null) => {
    const item = {
      texto: el("input", { type: "text", maxlength: "200", value: f.texto, "aria-label": "Texto da linha" }),
      ini: el("input", { type: "number", step: "0.1", min: "0", max: String(duracao), value: umaCasa(f.inicio), "aria-label": "Começa em (segundos)" }),
      fim: el("input", { type: "number", step: "0.1", min: "0", max: String(duracao), value: umaCasa(f.fim), "aria-label": "Termina em (segundos)" }),
    };
    const tocar = el("button", { class: "botao pequeno tempo", type: "button", title: "Ver esta linha no vídeo",
      text: `▶ ${Number(item.ini.value).toFixed(1)}s`, onclick: () => { video.currentTime = Number(item.ini.value) || 0; video.play().catch(() => {}); } });
    item.ini.addEventListener("input", () => (tocar.textContent = `▶ ${(Number(item.ini.value) || 0).toFixed(1)}s`));
    item.no = el("div", { class: "linha-legenda" }, tocar, item.texto,
      el("label", { class: "tempo-campo" }, "de", item.ini), el("label", { class: "tempo-campo" }, "até", item.fim),
      el("button", { class: "botao pequeno", type: "button", title: "Nova linha logo depois desta", "aria-label": "Nova linha depois desta", text: "＋", onclick: () => {
        const fim = Number(item.fim.value) || 0;
        adicionar({ inicio: fim, fim: Math.min(duracao || fim + 1.5, fim + 1.5), texto: "" }, item);
        raiz.dataset.sujo = "1";
      } }),
      el("button", { class: "botao pequeno perigo", type: "button", title: "Apagar esta linha", "aria-label": "Apagar esta linha", text: "✕", onclick: () => {
        itens.splice(itens.indexOf(item), 1); item.no.remove(); raiz.dataset.sujo = "1";
      } }));
    if (depoisDe) { itens.splice(itens.indexOf(depoisDe) + 1, 0, item); depoisDe.no.after(item.no); }
    else { itens.push(item); linhas.append(item.no); }
    item.texto.addEventListener("focus", () => { video.currentTime = Number(item.ini.value) || 0; });
  };
  dados.frases.forEach((f) => adicionar(f));
  if (!dados.frases.length) adicionar({ inicio: 0, fim: Math.min(2, duracao || 2), texto: "" });

  // a linha que está tocando fica destacada
  video.addEventListener("timeupdate", () => {
    const t = video.currentTime;
    for (const item of itens) item.no.classList.toggle("tocando", t >= Number(item.ini.value) && t < Number(item.fim.value));
  });

  const enviar = (corpo, mensagem, botao) => comCarregando(botao, "Enviando…", async () => {
    try {
      await gravar(`${projetoUrl(p)}/shorts/${indice}/legenda`, corpo);
      delete raiz.dataset.sujo;
      avisar(mensagem, "ok");
      aoMudar("textos");
    } catch (e) { avisar(e.message, "erro"); }
  });
  const salvar = el("button", { class: "botao cta", type: "button", text: "💾 Salvar e refazer o vídeo" });
  salvar.addEventListener("click", () => enviar({ frases: itens.map((i) => ({ inicio: Number(i.ini.value), fim: Number(i.fim.value), texto: i.texto.value })) },
    `Refazendo o Short ${indice} com a sua legenda… leva cerca de 1 minuto.`, salvar));
  const automatica = el("button", { class: "botao", type: "button", text: "↺ Voltar à legenda automática" });
  automatica.addEventListener("click", async () => {
    if (!(await confirmar("Descartar a sua legenda e voltar à automática?", { ok: "Voltar à automática", perigo: true }))) return;
    enviar({ automatica: true }, `Refazendo o Short ${indice} com a legenda automática…`, automatica);
  });
  if (!dados.editavel) { salvar.disabled = true; automatica.disabled = true; }
  const origem = dados.editada ? "✎ Legenda ajustada por você"
    : dados.traduzida_de ? `Legenda automática, traduzida do ${IDIOMAS[dados.traduzida_de] || dados.traduzida_de}` : "Legenda automática";
  area.replaceChildren(el("div", { class: "legenda-grade" },
    el("div", { class: "legenda-lado" }, video,
      el("p", { class: "pequeno", text: "Toque no ▶ de uma linha para ver o trecho. A linha que está tocando fica destacada." })),
    el("div", { class: "legenda-edicao" },
      el("div", { class: "legenda-topo" }, el("span", { class: `etiqueta ${dados.editada ? "ok" : ""}`, text: origem })),
      el("p", { class: "pequeno", text: "Corrija palavras, nomes e gírias, apague linhas ou crie novas. Os tempos são em segundos desde o começo do Short." }),
      linhas,
      el("div", { class: "acoes" }, salvar, dados.editada ? automatica : null),
      dados.editavel ? null : el("p", { class: "pequeno", text: "Este Short já foi para o YouTube ou está sendo refeito: a legenda não pode mudar agora." }))));
}
