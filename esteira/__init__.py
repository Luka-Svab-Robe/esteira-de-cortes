"""Esteira de produção automática para canais dark do YouTube.

Camadas:
  - config / log / util / ffmpeg / navegador / templates  -> infraestrutura
  - canal / historico / projeto / metadados                -> identidade e estado
  - llm / pesquisa / midia                                  -> serviços externos
  - modulos/m1..m6                                          -> motor de produção
  - orquestrador / automacao                                -> execução e agendamento
"""

__version__ = "1.0.0"
