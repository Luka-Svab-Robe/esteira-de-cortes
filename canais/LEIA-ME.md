# Seus canais ficam aqui

Esta pasta vem vazia: cada pessoa cria os próprios canais.

- **Pelo painel:** abra o app, entre (no primeiro acesso você escolhe usar sem senha ou com e-mail e senha) e vá em
  **Agendados → Seus canais de publicação → ＋ Cadastrar um canal** (ali também conecta o YouTube e escolhe os
  horários e dias de publicação).
- **Pelo terminal:** `python esteira.py canal criar --nome "Meu Canal" --nicho "…" --tom "…" --paleta "…" --ritmo medio`
  (para um canal de cortes de lives, acrescente `--tipo cortes`).

Cada canal vira uma pasta `canais/<id>/` com o `identidade.yaml` (visual, voz, horários, hashtags), a fila de temas,
as trilhas e, depois de conectar o YouTube, as credenciais dele. Nada disso deve ir para o GitHub: o `.gitignore`
já deixa de fora credenciais, histórico, fontes e listas de publicação de cada canal.
