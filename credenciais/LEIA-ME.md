# Credenciais do YouTube (OAuth2)

A publicação usa a **YouTube Data API v3** oficial. Você faz isso uma vez só:

Use a **mesma conta Google que administra o(s) canal(is) do YouTube**.

1. Acesse <https://console.cloud.google.com/projectcreate> e crie um projeto (ex.: "Esteira Canal Dark").
2. Ative a API: <https://console.cloud.google.com/apis/library/youtube.googleapis.com> → **Ativar**.
3. Configure a tela de login: <https://console.cloud.google.com/auth/overview> → **Primeiros passos**
   (nos menus antigos: *APIs e serviços → Tela de consentimento OAuth*):
   nome do app, seu e-mail de suporte, público **Externo**, seu e-mail de contato → **Criar**.
4. Em **Público-alvo** (*Audience*), clique em **Publicar app** e confirme (status **Em produção**).
   Não precisa de verificação do Google para uso próprio. **Se ficar em "Teste", a conexão expira a
   cada 7 dias** e as publicações automáticas param.
5. Em **Clientes** (*Clients*) → **Criar cliente** → tipo **App para computador** → **Criar** →
   **Baixar JSON**.
6. Salve o arquivo aqui como `credenciais/client_secret.json` (ou ajuste `YOUTUBE_CLIENT_SECRETS` no `.env`).
   O agente de IA pode mover o arquivo da pasta Downloads para cá.
7. Conecte cada canal (abre o navegador para você escolher a conta/canal). Como o app não é
   verificado, o Google mostra "O Google não verificou este app": clique em **Avançado → Acessar
   (não seguro)**. É o seu próprio app, então é seguro continuar. Também dá para conectar pelo painel
   (botão **＋ Live → Conectar canal do YouTube**).

   ```bash
   python esteira.py youtube autenticar --canal <id_do_seu_canal>
   ```

   O token fica em `canais/<canal>/credenciais/token_youtube.json` (cada canal tem o seu).

## Avisos importantes

- **Projetos não verificados:** vídeos enviados pela API por projetos do Google Cloud que ainda
  não passaram pela auditoria do YouTube ficam **travados como privados**. Para publicar
  publicamente pelo agendamento, solicite a auditoria em
  <https://support.google.com/youtube/contact/yt_api_form>. Enquanto isso, a esteira agenda
  normalmente e você pode tornar público manualmente no YouTube Studio.
- **Cota diária:** a cota padrão é de 10.000 unidades por dia e cada envio de vídeo consome uma
  parte grande dela. Um vídeo + 3 Shorts por dia costuma caber; volumes maiores exigem
  pedir aumento de cota no Google Cloud.
- **Capas personalizadas** exigem que o canal esteja verificado por telefone no YouTube.
- Nunca compartilhe `client_secret.json` nem os tokens: eles dão acesso ao seu canal.
