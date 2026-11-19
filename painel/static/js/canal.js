/* Canal: selo do YouTube no topo, agenda, músicas, modo automático e a janela de conexão com o Google. */
import { $, avisar, comCarregando, confirmar, dataCurta, el, nomeMusica, postar } from "./base.js";
import { canal, estado } from "./estado.js";

let recarregar = () => {};
let aoConectar = () => {};
export function ligarCanal({ aoMudar, aoConectarYoutube }) { recarregar = aoMudar; aoConectar = aoConectarYoutube; }

export function renderCanal() {
  renderSelo();
  renderAgenda();
  renderMusicas();
  renderAuto();
  if ($("#dialogo-youtube").open) renderYoutube();
}

/* ------------------------------------------------------------ selo do canal no topo */
function renderSelo() {
  const c = canal();
  const chip = $("#canal-chip");
  chip.className = "canal-chip";
  const foto = $("#canal-foto");
  if (c && c.avatar && foto.getAttribute("src") !== c.avatar) foto.src = c.avatar;
  foto.classList.toggle("oculto", !(c && c.avatar));
  const texto = $("#canal-chip-texto");
  if (!c) { chip.classList.add("alerta"); texto.textContent = "Crie um canal na Conta"; return; }
  const yt = c.youtube || {};
  if (yt.em_andamento) { chip.classList.add("alerta"); texto.textContent = "Conectando o YouTube…"; }
  else if (yt.conectado) { chip.classList.add("ok"); texto.textContent = yt.handle || yt.nome || c.nome; }
  else { chip.classList.add("alerta"); texto.textContent = "Conectar YouTube"; }
  const seletor = $("#seletor-canal");
  const canais = estado.dados.canais;
  seletor.classList.toggle("oculto", canais.length < 2);
  if (canais.length > 1 && seletor.options.length !== canais.length) {
    seletor.replaceChildren(...canais.map((x) => el("option", { value: x.id, text: x.nome })));
  }
  seletor.value = c.id;
}

/* ------------------------------------------------------------ agenda */
function renderAgenda() {
  const lista = $("#lista-agenda");
  const itens = estado.dados.agenda.slice(0, 10);
  lista.replaceChildren(...(itens.length ? itens.map((a) => el("li", {},
    el("span", { class: "data", text: dataCurta(a.quando) }),
    el("span", { class: "titulo" }, a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener", text: a.titulo }) : a.titulo)))
    : [el("li", { class: "pequeno", text: "Nada agendado ainda." })]));
}

/* ------------------------------------------------------------ músicas */
let assinaturaMusicas = "";
function renderMusicas() {
  const c = canal();
  const trilhas = c ? c.trilhas || [] : [];
  const assinatura = JSON.stringify([c && c.id, trilhas]);
  if (assinatura === assinaturaMusicas) return;  // não reinicia o player à toa
  assinaturaMusicas = assinatura;
  const lista = $("#lista-musicas");
  if (!trilhas.length) { lista.replaceChildren(el("li", { class: "pequeno", text: "Nenhuma música ainda. Toque em “Compor trilhas originais”." })); return; }
  lista.replaceChildren(...trilhas.map((t) => el("li", {},
    el("div", { class: "linha" },
      el("strong", { text: nomeMusica(t.nome) }),
      el("span", {}, t.original ? el("span", { class: "selo-original", text: "ORIGINAL" }) : null, " ",
        el("button", { class: "botao pequeno perigo", type: "button", text: "Remover", onclick: () => removerMusica(t) }))),
    el("audio", { src: t.url, controls: true, preload: "none" }),
    t.credito ? el("div", { class: "pequeno", text: `Crédito: ${t.credito}` }) : null)));
}

async function removerMusica(t) {
  if (!(await confirmar(`Remover a música “${nomeMusica(t.nome)}” do canal?`, { ok: "Remover", perigo: true }))) return;
  try { await postar(`/api/canais/${canal().id}/trilhas/remover`, { nome: t.nome }); avisar("Música removida."); recarregar(); }
  catch (e) { avisar(e.message, "erro"); }
}

export async function enviarMusica(ev) {
  const arquivo = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!arquivo) return;
  const credito = $("#credito-musica").value.trim();
  avisar("Enviando a música…");
  try {
    const r = await fetch(`/api/canais/${canal().id}/trilhas?nome=${encodeURIComponent(arquivo.name)}&credito=${encodeURIComponent(credito)}`,
      { method: "POST", body: arquivo, headers: { "Content-Type": "application/octet-stream", "X-Esteira": "1" }, credentials: "same-origin" });
    const corpo = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((corpo.detail && corpo.detail.mensagem) || "Não consegui enviar a música.");
    $("#credito-musica").value = "";
    avisar("Música adicionada ao canal.", "ok");
    recarregar();
  } catch (e) { avisar(e.message, "erro"); }
}

export function comporTrilhas(botao) {
  return comCarregando(botao, "Compondo…", async () => {
    try {
      const r = await postar(`/api/canais/${canal().id}/trilhas/gerar`);
      avisar(r.criadas.length ? `${r.criadas.length} trilha(s) nova(s) criada(s).` : "O canal já tem todas as trilhas originais.", "ok");
      recarregar();
    } catch (e) { avisar(e.message, "erro"); }
  });
}

/* ------------------------------------------------------------ modo automático */
function renderAuto() {
  const c = canal();
  const vigia = $("#chave-vigia"), revisao = $("#chave-revisao");
  vigia.disabled = revisao.disabled = !c;
  if (!c) return;
  vigia.checked = c.automacao.ativa;
  const m = /(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})/.exec(c.automacao.proxima || "");
  const vigiados = (c.fontes || []).filter((f) => f.monitorar).length;
  $("#texto-vigia").textContent = !c.automacao.ativa ? "Desligado: só corta as lives que você escolher."
    : !vigiados ? "Ligado, mas nenhum canal está marcado para vigiar: marque na aba Canais."
      : `Procura live nova a cada ${c.automacao.intervalo} min em ${vigiados} canal(is)${m ? ` (próxima: ${m[3]}/${m[2]} às ${m[4]})` : ""} e corta sozinho.`;
  revisao.checked = !c.revisao;
  $("#texto-revisao").textContent = c.revisao ? "Desligado: os Shorts esperam você aprovar." : "Os Shorts vão direto para a agenda do YouTube.";
}

export async function alternarAuto(caminho, corpo, chave) {
  if (caminho === "revisao" && corpo.obrigatoria === false &&
      !(await confirmar("Agendar os Shorts sem você revisar?", { ok: "Sim, agendar sozinho", detalhe: "Cada live cortada vai direto para a agenda do YouTube." }))) {
    chave.checked = false;
    return;
  }
  chave.disabled = true;
  try { await postar(`/api/canais/${canal().id}/${caminho}`, corpo); avisar("Pronto, ajuste salvo.", "ok"); }
  catch (e) { chave.checked = !chave.checked; avisar(e.message, "erro"); }
  finally { chave.disabled = false; recarregar(); }
}

/* ------------------------------------------------------------ YouTube */
let conectando = false;
export function abrirYoutube() {
  renderYoutube();
  $("#dialogo-youtube").showModal();
}

export function renderYoutube() {
  const c = canal();
  const corpo = $("#youtube-corpo");
  if (!c) { corpo.replaceChildren(el("p", { text: "Crie um canal na aba Conta primeiro." })); return; }
  const yt = c.youtube || {};
  if (conectando && !yt.em_andamento && yt.conectado && !yt.erro) {
    conectando = false;
    avisar(`YouTube conectado: ${yt.nome}`, "ok");
    aoConectar();
  }
  if (yt.em_andamento && yt.url) {
    corpo.replaceChildren(
      el("ol", {},
        el("li", { text: "Toque no botão abaixo (abre o Google numa aba nova)." }),
        el("li", { text: "Escolha a conta e o canal do YouTube." }),
        el("li", { text: "Se aparecer “O Google não verificou este app”, toque em Avançado → Acessar." }),
        el("li", { text: "Marque as permissões e toque em Continuar." })),
      el("a", { class: "botao cta", href: yt.url, target: "_blank", rel: "noopener", text: "Abrir a página do Google" }),
      el("p", { class: "pequeno", text: "Esta janela atualiza sozinha quando terminar." }));
    return;
  }
  if (yt.em_andamento) {
    corpo.replaceChildren(el("p", { class: "linha-progresso" }, el("span", { class: "giro" }), "Preparando o link do Google…"));
    return;
  }
  const botao = el("button", { class: "botao cta", type: "button", text: yt.conectado ? "Reconectar" : "Conectar canal do YouTube" });
  botao.addEventListener("click", async () => {
    try { conectando = true; await postar(`/api/canais/${c.id}/youtube`); recarregar(); }
    catch (e) { conectando = false; avisar(e.message, "erro"); }
  });
  corpo.replaceChildren(...[
    yt.erro ? el("p", { class: "aviso erro", text: yt.erro }) : null,
    yt.conectado
      ? el("p", {}, "Vinculado a ", el("strong", { text: yt.nome || "seu canal" }), yt.handle ? ` (${yt.handle})` : "", ". As lives dele aparecem no painel.")
      : el("p", { text: "Conecte o canal do YouTube: as lives dele aparecem aqui para cortar e os Shorts são publicados nele." }),
    yt.conectado ? el("p", { class: "pequeno", text: "Enquanto o app do Google estiver em modo Teste, a conexão vence a cada 7 dias. Se algo falhar, reconecte aqui." }) : null,
    botao].filter(Boolean));
}
