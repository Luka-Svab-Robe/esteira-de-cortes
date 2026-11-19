/* Estado compartilhado do painel e avisos de "os dados mudaram" entre as telas. */

function lerPreferencia(chave, padrao) {
  try { return localStorage.getItem(chave) || padrao; } catch (_) { return padrao; }
}
export function guardarPreferencia(chave, valor) {
  try { localStorage.setItem(chave, valor); } catch (_) { /* navegador sem armazenamento: tudo bem */ }
}

export const estado = {
  sessao: null,          // {usuario, canais}
  dados: null,           // /api/painel
  etag: "",
  lives: new Map(),      // link do canal integrado -> {lista} | {carregando} | {erro} (aba Canais)
  canalId: lerPreferencia("canal", ""),
  aba: "painel",
  abertos: new Map(),    // projeto -> etapa aberta no pipeline
  rascunhos: new Map(),  // projeto -> {indice: {titulo, descricao, tags, selecionado}}
  dispensados: new Set(),
};

export function canal() {
  const canais = estado.dados ? estado.dados.canais : [];
  return canais.find((c) => c.id === estado.canalId) || canais[0] || null;
}

const ouvintes = new Set();
export function aoMudar(fn) { ouvintes.add(fn); }
export function notificar() { for (const fn of ouvintes) fn(); }

export const ESTADOS_TRABALHANDO = ["em_producao", "publicando", "aprovado", "aguardando_roteiro"];
export function ocupado() {
  const d = estado.dados;
  return Boolean(d && (d.envios.some((e) => e.estado === "rodando") ||
    d.projetos.some((p) => p.trabalhando || ESTADOS_TRABALHANDO.includes(p.estado) || p.publicando ||
      p.shorts.some((s) => s.refazendo)) ||
    d.canais.some((c) => c.youtube.em_andamento)));
}
