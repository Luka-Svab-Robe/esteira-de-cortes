"""Motor de produção: seis módulos sequenciais baseados em artefatos em disco.

m1_roteiro     -> pesquisa, anti-duplicidade e roteiro estruturado
m2_narracao    -> voz neural local + auditoria de pronúncia (Whisper)
m3_montagem    -> cenas, motion graphics, trilha, ducking e loudness
m4_shorts      -> cortes verticais 9:16 com legenda palavra por palavra
m4_thumbnails  -> capas 1280x720 a partir dos quadros de maior contraste
m6_publicacao  -> envio agendado via YouTube Data API v3
(o módulo 5 — painel de revisão — fica em painel/servidor.py)
"""
