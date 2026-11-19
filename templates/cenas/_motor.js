/* Motor de tempo determinístico das cenas.
   window.__setTime(t) congela TODAS as animações CSS no instante t (segundos)
   e chama os ganchos JS registrados com aoTempo(fn). Assim cada quadro é
   capturado exatamente igual, em qualquer máquina. */
(function () {
  const ganchos = [];
  window.aoTempo = function (fn) { ganchos.push(fn); };
  window.limitar = (x, a, b) => Math.min(b, Math.max(a, x));
  window.progresso = (t, inicio, duracao) => limitar((t - inicio) / duracao, 0, 1);
  window.suavizar = (x) => { x = limitar(x, 0, 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
  window.desacelerar = (x) => 1 - Math.pow(1 - limitar(x, 0, 1), 3);
  window.aleatorio = function (semente) {
    let s = (Math.abs(semente | 0) || 1) >>> 0;
    return function () { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  };
  window.misturar = function (a, b, t) {
    const h = (c) => { c = String(c).replace("#", ""); return [0, 2, 4].map((i) => parseInt(c.substr(i, 2), 16) || 0); };
    const x = h(a), y = h(b);
    return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
  };
  window.ajustarTexto = function (el, maximo, minimo) {
    let tamanho = maximo;
    el.style.fontSize = tamanho + "px";
    while (tamanho > minimo && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)) {
      tamanho -= 2;
      el.style.fontSize = tamanho + "px";
    }
    return tamanho;
  };
  /* "O FIM DO *TITANIC*" -> a palavra entre asteriscos recebe a classe .realce */
  window.realcar = function (el, texto) {
    el.textContent = "";
    String(texto || "").split(/(\*[^*]+\*)/).forEach(function (parte) {
      if (!parte) return;
      if (parte.length > 2 && parte[0] === "*" && parte[parte.length - 1] === "*") {
        const s = document.createElement("span");
        s.className = "realce";
        s.textContent = parte.slice(1, -1);
        el.appendChild(s);
      } else {
        el.appendChild(document.createTextNode(parte));
      }
    });
  };
  window.dividirPalavras = function (el, texto, atrasoInicial, passo, animacao) {
    el.textContent = "";
    String(texto || "").split(/\s+/).filter(Boolean).forEach(function (p, i) {
      const s = document.createElement("span");
      s.className = "palavra anim";
      s.style.animationName = animacao || "subir";
      s.style.animationDelay = (atrasoInicial + i * passo).toFixed(3) + "s";
      if (p.length > 2 && p[0] === "*" && p[p.length - 1] === "*") { s.classList.add("realce"); p = p.slice(1, -1); }
      s.textContent = p;
      el.appendChild(s);
      el.appendChild(document.createTextNode(" "));
    });
  };
  window.__setTime = function (t) {
    document.getAnimations().forEach(function (a) { try { a.pause(); a.currentTime = t * 1000; } catch (e) {} });
    for (const fn of ganchos) { try { fn(t); } catch (e) { console.error(e); } }
  };
  async function iniciar() {
    if (typeof window.prepararCena === "function") { try { await window.prepararCena(); } catch (e) { console.error(e); } }
    try { await document.fonts.ready; } catch (e) {}
    // mede o texto no estado FINAL das animações (deslocamentos de entrada inflariam a altura)
    window.__setTime(60);
    if (typeof window.ajustarCena === "function") { try { window.ajustarCena(); } catch (e) { console.error(e); } }
    await Promise.all(Array.from(document.images).map(function (img) {
      return img.complete ? null : new Promise(function (r) { img.onload = img.onerror = r; });
    }));
    window.__setTime(0);
    window.__pronto = true;
  }
  if (document.readyState === "complete") setTimeout(iniciar, 0); else window.addEventListener("load", iniciar);
})();
