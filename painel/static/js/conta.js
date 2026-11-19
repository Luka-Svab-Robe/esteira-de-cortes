/* Aba Conta: perfil (e o que fica visível), senha, conexões (chaves de IA e endpoints), canais e usuários. */
import { $, api, avisar, comCarregando, confirmar, el, gravar, postar } from "./base.js";
import { abrirYoutube } from "./canal.js";
import { estado, guardarPreferencia } from "./estado.js";
import { seletorModelo } from "./lives.js";
import { esquecerConexoes } from "./servicos.js";

const ROTULOS_PERFIL = { nome_publico: "Nome público", bio: "Sobre você", cidade: "Cidade", site: "Site", instagram: "Instagram", telefone: "Telefone" };
let recarregar = () => {};
let aoAtualizarSessao = () => {};
export function ligarConta({ aoMudar, aoMudarSessao }) { recarregar = aoMudar; aoAtualizarSessao = aoMudarSessao; }

export async function renderConta() {
  const raiz = $("#conta");
  raiz.replaceChildren(el("div", { class: "esqueleto", style: "height:220px" }));
  let conta, conexoes;
  try {
    [conta, conexoes] = await Promise.all([api("/api/conta"), api("/api/conexoes")]);
  } catch (e) {
    raiz.replaceChildren(el("div", { class: "aviso erro", text: e.message }));
    return;
  }
  const aberto = estado.sessao.modo === "aberto";
  raiz.replaceChildren(...[  // seções que não se aplicam ficam null e saem (senão o navegador escreve "null" na tela)
    secao("Seu perfil", "Escolha o que aparece para a equipe no site.", formPerfil(conta)),
    secao("Conexões", "Chaves das IAs e endpoints que a SUA esteira usa. Ficam cifradas; ninguém mais vê.", formConexoes(conexoes)),
    secao("Seus canais", "Conecte o YouTube, escolha o modelo visual e os padrões dos cortes.", canais()),
    aberto
      ? secao("Entrar com senha", "Hoje o painel abre direto neste computador, sem senha. Se outras pessoas usam este PC ou se você quer ter uma equipe, proteja com e-mail e senha.", formProteger())
      : secao("Senha e acesso", "", formSenha(), conta.papel === "admin" ? formSemSenha() : null),
    conta.papel === "admin" && !aberto ? secao("Equipe", "Crie contas para quem trabalha com você. Cada um vê só os próprios canais.", usuarios()) : null,
  ].filter(Boolean));
}

/* ------------------------------------------------------------ modo de acesso (com ou sem senha) */
function formProteger() {
  const email = el("input", { type: "email", autocomplete: "username" });
  const senha = el("input", { type: "password", autocomplete: "new-password", minlength: "8" });
  const repetir = el("input", { type: "password", autocomplete: "new-password" });
  const botao = el("button", { class: "botao cta", type: "button", text: "🔒 Proteger com senha" });
  botao.addEventListener("click", () => comCarregando(botao, "Protegendo…", async () => {
    if (senha.value !== repetir.value) return avisar("As senhas não são iguais.", "erro");
    try {
      await postar("/api/conta/proteger", { email: email.value, senha: senha.value });
      avisar("Pronto! Daqui em diante o painel pede e-mail e senha.", "ok");
      setTimeout(() => location.reload(), 900);
    } catch (e) { avisar(e.message, "erro"); }
  }));
  return el("div", {}, el("div", { class: "grade-campos" },
    el("label", { class: "campo" }, "E-mail", email), el("label", { class: "campo" }, "Senha (8+ letras)", senha),
    el("label", { class: "campo" }, "Repita a senha", repetir)), el("div", { class: "acoes" }, botao));
}

function formSemSenha() {
  const senha = el("input", { type: "password", autocomplete: "current-password" });
  const botao = el("button", { class: "botao perigo", type: "button", text: "🔓 Usar sem senha neste computador" });
  botao.addEventListener("click", async () => {
    if (!(await confirmar("Deixar de pedir senha?", { ok: "Usar sem senha", perigo: true,
      detalhe: "Quem usar este computador abre o painel direto, na sua conta. Pela rede continua fechado." }))) return;
    comCarregando(botao, "Mudando…", async () => {
      try {
        await postar("/api/conta/sem-senha", { senha: senha.value });
        avisar("Pronto! O painel não pede mais senha neste computador.", "ok");
        setTimeout(() => location.reload(), 900);
      } catch (e) { avisar(e.message, "erro"); }
    });
  });
  return el("details", { class: "novo-canal" }, el("summary", { text: "Usar sem senha neste computador" }),
    el("p", { class: "pequeno", text: "Só se apenas você usa este PC. As contas da equipe precisam estar desativadas. Confirme com a sua senha atual." }),
    el("div", { class: "grade-campos" }, el("label", { class: "campo" }, "Senha atual", senha)),
    el("div", { class: "acoes" }, botao));
}

const secao = (titulo, texto, ...conteudo) => el("section", { class: "card secao-conta" },
  el("h2", { class: "titulo-gta", text: titulo }), texto ? el("p", { class: "pequeno", text: texto }) : null, ...conteudo);

/* ------------------------------------------------------------ perfil e visibilidade */
function formPerfil(conta) {
  const nome = el("input", { type: "text", value: conta.nome, maxlength: "120" });
  const campos = {};
  const visivel = {};
  const chave = (campo, valorPadrao) => {
    const c = el("input", { type: "checkbox", checked: conta.visivel[campo] ?? valorPadrao, "aria-label": `Mostrar ${campo}` });
    visivel[campo] = c;
    return el("label", { class: "marcar visibilidade" }, c, "visível");
  };
  const previa = el("div", { class: "cartao-publico" });
  const atualizarPrevia = () => {
    const nomeExibido = (visivel.nome_publico.checked && campos.nome_publico.value.trim()) || (visivel.nome.checked ? nome.value : "Membro da equipe");
    previa.replaceChildren(el("div", { class: "avatar-letra", text: (nomeExibido || "?").slice(0, 1).toUpperCase() }),
      el("div", {}, el("strong", { text: nomeExibido }),
        ...["bio", "cidade", "site", "instagram", "telefone"].filter((c) => visivel[c].checked && campos[c].value.trim())
          .map((c) => el("div", { class: "pequeno", text: campos[c].value.trim() })),
        visivel.email.checked && estado.sessao.modo !== "aberto" ? el("div", { class: "pequeno", text: conta.email }) : null));
  };
  const linhas = [
    el("div", { class: "linha-perfil" }, el("label", { class: "campo" }, "Nome", nome), chave("nome", true)),
    el("div", { class: "linha-perfil" }, el("label", { class: "campo" }, "E-mail", el("input", estado.sessao.modo === "aberto"
      ? { type: "text", value: "", placeholder: "sem e-mail (uso sem senha)", disabled: true }
      : { type: "email", value: conta.email, disabled: true })), chave("email", false)),
    ...conta.campos.map((campo) => {
      const entrada = campo === "bio" ? el("textarea", { rows: 2, maxlength: "300", value: conta.perfil[campo] || "" })
        : el("input", { type: "text", maxlength: "300", value: conta.perfil[campo] || "" });
      campos[campo] = entrada;
      return el("div", { class: "linha-perfil" }, el("label", { class: "campo" }, ROTULOS_PERFIL[campo] || campo, entrada), chave(campo, false));
    }),
  ];
  const salvar = el("button", { class: "botao cta", type: "button", text: "Salvar perfil" });
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    try {
      const r = await gravar("/api/conta", { nome: nome.value,
        perfil: Object.fromEntries(Object.entries(campos).map(([k, v]) => [k, v.value])),
        visivel: Object.fromEntries(Object.entries(visivel).map(([k, v]) => [k, v.checked])) });
      aoAtualizarSessao(r);
      avisar("Perfil salvo.", "ok");
    } catch (e) { avisar(e.message, "erro"); }
  }));
  const formulario = el("div", { class: "perfil-grade" }, el("div", {}, ...linhas, el("div", { class: "acoes" }, salvar)),
    el("div", {}, el("div", { class: "pequeno", text: "Como aparece no site" }), previa));
  formulario.addEventListener("input", atualizarPrevia);
  atualizarPrevia();
  return formulario;
}

/* ------------------------------------------------------------ senha */
function formSenha() {
  const atual = el("input", { type: "password", autocomplete: "current-password" });
  const nova = el("input", { type: "password", autocomplete: "new-password", minlength: "8" });
  const repetir = el("input", { type: "password", autocomplete: "new-password" });
  const botao = el("button", { class: "botao", type: "button", text: "Trocar senha" });
  botao.addEventListener("click", () => comCarregando(botao, "Trocando…", async () => {
    if (nova.value !== repetir.value) return avisar("As senhas novas não são iguais.", "erro");
    try {
      await postar("/api/conta/senha", { atual: atual.value, nova: nova.value });
      atual.value = nova.value = repetir.value = "";
      avisar("Senha trocada. As outras sessões foram encerradas.", "ok");
    } catch (e) { avisar(e.message, "erro"); }
  }));
  return el("div", {}, el("div", { class: "grade-campos" },
    el("label", { class: "campo" }, "Senha atual", atual), el("label", { class: "campo" }, "Nova senha (8+ letras)", nova),
    el("label", { class: "campo" }, "Repita a nova", repetir)), el("div", { class: "acoes" }, botao));
}

/* ------------------------------------------------------------ conexões */
function formConexoes(lista) {
  const entradas = {};
  const campos = lista.map((c) => {
    const entrada = el("input", { type: c.segredo ? "password" : "text", autocomplete: "off", spellcheck: "false",
      placeholder: c.segredo ? (c.configurado ? c.valor : "cole a chave aqui") : (c.valor || ""),
      value: c.segredo ? "" : c.valor || "" });
    entradas[c.chave] = entrada;
    return el("label", { class: "campo" }, el("span", {}, c.rotulo, c.configurado ? el("span", { class: "etiqueta ok", text: "configurado" }) : null), entrada);
  });
  const salvar = el("button", { class: "botao cta", type: "button", text: "Salvar conexões" });
  salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
    const corpo = {};
    for (const c of lista) {
      const valor = entradas[c.chave].value.trim();
      if (c.segredo && !valor) continue;  // vazio = mantém a chave que já estava salva
      corpo[c.chave] = valor;
    }
    try { await gravar("/api/conexoes", corpo); esquecerConexoes(); avisar("Conexões salvas.", "ok"); renderConta(); }
    catch (e) { avisar(e.message, "erro"); }
  }));
  const resultado = el("p", { class: "pequeno", role: "status" });
  const testar = (tipo, rotulo) => {
    const b = el("button", { class: "botao pequeno turquesa", type: "button", text: rotulo });
    b.addEventListener("click", () => comCarregando(b, "Testando…", async () => {
      try { const r = await postar("/api/conexoes/testar", { tipo }); resultado.textContent = `${r.ok ? "✓" : "✗"} ${r.mensagem}`; }
      catch (e) { resultado.textContent = `✗ ${e.message}`; }
    }));
    return b;
  };
  return el("div", {}, el("div", { class: "grade-campos" }, campos),
    el("div", { class: "acoes" }, salvar, testar("anthropic", "Testar Claude"), testar("openai", "Testar endpoint"), testar("ollama", "Testar IA local")),
    resultado);
}

/* ------------------------------------------------------------ canais */
function canais() {
  const caixa = el("div", { class: "canais-conta" });
  for (const c of estado.dados ? estado.dados.canais : []) {
    const padrao = c.cortes_padrao || {};
    let modelo = c.modelo || "padrao";
    const qtd = el("input", { type: "number", min: "1", max: "10", value: padrao.max_cortes_por_live || 5 });
    const dmin = el("input", { type: "number", min: "10", max: "60", value: padrao.duracao_min || 20 });
    const dmax = el("input", { type: "number", min: "15", max: "180", value: padrao.duracao_max || 58 });
    const salvar = el("button", { class: "botao cta pequeno", type: "button", text: "Salvar padrões" });
    salvar.addEventListener("click", () => comCarregando(salvar, "Salvando…", async () => {
      try {
        await gravar(`/api/canais/${c.id}/padroes`, { modelo, max_cortes_por_live: Number(qtd.value), duracao_min: Number(dmin.value), duracao_max: Number(dmax.value) });
        avisar("Padrões do canal salvos.", "ok");
        recarregar();
      } catch (e) { avisar(e.message, "erro"); }
    }));
    const yt = c.youtube || {};
    caixa.append(el("div", { class: "canal-conta" },
      el("div", { class: "canal-conta-topo" },
        c.avatar ? el("img", { src: c.avatar, alt: "", class: "avatar" }) : el("div", { class: "avatar-letra", text: c.nome.slice(0, 1) }),
        el("div", {}, el("strong", { text: c.nome }), el("div", { class: "pequeno", text: yt.conectado ? `YouTube: ${yt.handle || yt.nome}` : "YouTube não conectado" })),
        el("button", { class: "botao pequeno", type: "button", text: yt.conectado ? "YouTube" : "Conectar YouTube", onclick: () => {
          estado.canalId = c.id; guardarPreferencia("canal", c.id); abrirYoutube(); } })),
      el("h4", { class: "subtitulo", text: "Modelo visual padrão" }),
      seletorModelo(modelo, (m) => (modelo = m), c.id),
      el("div", { class: "grade-campos" },
        el("label", { class: "campo" }, "Shorts por live (até)", qtd), el("label", { class: "campo" }, "Duração mínima (s)", dmin),
        el("label", { class: "campo" }, "Duração máxima (s)", dmax)),
      el("div", { class: "acoes" }, salvar)));
  }
  caixa.append(formNovoCanal());
  return caixa;
}

function formNovoCanal() {
  const nome = el("input", { type: "text", maxlength: "60", placeholder: "ex.: Cortes do Fulano" });
  const nicho = el("input", { type: "text", placeholder: "ex.: games, cortes de lives" });
  const paleta = el("input", { type: "text", placeholder: "ex.: roxo e amarelo" });
  const criar = el("button", { class: "botao turquesa", type: "button", text: "＋ Criar canal" });
  criar.addEventListener("click", () => comCarregando(criar, "Criando…", async () => {
    try {
      const r = await postar("/api/canais/novo", { nome: nome.value, nicho: nicho.value, paleta: paleta.value });
      estado.canalId = r.canal;
      guardarPreferencia("canal", r.canal);
      avisar("Canal criado. Agora conecte o YouTube dele.", "ok");
      recarregar(true);
    } catch (e) { avisar(e.message, "erro"); }
  }));
  return el("details", { class: "novo-canal" }, el("summary", { text: "Criar outro canal de cortes" }),
    el("div", { class: "grade-campos" }, el("label", { class: "campo" }, "Nome do canal", nome),
      el("label", { class: "campo" }, "Nicho", nicho), el("label", { class: "campo" }, "Cores", paleta)),
    el("div", { class: "acoes" }, criar));
}

/* ------------------------------------------------------------ equipe (administrador) */
function usuarios() {
  const lista = el("ul", { class: "lista usuarios" }, el("li", { class: "esqueleto", style: "height:40px" }));
  const carregar = async () => {
    try {
      const todos = await api("/api/usuarios");
      lista.replaceChildren(...todos.map((u) => {
        const ativo = el("input", { type: "checkbox", checked: u.ativo, disabled: u.id === estado.sessao.usuario.id, "aria-label": `Conta de ${u.nome} ativa` });
        ativo.addEventListener("change", async () => {
          try { await postar(`/api/usuarios/${u.id}/ativo`, { ativo: ativo.checked }); avisar(ativo.checked ? "Conta ativada." : "Conta desativada.", "ok"); }
          catch (e) { ativo.checked = !ativo.checked; avisar(e.message, "erro"); }
        });
        return el("li", {}, el("div", {}, el("strong", { text: u.nome }), el("div", { class: "pequeno", text: `${u.email} · ${u.papel === "admin" ? "administrador" : "membro"}` })),
          el("label", { class: "chave", title: "Conta ativa" }, ativo, el("span")));
      }));
    } catch (e) { lista.replaceChildren(el("li", { class: "aviso erro", text: e.message })); }
  };
  const nome = el("input", { type: "text" }), email = el("input", { type: "email" });
  const senha = el("input", { type: "password", autocomplete: "new-password", placeholder: "8+ letras (a pessoa troca depois)" });
  const papel = el("select", {}, el("option", { value: "usuario", text: "Membro" }), el("option", { value: "admin", text: "Administrador" }));
  const criar = el("button", { class: "botao turquesa", type: "button", text: "＋ Criar conta" });
  criar.addEventListener("click", () => comCarregando(criar, "Criando…", async () => {
    try {
      await postar("/api/usuarios", { nome: nome.value, email: email.value, senha: senha.value, papel: papel.value });
      nome.value = email.value = senha.value = "";
      avisar("Conta criada. Passe o e-mail e a senha para a pessoa.", "ok");
      carregar();
    } catch (e) { avisar(e.message, "erro"); }
  }));
  carregar();
  return el("div", {}, lista,
    el("details", { class: "novo-canal" }, el("summary", { text: "Criar conta para alguém da equipe" }),
      el("div", { class: "grade-campos" }, el("label", { class: "campo" }, "Nome", nome), el("label", { class: "campo" }, "E-mail", email),
        el("label", { class: "campo" }, "Senha inicial", senha), el("label", { class: "campo" }, "Papel", papel)),
      el("div", { class: "acoes" }, criar)));
}
