/* Telas de entrada: primeiro acesso (escolhe usar sem senha neste computador ou proteger com e-mail e senha) e login. */
import { $, avisar, el, postar } from "./base.js";

const MODOS = [
  ["aberto", "🔓 Usar sem senha", "Abre direto neste computador, sem login. Ideal se só você usa este PC."],
  ["protegido", "🔒 Proteger com e-mail e senha", "Pede login para entrar. Ideal se outras pessoas usam o PC ou se você quer ter uma equipe."],
];

export function mostrarEntrada(precisaSetup, aoEntrar) {
  const tela = $("#entrada");
  const erro = el("p", { class: "aviso erro oculto", role: "alert" });
  const campo = (rotulo, attrs) => el("label", { class: "campo" }, rotulo, el("input", attrs));
  const form = el("form", { class: "card entrada-card", novalidate: true });
  let modo = precisaSetup ? "" : "protegido";
  const nome = precisaSetup ? campo("Seu nome", { type: "text", name: "nome", autocomplete: "name", maxlength: "120" }) : null;
  const email = campo("E-mail", { type: "email", name: "email", autocomplete: "username", required: true });
  const senha = campo("Senha", { type: "password", name: "senha", minlength: "8", required: true,
    autocomplete: precisaSetup ? "new-password" : "current-password" });
  const repetir = precisaSetup ? campo("Repita a senha", { type: "password", name: "repetir", autocomplete: "new-password" }) : null;
  const camposLogin = el("div", { class: "campos-login" }, email, senha, repetir);
  const rotuloBotao = () => (!precisaSetup ? "Entrar" : modo === "aberto" ? "Começar sem senha" : "Criar minha conta");
  const botao = el("button", { class: "botao cta grande", type: "submit", text: rotuloBotao() });

  let escolha = null;
  if (precisaSetup) {
    escolha = el("div", { class: "modos", role: "radiogroup", "aria-label": "Como entrar no painel" });
    for (const [id, titulo, texto] of MODOS) {
      const b = el("button", { class: "modo", type: "button", role: "radio", "aria-checked": "false" },
        el("strong", { text: titulo }), el("span", { class: "pequeno", text: texto }));
      b.addEventListener("click", () => {
        modo = id;
        for (const x of escolha.children) { x.classList.toggle("escolhido", x === b); x.setAttribute("aria-checked", String(x === b)); }
        camposLogin.classList.toggle("oculto", id === "aberto");
        detalhes.classList.remove("oculto");
        botao.textContent = rotuloBotao();
        (nome.querySelector("input")).focus();
      });
      escolha.append(b);
    }
  }
  const detalhes = el("div", { class: `entrada-detalhes ${precisaSetup ? "oculto" : ""}` }, nome, camposLogin, erro, botao,
    precisaSetup ? el("p", { class: "pequeno", text: "Dá para mudar depois, na aba Conta." }) : null);

  form.append(...[  // a escolha só existe no primeiro acesso (null sairia escrito "null" na tela)
    el("h1", { class: "titulo-gta", text: precisaSetup ? "Primeiro acesso" : "Bem-vindo de volta" }),
    el("p", { class: "pequeno", text: precisaSetup
      ? "Como você quer entrar no painel? Os canais que já existirem passam a ser seus."
      : "Entre para ver as lives e os cortes do seu canal." }),
    escolha, detalhes].filter(Boolean));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    erro.classList.add("oculto");
    if (!modo) return;
    const dados = Object.fromEntries(new FormData(form));
    if (precisaSetup && modo === "protegido" && dados.senha !== dados.repetir) {
      erro.textContent = "As senhas não são iguais.";
      erro.classList.remove("oculto");
      return;
    }
    botao.disabled = true;
    botao.replaceChildren(el("span", { class: "giro" }), "Entrando…");
    try {
      const corpo = modo === "aberto" ? { modo, nome: dados.nome } : { modo, email: dados.email, senha: dados.senha, nome: dados.nome };
      const sessao = await postar(precisaSetup ? "/api/setup" : "/api/login", corpo);
      tela.classList.add("oculto");
      aoEntrar(sessao);
    } catch (e) {
      erro.textContent = e.message;
      erro.classList.remove("oculto");
    } finally {
      botao.disabled = false;
      botao.textContent = rotuloBotao();
    }
  });
  tela.replaceChildren(
    el("div", { class: "entrada-fundo" }, el("div", { class: "ceu" }), el("div", { class: "sol" }), el("div", { class: "chao" })),
    el("div", { class: "entrada-conteudo" },
      el("div", { class: "marca grande" }, el("span", { class: "nome titulo-gta", text: "Esteira de Cortes" })),
      form));
  tela.classList.remove("oculto");
  setTimeout(() => (form.querySelector(precisaSetup ? ".modo" : "input") || {}).focus?.(), 50);
}

export async function sair() {
  try { await postar("/api/logout"); } catch (_) { avisar("Saindo…"); }
  location.reload();
}
