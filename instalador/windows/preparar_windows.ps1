# Preparo da Esteira de Cortes no Windows (roda dentro do instalador .exe).
# 1) Python 3.12 (instala so para este usuario, sem pedir administrador, se nao houver um 3.11+)
# 2) FFmpeg e Deno portateis em ferramentas\ (a esteira acha sozinha; nao mexe no PATH do sistema)
# 3) instalar.py: ambiente .venv, dependencias, navegador interno e fontes
# Mensagens sem acento de proposito: a janela do instalador mostra a saida do PowerShell em ANSI.
param([Parameter(Mandatory = $true)][string]$Pasta)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # deixa o Invoke-WebRequest muito mais rapido
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$FFmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
$DenoUrl = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

$Logs = Join-Path $Pasta "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Passo([string]$texto) { Write-Output "==> $texto" }

function Baixar([string]$url, [string]$destino) {
    for ($tentativa = 1; $tentativa -le 3; $tentativa++) {
        try { Invoke-WebRequest -Uri $url -OutFile $destino -UseBasicParsing; return }
        catch { if ($tentativa -eq 3) { throw }; Start-Sleep -Seconds (5 * $tentativa) }
    }
}

function Achar-Python {
    foreach ($versao in "3.12", "3.11", "3.13") {
        try {
            $exe = & py "-$versao" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch { }
    }
    foreach ($pasta in "Python312", "Python311", "Python313") {
        $local = Join-Path $env:LOCALAPPDATA "Programs\Python\$pasta\python.exe"
        if (Test-Path $local) { return $local }
    }
    return $null
}

try {
    # ---------------------------------------------------------------- 1. Python
    $python = Achar-Python
    if (-not $python) {
        Passo "Baixando o Python 3.12 (python.org)..."
        $instalador = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
        Baixar $PythonUrl $instalador
        Passo "Instalando o Python so para este usuario..."
        $p = Start-Process -FilePath $instalador -Wait -PassThru -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "InstallLauncherAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0")
        $python = Achar-Python
        if (-not $python) { Write-Output "ERRO: o Python nao foi instalado (codigo $($p.ExitCode))."; exit 2 }
    }
    Passo "Python: $python"

    # ---------------------------------------------------------------- 2. FFmpeg portatil
    $ffmpegBin = Join-Path $Pasta "ferramentas\ffmpeg\bin"
    if (-not (Test-Path (Join-Path $ffmpegBin "ffmpeg.exe"))) {
        Passo "Baixando o FFmpeg (edicao de video)..."
        $zip = Join-Path $env:TEMP "esteira-ffmpeg.zip"
        $tmp = Join-Path $env:TEMP "esteira-ffmpeg"
        Baixar $FFmpegUrl $zip
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $origem = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if (-not $origem) { Write-Output "ERRO: o pacote do FFmpeg veio sem o ffmpeg.exe."; exit 3 }
        New-Item -ItemType Directory -Force $ffmpegBin | Out-Null
        Copy-Item -Force (Join-Path $origem.DirectoryName "*.exe") $ffmpegBin
        Remove-Item -Recurse -Force $tmp, $zip -ErrorAction SilentlyContinue
    }
    Passo "FFmpeg pronto."

    # ---------------------------------------------------------------- 3. Deno portatil (o YouTube exige para baixar lives)
    $denoPasta = Join-Path $Pasta "ferramentas\deno"
    if (-not (Test-Path (Join-Path $denoPasta "deno.exe"))) {
        Passo "Baixando o Deno (necessario para baixar lives do YouTube)..."
        $zip = Join-Path $env:TEMP "esteira-deno.zip"
        Baixar $DenoUrl $zip
        New-Item -ItemType Directory -Force $denoPasta | Out-Null
        Expand-Archive -Path $zip -DestinationPath $denoPasta -Force
        Remove-Item -Force $zip -ErrorAction SilentlyContinue
    }
    Passo "Deno pronto."

    # ---------------------------------------------------------------- 4. Dependencias da esteira
    Passo "Instalando as dependencias da esteira (5 a 15 minutos, depende da internet)..."
    $env:PATH = "$ffmpegBin;$env:PATH"   # o instalar.py confere o FFmpeg pelo PATH
    $env:PYTHONIOENCODING = "utf-8"
    $saida = Join-Path $Logs "instalacao.log"
    $erros = Join-Path $Logs "instalacao-erros.log"
    $p = Start-Process -FilePath $python -WorkingDirectory $Pasta -Wait -PassThru -NoNewWindow `
        -ArgumentList @("`"$(Join-Path $Pasta 'instalar.py')`"", "--sem-modelos") `
        -RedirectStandardOutput $saida -RedirectStandardError $erros
    if ($p.ExitCode -ne 0) { Write-Output "ERRO: as dependencias nao terminaram (codigo $($p.ExitCode)). Veja $saida"; exit 4 }
    if (-not (Test-Path (Join-Path $Pasta ".venv\Scripts\pythonw.exe"))) { Write-Output "ERRO: o ambiente .venv nao foi criado. Veja $saida"; exit 5 }
    Passo "Tudo pronto! O modelo de transcricao e baixado no primeiro corte."
    exit 0
}
catch {
    Write-Output "ERRO: $($_.Exception.Message)"
    exit 1
}
