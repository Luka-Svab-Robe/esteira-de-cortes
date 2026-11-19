; Instalador da Esteira de Cortes para Windows (NSIS 3, Modern UI 2).
; Gere com: python instalador/construir_exe.py  (passa PACOTE, SAIDA, ICONE e VERSAO com -D)
; Instala só para o usuário atual (sem pedir administrador) em %LOCALAPPDATA%\EsteiraDeCortes.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
ManifestDPIAware true

!define APP "Esteira de Cortes"
!define ID_APP "EsteiraDeCortes"
!define CHAVE_DESINSTALAR "Software\Microsoft\Windows\CurrentVersion\Uninstall\${ID_APP}"

Name "${APP}"
OutFile "${SAIDA}"
InstallDir "$LOCALAPPDATA\${ID_APP}"
InstallDirRegKey HKCU "${CHAVE_DESINSTALAR}" "InstallLocation"
BrandingText "${APP} ${VERSAO}"
VIProductVersion "${VERSAO}.0"
VIAddVersionKey /LANG=1046 "ProductName" "${APP}"
VIAddVersionKey /LANG=1046 "FileDescription" "Instalador da ${APP}"
VIAddVersionKey /LANG=1046 "FileVersion" "${VERSAO}"
VIAddVersionKey /LANG=1046 "ProductVersion" "${VERSAO}"
VIAddVersionKey /LANG=1046 "LegalCopyright" "Esteira de Cortes"

!include "MUI2.nsh"
!define MUI_ICON "${ICONE}"
!define MUI_UNICON "${ICONE}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Bem-vindo à ${APP}"
!define MUI_WELCOMEPAGE_TEXT "Este assistente instala a ${APP}: a equipe que corta as lives do seu canal em Shorts, com legenda, música e agendamento.$\r$\n$\r$\nDurante a instalação o computador baixa o Python, o FFmpeg e as dependências (cerca de 1 GB). Pode levar de 5 a 15 minutos, conforme a internet.$\r$\n$\r$\nNão precisa de senha de administrador."
!define MUI_FINISHPAGE_TITLE "${APP} instalada"
!define MUI_FINISHPAGE_TEXT "O atalho ${APP} está no Menu Iniciar e na Área de Trabalho. Na primeira vez, crie sua conta de administrador na tela de Primeiro acesso."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Abrir a ${APP} agora"
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

Function AbrirApp
  IfFileExists "$INSTDIR\.venv\Scripts\pythonw.exe" 0 +2
    Exec '"$INSTDIR\.venv\Scripts\pythonw.exe" "$INSTDIR\esteira.py" app'
FunctionEnd

Section "Programa" SecPrograma
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${PACOTE}/*.*"

  DetailPrint "Preparando Python, FFmpeg e dependências (5 a 15 minutos)..."
  SetDetailsPrint both
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\instalador\preparar_windows.ps1" -Pasta "$INSTDIR"'
  Pop $0
  StrCmp $0 "0" preparo_ok
    MessageBox MB_ICONEXCLAMATION|MB_OK "Parte da preparação não terminou (código $0).$\r$\n$\r$\nConfira a internet e rode o instalador de novo: ele continua de onde parou. Detalhes em $INSTDIR\logs\instalacao.log"
  preparo_ok:

  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\esteira.py" app' "$INSTDIR\instalador\esteira.ico" 0
  CreateShortcut "$SMPROGRAMS\${APP}\Desinstalar ${APP}.lnk" "$INSTDIR\Desinstalar.exe"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\esteira.py" app' "$INSTDIR\instalador\esteira.ico" 0

  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "DisplayVersion" "${VERSAO}"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "Publisher" "${APP}"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "DisplayIcon" "$INSTDIR\instalador\esteira.ico"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${CHAVE_DESINSTALAR}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${CHAVE_DESINSTALAR}" "NoModify" 1
  WriteRegDWORD HKCU "${CHAVE_DESINSTALAR}" "NoRepair" 1
SectionEnd

; Desinstalar tira o programa e os atalhos, mas MANTÉM seus dados: contas (dados), canais, vídeos (projetos),
; credenciais do YouTube e o .env. Para apagar tudo, apague a pasta depois.
Section "Uninstall"
  ; fecha o painel (Python rodando desta pasta) para liberar os arquivos
  nsExec::Exec `powershell.exe -NoProfile -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\Desinstalar ${APP}.lnk"
  RMDir "$SMPROGRAMS\${APP}"

  RMDir /r "$INSTDIR\.venv"
  RMDir /r "$INSTDIR\esteira"
  RMDir /r "$INSTDIR\painel"
  RMDir /r "$INSTDIR\templates"
  RMDir /r "$INSTDIR\docs"
  RMDir /r "$INSTDIR\assets"
  RMDir /r "$INSTDIR\ferramentas"
  RMDir /r "$INSTDIR\instalador"
  RMDir /r "$INSTDIR\modelos"
  RMDir /r "$INSTDIR\.claude"
  Delete "$INSTDIR\esteira.py"
  Delete "$INSTDIR\instalar.py"
  Delete "$INSTDIR\instalar.bat"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\AGENTS.md"
  Delete "$INSTDIR\CLAUDE.md"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\Desinstalar.exe"
  DeleteRegKey HKCU "${CHAVE_DESINSTALAR}"
  RMDir "$INSTDIR"  ; só some se estiver vazia (seus dados continuam lá)
  IfFileExists "$INSTDIR\*.*" 0 +2
    MessageBox MB_ICONINFORMATION|MB_OK "O programa foi removido. Suas contas, canais e vídeos ficaram em:$\r$\n$INSTDIR$\r$\n$\r$\nApague essa pasta se não quiser mais nada."
SectionEnd
