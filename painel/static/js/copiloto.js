/* Botão "✨ IA sugere": pede 3 opções ao copiloto e aplica a escolhida no campo (a decisão é sempre sua). */
import { avisar, el, postar } from "./base.js";

/* um único ouvinte fecha as listas abertas quando você clica fora delas */
document.addEventListener("pointerdown", (ev) => {
  for (const lista of document.querySelectorAll(".sugestoes:not(.oculto)")) {
    if (!lista.parentElement.contains(ev.target)) lista.classList.add("oculto");
  }
});

const MOTORES = { regras: "sugestões automáticas (sem IA conectada)", "IA local": "IA local", Claude: "Claude" };

export function botaoIA(campo, contexto, aplicar, rotulo = "✨ IA sugere") {
  const caixa = el("div", { class: "copiloto" });
  const botao = el("button", { class: "botao pequeno ia", type: "button", text: rotulo });
  const lista = el("div", { class: "sugestoes oculto", role: "listbox" });
  caixa.addEventListener("keydown", (ev) => { if (ev.key === "Escape") lista.classList.add("oculto"); });
  botao.addEventListener("click", async () => {
    botao.disabled = true;
    botao.replaceChildren(el("span", { class: "giro" }), "Pensando…");
    const lento = setTimeout(() => botao.replaceChildren(el("span", { class: "giro" }), "A IA local leva até 1 min…"), 6000);
    try {
      const r = await postar("/api/copiloto", { campo, ...contexto() });
      lista.replaceChildren(
        ...r.sugestoes.map((s) => el("button", { class: "sugestao", type: "button", role: "option", text: s,
          onclick: () => { aplicar(s); lista.classList.add("oculto"); } })),
        el("div", { class: "pequeno motor", text: `Fonte: ${MOTORES[r.motor] || r.motor}` }));
      lista.classList.remove("oculto");
    } catch (e) {
      avisar(e.message, "erro");
    } finally {
      clearTimeout(lento);
      botao.disabled = false;
      botao.textContent = rotulo;
    }
  });
  caixa.append(botao, lista);
  return caixa;
}

/* campo com rótulo + botão da IA ao lado */
export function campoComIA(rotulo, entrada, campo, contexto, dica = "") {
  return el("div", { class: "campo-ia" },
    el("div", { class: "campo-ia-topo" }, el("span", { text: rotulo }),
      botaoIA(campo, () => ({ ...contexto(), atual: entrada.value }), (s) => {
        entrada.value = s;
        entrada.dispatchEvent(new Event("input", { bubbles: true }));
      })),
    entrada,
    dica ? el("span", { class: "dica", text: dica }) : null);
}
