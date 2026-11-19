# Aba Canais: passo a passo

A aba **Canais** reúne os canais de onde vêm as lives que você corta (Twitch, Kick ou YouTube).
Dá para integrar quantos canais você quiser.

> O canal do YouTube conectado no topo da tela (o selo ao lado da sua foto) serve **só para postar** os Shorts.
> Ele não aparece mais sozinho como fonte de lives.

---

## 1. Abrir o app

1. Abra **Esteira de Cortes** pelo menu do computador.
2. No menu do topo, clique em **Canais** (entre *Painel* e *Agendados*).

## 2. Integrar um canal

1. Em **Integrar um canal**, cole o link do canal. Exemplos:
   - `twitch.tv/nome`
   - `kick.com/nome`
   - `youtube.com/@canal`

   Também vale o link de uma live desse canal: o app descobre o canal sozinho.
2. Escolha de quem é o canal:
   - **🤝 De outro criador**: o criador permite cortes. O crédito da live aparece no vídeo e na descrição.
   - **🎙 É um canal meu**: lives suas, sem crédito a terceiros.
3. Se for de outro criador, marque **"Confirmo que o criador permite cortes das lives"**.
   Se quiser, diga onde ele autorizou (regras da Twitch, Discord, e-mail…).
   **Corte só de quem permite**: cortar sem permissão pode render strike no seu canal.
4. (Opcional) Marque **"Vigiar este canal no Modo automático"** para cortar sozinho as lives novas dele.
5. Clique em **＋ Integrar canal**. Em alguns segundos ele aparece em **Canais integrados**, já com as lives.

Repita para cada canal que quiser. Não há limite.

## 3. Cortar uma live de um canal integrado

1. No cartão do canal, clique em **▾ Ver lives**.
2. Escolha a live e clique em **✂ Cortar esta live**.
3. Na janela **Como você quer cortar?**, escolha:
   - **⚡ Tudo automático**: a equipe faz tudo e deixa os Shorts prontos para você aprovar.
   - **🎛 Passo a passo**: a live é baixada e você escolhe os momentos.
4. Ajuste layout, modelo, quantidade e duração dos Shorts, se quiser, e clique em **Começar**.
5. Acompanhe a barra de progresso na aba **Painel**, em *Lives em produção*.

Se a live ainda estiver no ar, informe um trecho que já passou (ex.: `30:00` até `1:10:00`).
Para ver lives novas, clique em **↻ Procurar lives novas** no cartão do canal.

## 4. Vigiar canais sozinho (Modo automático)

1. Na aba **Canais**, ligue a chave **Vigiar** de cada canal que deve ser acompanhado.
2. Na aba **Painel**, em **Modo automático**, ligue **"Vigiar os canais marcados na aba Canais…"**.

O app procura lives novas de tempos em tempos e corta sozinho. Os Shorts esperam a sua aprovação,
a menos que você tenha ligado **"Agendar sem eu revisar"**.

## 5. Remover um canal

1. No cartão do canal, clique em **Remover** e confirme.
2. As lives dele somem da aba e o Modo automático para de vigiá-lo.
   **Os Shorts já feitos continuam.**

---

## Dúvidas comuns

| Situação | O que fazer |
|---|---|
| "Não consegui ver as lives" | Confira se o link é do canal certo e clique em **↻ Procurar lives novas**. |
| O canal não aparece depois de integrar | Confira o link (precisa ser da Twitch, da Kick ou do YouTube) e tente de novo. |
| Quero cortar uma live de um canal não integrado | Cole o link da live em **Cortar uma live**, na aba Painel. O app pergunta se o criador permite cortes e integra o canal. |
| O Modo automático não corta nada | Veja se algum canal está com **Vigiar** ligado e se o Modo automático está ligado no Painel. |

Pelo terminal (para quem prefere): `python esteira.py fontes listar|adicionar|remover --canal <canal> …`.
