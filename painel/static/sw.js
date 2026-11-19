/* Service worker do app Esteira de Cortes.
   - Abre rápido: guarda a "casca" do site (estilos, código, fontes, ícones) e usa a versão nova sempre que o
     painel responde (rede primeiro, cópia guardada só se o painel estiver desligado).
   - Nunca guarda dados das contas (/api), vídeos (/arquivos), músicas (/trilhas) nem fotos do canal.
   - Se o painel estiver desligado, mostra um aviso amigável no lugar da página de erro do navegador. */
const VERSAO = "cortes-v2";
const CASCA = [
  "/static/estilo.css", "/static/js/app.js", "/static/js/base.js", "/static/js/estado.js", "/static/js/entrada.js",
  "/static/js/lives.js", "/static/js/fontes.js", "/static/js/projetos.js", "/static/js/momentos.js", "/static/js/legenda.js",
  "/static/js/textos.js", "/static/js/copiloto.js", "/static/js/progresso.js", "/static/js/canal.js",
  "/static/js/servicos.js", "/static/js/conta.js", "/static/js/agendados.js", "/static/icones/app-192.png", "/static/icones/app-512.png",
];
const PAGINA_DESLIGADO = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Esteira de Cortes</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 system-ui,sans-serif;color:#fdf2ff;
background:radial-gradient(120% 70% at 50% -10%,#3a0b4d 0%,#150728 45%,#0b0418 80%)}main{max-width:420px;padding:24px;text-align:center}
h1{margin:0 0 8px;font-size:26px;background:linear-gradient(100deg,#ff2d95,#ff8a3d 55%,#ffd23f);-webkit-background-clip:text;
background-clip:text;color:transparent}p{color:#c8b3df}button{margin-top:12px;padding:12px 18px;border:0;border-radius:12px;
font-weight:700;cursor:pointer;background:linear-gradient(100deg,#ff2d95,#ff8a3d 55%,#ffd23f);color:#1a0410}</style></head>
<body><main><h1>O painel está desligado</h1><p>Abra pelo atalho <strong>Esteira de Cortes</strong> no menu do computador:
ele liga o painel e volta para cá sozinho.</p><button onclick="location.reload()">Tentar de novo</button></main></body></html>`;

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCA)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(caches.keys()
    .then((nomes) => Promise.all(nomes.filter((n) => n !== VERSAO).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

const guardavel = (url) => url.pathname.startsWith("/static/") || url.pathname.startsWith("/fontes/");

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    ev.respondWith(fetch(req).catch(() => new Response(PAGINA_DESLIGADO, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
    return;
  }
  if (!guardavel(url)) return;  // /api, /arquivos, /trilhas, /canal: sempre direto ao painel, nada guardado
  ev.respondWith(fetch(req).then((resp) => {
    if (resp.ok) { const copia = resp.clone(); caches.open(VERSAO).then((c) => c.put(req, copia)); }
    return resp;
  }).catch(() => caches.match(req).then((guardado) => guardado || Response.error())));
});
