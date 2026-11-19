/* Etapa "Momentos": escolher/ajustar à mão os trechos que viram Shorts, com prévia do vídeo da live,
   sugestões do analista (candidatos) e o copiloto para gancho e título. */
import { api, avisar, comCarregando, el, gravar, postar, projetoUrl, relogio, segundos } from "./base.js";
import { campoComIA } from "./copiloto.js";

/* mesmo momento = dividem mais da metade do tempo do menor (a mesma regra da esteira: analise.repete) */
const REPETIDO_FRACAO = 0.5;
function repete(a, b) {
  const comum = Math.min(a.fim, b.fim) - Math.max(a.inicio, b.inicio);
  const menor = Math.min(a.fim - a.inicio, b.fim - b.inicio);
  return menor > 0 && comum > REPETIDO_FRACAO * menor;
}

function avisoDeSalvo(resposta, texto) {
  const n = (resposta && resposta.repetidos) || 0;
  return n ? `${texto} ${n} momento(s) repetido(s) tirado(s): era o mesmo trecho de outro Short.` : texto;
}

export async function painelMomentos(p, aoMudar) {
  const raiz = el("div", { class: "momentos-painel" }, el("div", { class: "esqueleto", style: "height:160px" }));
  raiz.addEventListener("input", () => (raiz.dataset.sujo = "1"));  // você está mexendo: o painel não se redesenha
  let dados;
  try {
    dados = await api(`${projetoUrl(p)}/momentos`);
  } catch (e) {
    raiz.replaceChildren(el("div", { class: "aviso erro", text: e.message }));
    return raiz;
  }
  const video = dados.video ? el("video", { class: "previa-live", src: dados.video, controls: true, preload: "metadata", playsinline: true }) : null;
  const tocar = (inicio) => {  // os tempos dos momentos já estão no relógio do arquivo baixado
    if (!video) return;
    video.currentTime = Math.max(0, inicio);
    video.play().catch(() => {});
    video.scrollIntoView({ block: "nearest" });
  };
  const lista = el("div", { class: "momentos" });
  const linhas = [];
  const itensSugestao = [];  // {c: candidato, no: elemento}; preenchido mais abaixo
  let resumoSugestoes = null;
  const tempos = (l) => ({ inicio: segundos(l.ini.value), fim: segundos(l.fim.value) });
  const valido = (m) => m.inicio !== null && m.fim !== null && m.fim > m.inicio;
  /* some com a sugestão que já virou momento e marca o momento que repete outro (o mesmo trecho duas vezes) */
  function atualizarRepetidos() {
    const escolhidos = [];
    for (const linha of linhas) {
      const m = tempos(linha);
      const repetido = valido(m) && escolhidos.some((outro) => repete(m, outro));
      linha.repetido.hidden = !repetido;
      if (valido(m) && !repetido) escolhidos.push(m);
    }
    let livres = 0;
    for (const item of itensSugestao) {
      item.no.hidden = escolhidos.some((m) => repete(m, item.c));
      if (!item.no.hidden) livres += 1;
    }
    if (resumoSugestoes) {
      const usadas = itensSugestao.length - livres;
      resumoSugestoes.textContent = `Sugestões do analista (${livres})${usadas ? ` · ${usadas} já escolhida(s), escondida(s)` : ""}`;
    }
  }
  const contexto = (linha) => () => ({ canal: p.canal, projeto: p.id.split("/")[1], inicio: segundos(linha.ini.value),
    fim: segundos(linha.fim.value), gancho: linha.gancho.value, titulo: linha.titulo.value });

  function adicionar(m) {
    const linha = {
      ini: el("input", { type: "text", value: relogio(m.inicio), "aria-label": "Começo", inputmode: "numeric" }),
      fim: el("input", { type: "text", value: relogio(m.fim), "aria-label": "Fim", inputmode: "numeric" }),
      gancho: el("input", { type: "text", maxlength: "60", value: m.gancho || "", placeholder: "Texto grande no topo" }),
      titulo: el("input", { type: "text", maxlength: "90", value: m.titulo || "", placeholder: "Título no YouTube" }),
      extra: { descricao: m.descricao || "", tags: m.tags || [], trilha: m.trilha, modelo: m.modelo },
    };
    const duracao = el("span", { class: "etiqueta" });
    linha.repetido = el("span", { class: "etiqueta ruim", text: "repetido: sai ao salvar", hidden: true,
      title: "Mais da metade deste trecho já está em outro momento; só o primeiro vira Short." });
    const atualizarDuracao = () => {
      const a = segundos(linha.ini.value), b = segundos(linha.fim.value);
      duracao.textContent = a !== null && b !== null && b > a ? `${Math.round(b - a)} s` : "tempo inválido";
      duracao.classList.toggle("ruim", !(a !== null && b !== null && b > a));
      atualizarRepetidos();
    };
    linha.ini.addEventListener("input", atualizarDuracao);
    linha.fim.addEventListener("input", atualizarDuracao);
    const no = el("div", { class: "momento" },
      el("div", { class: "momento-tempo" },
        el("button", { class: "botao pequeno", type: "button", text: "▶", title: "Ver este trecho", onclick: () => tocar(segundos(linha.ini.value) || 0) }),
        linha.ini, el("span", { class: "pequeno", text: "até" }), linha.fim, duracao, linha.repetido,
        el("button", { class: "botao pequeno perigo", type: "button", text: "Tirar", onclick: () => {
          linhas.splice(linhas.indexOf(linha), 1); no.remove(); raiz.dataset.sujo = "1"; atualizarRepetidos(); } })),
      m.texto ? el("p", { class: "fala", text: `“${m.texto.slice(0, 220)}${m.texto.length > 220 ? "…" : ""}”` }) : null,
      el("div", { class: "lado-a-lado" },
        campoComIA("Gancho", linha.gancho, "gancho", contexto(linha), "Use *palavra* para destacar."),
        campoComIA("Título", linha.titulo, "titulo", contexto(linha))));
    linha.no = no;
    linhas.push(linha);
    lista.append(no);
    atualizarDuracao();  // já com a linha na lista: confere repetição e esconde a sugestão usada
    if (m.manual_novo) raiz.dataset.sujo = "1";
  }

  (dados.cortes.length ? dados.cortes : []).forEach(adicionar);
  if (!dados.cortes.length) lista.append(el("p", { class: "pequeno vazio-momentos", text: "Nenhum momento ainda. Peça à IA ou escolha nas sugestões abaixo." }));

  for (const c of dados.candidatos) {
    itensSugestao.push({ c, no: el("div", { class: "candidato" },
      el("button", { class: "botao pequeno", type: "button", text: "▶", onclick: () => tocar(c.inicio) }),
      el("span", { class: "etiqueta", text: `${relogio(c.inicio)}–${relogio(c.fim)} · ${Math.round(c.fim - c.inicio)} s` }),
      el("span", { class: "fala", text: c.texto.slice(0, 140) }),
      el("button", { class: "botao pequeno turquesa", type: "button", text: "＋ Usar", onclick: () => {
        lista.querySelector(".vazio-momentos")?.remove();
        adicionar({ inicio: c.inicio, fim: c.fim, gancho: "", titulo: "", texto: c.texto, manual_novo: true });
      } })) });
  }
  resumoSugestoes = el("summary");
  const sugestoes = el("details", { class: "candidatos" }, resumoSugestoes,
    el("div", { class: "lista-candidatos" }, itensSugestao.map((item) => item.no)));
  atualizarRepetidos();

  const coletar = () => linhas.map((l) => ({ inicio: segundos(l.ini.value), fim: segundos(l.fim.value),
    gancho: l.gancho.value.trim(), titulo: l.titulo.value.trim(), ...l.extra }));
  const salvar = el("button", { class: "botao", type: "button", text: "💾 Salvar momentos" });
  const editar = el("button", { class: "botao cta", type: "button", text: "🎬 Salvar e editar os Shorts" });
  const ia = el("button", { class: "botao ia", type: "button", text: "✨ IA escolhe os momentos" });
  const salvarMomentos = async () => {
    const cortes = coletar();
    if (cortes.some((c) => c.inicio === null || c.fim === null)) throw new Error("Confira os tempos (formato 12:30 ou 1:02:30).");
    const resposta = await gravar(`${projetoUrl(p)}/momentos`, { cortes });
    delete raiz.dataset.sujo;  // salvo: o painel pode se atualizar de novo
    return resposta;
  };
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    try { avisar(avisoDeSalvo(await salvarMomentos(), "Momentos salvos."), "ok"); aoMudar(); } catch (e) { avisar(e.message, "erro"); }
  }));
  editar.addEventListener("click", () => comCarregando(editar, "Enviando…", async () => {
    try {
      const resposta = await salvarMomentos();
      await postar(`${projetoUrl(p)}/etapas/edicao`, {});
      avisar(avisoDeSalvo(resposta, "O editor começou. Acompanhe a barra de progresso."), "ok");
      aoMudar("editar");
    } catch (e) { avisar(e.message, "erro"); }
  }));
  ia.addEventListener("click", () => comCarregando(ia, "Chamando o analista…", async () => {
    try {
      await postar(`${projetoUrl(p)}/etapas/analise`, { parar: true });
      delete raiz.dataset.sujo;
      avisar("O analista está escolhendo os momentos. Eles aparecem aqui quando terminar.", "ok");
      aoMudar();
    } catch (e) { avisar(e.message, "erro"); }
  }));
  if (!dados.editavel) for (const b of [salvar, editar, ia]) b.disabled = true;
  raiz.replaceChildren(
    el("div", { class: "momentos-topo" },
      el("p", { class: "pequeno", text: `Cada Short entre ${dados.limites.duracao_min} e ${dados.limites.duracao_max} s (até ${dados.limites.maximo}). Ajuste o começo e o fim, escreva o gancho ou peça ✨ à IA.` }),
      ia),
    video, lista, sugestoes,
    el("div", { class: "acoes" }, salvar, editar));
  return raiz;
}
