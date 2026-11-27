/* Entrada do painel: sessão (login / primeiro acesso), abas Painel · Canais · Agendados · Serviços · Conta e atualização automática
   (rápida quando a equipe está trabalhando, lenta quando não; pausa com a aba escondida; ETag evita baixar de novo). */
import { $, api, avisar, quandoPerderSessao } from "./base.js";
import { alternarAuto, abrirYoutube, comporTrilhas, enviarMusica, ligarCanal, renderCanal } from "./canal.js";
import { ligarConta, renderConta } from "./conta.js";
import { mostrarEntrada, sair } from "./entrada.js";
import { canal, estado, guardarPreferencia, ocupado } from "./estado.js";
import { esquecerLives, ligarFontes, renderFontes } from "./fontes.js";
import { ligarLink, quandoComecar } from "./lives.js";
import { ligarPublicacao, renderCanaisPublicacao } from "./publicacao.js";
import { destacarDoEndereco, quandoPrecisarRecarregar, renderProjetos } from "./projetos.js";
import { renderServicos } from "./servicos.js";
import { renderAgendados } from "./agendados.js";
import { atualizarApis, renderApis } from "./apis.js";

const RAPIDO_MS = 2500, LENTO_MS = 15000;
let timer = null;

async function iniciar() {
  quandoPerderSessao(() => { clearTimeout(timer); location.reload(); });
  let sessao;
  try {
    sessao = await api("/api/sessao");
  } catch (_) {
    $("#abertura .texto").textContent = "Sem conexão com o painel";
    setTimeout(iniciar, 3000);
    return;
  }
  if (!sessao.usuario) {
    fecharAbertura();
    mostrarEntrada(sessao.precisa_setup, () => location.reload());
    return;
  }
  estado.sessao = sessao;
  montarCasca();
  await carregar(true);
  fecharAbertura();
}

function fecharAbertura() {
  $("#abertura").classList.add("saindo");
}

/* ------------------------------------------------------------ casca (topo e abas) */
function montarCasca() {
  $("#app").classList.remove("oculto");
  renderUsuario();
  quandoComecar(() => { carregar(true); const campo = $("#link-live"); if (campo) campo.value = ""; });
  ligarLink();
  quandoPrecisarRecarregar(() => carregar(true));
  ligarCanal({ aoMudar: () => carregar(true), aoConectarYoutube: () => { carregar(true); if (estado.aba === "apis") atualizarApis(); } });
  ligarFontes({ aoMudar: () => carregar(true) });
  ligarPublicacao({ aoMudar: () => carregar(true) });
  ligarConta({ aoMudar: () => carregar(true).then(() => estado.aba === "conta" && renderConta()),
    aoMudarSessao: (u) => { estado.sessao.usuario = { ...estado.sessao.usuario, ...u }; renderUsuario(); } });
  $("#arquivo-musica").addEventListener("change", enviarMusica);
  $("#btn-compor").addEventListener("click", (ev) => comporTrilhas(ev.currentTarget));
  $("#chave-vigia").addEventListener("change", (ev) => alternarAuto("automacao", { ativa: ev.target.checked }, ev.target));
  $("#chave-revisao").addEventListener("change", (ev) => alternarAuto("revisao", { obrigatoria: !ev.target.checked }, ev.target));
  $("#canal-chip").addEventListener("click", abrirYoutube);
  $("#btn-fechar-youtube").addEventListener("click", () => $("#dialogo-youtube").close());
  $("#seletor-canal").addEventListener("change", (ev) => {
    estado.canalId = ev.target.value;
    guardarPreferencia("canal", estado.canalId);
    esquecerLives();
    desenhar();
    renderFontes(true);
  });
  $("#btn-sair").addEventListener("click", sair);
  window.addEventListener("hashchange", aplicarEndereco);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) carregar(); });
  aplicarEndereco();
}

function renderUsuario() {
  const u = estado.sessao.usuario;
  const publico = u.publico || {};
  const nome = publico.nome_publico || publico.nome || u.nome;
  $("#usuario-nome").textContent = nome;
  $("#usuario-letra").textContent = (nome || "?").slice(0, 1).toUpperCase();
  $("#btn-sair").classList.toggle("oculto", estado.sessao.modo === "aberto");  // sem senha não há de onde sair
}

function aplicarEndereco() {
  const m = /^#\/(painel|canais|agendados|apis|servicos|conta)/.exec(location.hash);
  const aba = m ? m[1] : "painel";
  estado.aba = aba;
  for (const botao of document.querySelectorAll(".aba")) {
    const ativa = botao.dataset.aba === aba;
    botao.classList.toggle("ativa", ativa);
    botao.setAttribute("aria-current", ativa ? "page" : "false");
  }
  for (const tela of document.querySelectorAll(".tela-aba")) tela.classList.toggle("oculto", tela.id !== `tela-${aba}`);
  if (aba === "servicos") renderServicos();
  if (aba === "canais") renderFontes(true);
  if (aba === "agendados") { renderCanaisPublicacao(true); renderAgendados(true); }
  if (aba === "apis") renderApis();
  if (aba === "conta") renderConta();
  if (aba === "painel") destacarDoEndereco();
}

/* ------------------------------------------------------------ dados e atualização */
async function carregar(forcar = false) {
  clearTimeout(timer);
  try {
    const r = await fetch("/api/painel", { headers: !forcar && estado.etag ? { "If-None-Match": estado.etag } : {}, credentials: "same-origin" });
    if (r.status === 401) return location.reload();
    if (r.ok) {
      estado.etag = r.headers.get("ETag") || "";
      estado.dados = await r.json();
      desenhar();
    } else if (r.status !== 304) {
      throw new Error(String(r.status));
    }
    $("#sem-conexao").classList.add("oculto");
  } catch (_) {
    $("#sem-conexao").classList.remove("oculto");
  }
  if (!document.hidden) timer = setTimeout(carregar, ocupado() ? RAPIDO_MS : LENTO_MS);
}

function desenhar() {
  const c = canal();
  if (c && c.id !== estado.canalId) estado.canalId = c.id;
  renderCanal();
  renderProjetos();
  renderFontes();
  if (estado.aba === "servicos") renderServicos();
  if (estado.aba === "agendados") { renderCanaisPublicacao(); renderAgendados(); }
}

/* ------------------------------------------------------------ app instalável */
let pedidoInstalacao = null;
window.addEventListener("beforeinstallprompt", (ev) => {
  ev.preventDefault();  // mostramos o nosso botão no menu da conta
  pedidoInstalacao = ev;
  $("#btn-instalar").classList.remove("oculto");
});
window.addEventListener("appinstalled", () => {
  $("#btn-instalar").classList.add("oculto");
  avisar("App instalado! Ele aparece no menu do computador.", "ok");
});
document.addEventListener("click", async (ev) => {
  if (!ev.target.closest("#btn-instalar") || !pedidoInstalacao) return;
  pedidoInstalacao.prompt();
  await pedidoInstalacao.userChoice;
  pedidoInstalacao = null;
  $("#btn-instalar").classList.add("oculto");
});
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

window.addEventListener("error", () => avisar("Algo saiu errado na tela. Recarregue a página se continuar.", "erro"));
iniciar();
