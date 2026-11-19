/* Telas de entrada: primeiro acesso (cria o administrador) e login. */
import { $, avisar, el, postar } from "./base.js";

export function mostrarEntrada(precisaSetup, aoEntrar) {
  const tela = $("#entrada");
  const erro = el("p", { class: "aviso erro oculto", role: "alert" });
  const campo = (rotulo, attrs) => el("label", { class: "campo" }, rotulo, el("input", attrs));
  const form = el("form", { class: "card entrada-card", novalidate: true });
  const nome = precisaSetup ? campo("Seu nome", { type: "text", name: "nome", autocomplete: "name", required: true }) : null;
  const email = campo("E-mail", { type: "email", name: "email", autocomplete: "username", required: true });
  const senha = campo("Senha", { type: "password", name: "senha", minlength: "8", required: true,
    autocomplete: precisaSetup ? "new-password" : "current-password" });
  const repetir = precisaSetup ? campo("Repita a senha", { type: "password", name: "repetir", autocomplete: "new-password" }) : null;
  const botao = el("button", { class: "botao cta grande", type: "submit", text: precisaSetup ? "Criar minha conta" : "Entrar" });
  form.append(
    el("h1", { class: "titulo-gta", text: precisaSetup ? "Primeiro acesso" : "Bem-vindo de volta" }),
    el("p", { class: "pequeno", text: precisaSetup
      ? "Crie a conta de administrador. Os canais que já existem passam a ser seus; depois você convida a equipe."
      : "Entre para ver as lives e os cortes do seu canal." }),
    nome, email, senha, repetir, erro, botao);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    erro.classList.add("oculto");
    const dados = Object.fromEntries(new FormData(form));
    if (precisaSetup && dados.senha !== dados.repetir) {
      erro.textContent = "As senhas não são iguais.";
      erro.classList.remove("oculto");
      return;
    }
    botao.disabled = true;
    botao.replaceChildren(el("span", { class: "giro" }), "Entrando…");
    try {
      const sessao = await postar(precisaSetup ? "/api/setup" : "/api/login",
        { email: dados.email, senha: dados.senha, nome: dados.nome });
      tela.classList.add("oculto");
      aoEntrar(sessao);
    } catch (e) {
      erro.textContent = e.message;
      erro.classList.remove("oculto");
    } finally {
      botao.disabled = false;
      botao.textContent = precisaSetup ? "Criar minha conta" : "Entrar";
    }
  });
  tela.replaceChildren(
    el("div", { class: "entrada-fundo" }, el("div", { class: "ceu" }), el("div", { class: "sol" }), el("div", { class: "chao" })),
    el("div", { class: "entrada-conteudo" },
      el("div", { class: "marca grande" }, el("span", { class: "nome titulo-gta", text: "Esteira de Cortes" })),
      form));
  tela.classList.remove("oculto");
  setTimeout(() => (form.querySelector("input") || {}).focus?.(), 50);
}

export async function sair() {
  try { await postar("/api/logout"); } catch (_) { avisar("Saindo…"); }
  location.reload();
}
