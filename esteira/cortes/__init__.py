"""Esteira de cortes: transforma lives (YouTube/Twitch) de fontes autorizadas em Shorts prontos.

fontes.py       -> cadastro de canais autorizados (próprios ou com permissão) e crédito
ingestao.py     -> "olheiro": yt-dlp (vídeo, chat da live, legendas, lives novas)
analise.py      -> "analista": sinais (chat, mais repetidos, áudio, fala) + escolha dos momentos
edicao.py       -> "editor": corte, enquadramento vertical, legenda, gancho, crédito, música
orquestrador.py -> encadeia as etapas, modo agente, monitoramento automático
"""
