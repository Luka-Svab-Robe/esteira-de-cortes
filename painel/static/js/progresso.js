/* Barra de progresso real (0–100 %) com rótulo amigável. Atualiza no lugar para a animação ficar suave. */
import { el } from "./base.js";

export function barraProgresso({ pct = 0, rotulo = "" } = {}, { erro = false } = {}) {
  const no = el("div", { class: `progresso ${erro ? "falhou" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" },
    el("div", { class: "progresso-topo" },
      el("span", { class: "rotulo" }, erro ? null : el("span", { class: "pulso" }), el("span", { class: "texto" })),
      el("span", { class: "pct" })),
    el("div", { class: "trilho" }, el("div", { class: "preenchido" })));
  no.dataset.pct = "0";
  atualizarBarra(no, { pct, rotulo });
  return no;
}

export function atualizarBarra(no, { pct = 0, rotulo = "" } = {}) {
  const alvo = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  no.setAttribute("aria-valuenow", String(alvo));
  no.setAttribute("aria-valuetext", `${alvo}% — ${rotulo}`);
  no.querySelector(".texto").textContent = rotulo;
  no.querySelector(".preenchido").style.width = `${alvo}%`;
  no.classList.toggle("completo", alvo >= 100);
  contar(no.querySelector(".pct"), Number(no.dataset.pct) || 0, alvo);
  no.dataset.pct = String(alvo);
}

/* o número sobe contando até o valor novo */
function contar(alvoNo, de, para) {
  if (de === para) { alvoNo.textContent = `${para}%`; return; }
  const inicio = performance.now(), duracao = 600;
  const passo = (agora) => {
    const t = Math.min(1, (agora - inicio) / duracao);
    alvoNo.textContent = `${Math.round(de + (para - de) * (1 - (1 - t) ** 3))}%`;
    if (t < 1) requestAnimationFrame(passo);
  };
  requestAnimationFrame(passo);
}
