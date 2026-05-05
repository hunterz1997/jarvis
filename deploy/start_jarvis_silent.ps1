# ════════════════════════════════════════════════════════════════
# JARVIS — Silent boot launcher + crash watchdog
# ════════════════════════════════════════════════════════════════
# Starts the WA bridge AND the Jarvis API in the background.
# Then stays alive as a watchdog, restarting either service if
# it dies (e.g. crash, OOM, manual kill).
#
# No console window — all output goes to log files.
# The Jarvis UI only opens when the user launches launcher.pyw.
# ════════════════════════════════════════════════════════════════

$ErrorActionPreference = 'SilentlyContinue'

$JarvisDir = 'C:\Claude\Jarvis'
$BridgeDir = Join-Path $JarvisDir 'whatsapp_bridge'
$Python    = Join-Path $JarvisDir '.venv\Scripts\python.exe'
$MainPy    = Join-Path $JarvisDir 'main.py'
$LogDir    = Join-Path $JarvisDir 'logs'
$AutoLog   = Join-Path $LogDir 'autostart.log'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Log([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $AutoLog -Value "$ts  $msg"
}

function Test-PortInUse([int]$Port) {
    return [bool] (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-Bridge {
    if (Test-PortInUse 3001) { return }
    $stdout = Join-Path $JarvisDir 'wa_bridge.log'
    $stderr = Join-Path $JarvisDir 'wa_bridge.log.err'
    Start-Process -FilePath 'node.exe' `
                  -ArgumentList 'server.js' `
                  -WorkingDirectory $BridgeDir `
                  -WindowStyle Hidden `
                  -RedirectStandardOutput $stdout `
                  -RedirectStandardError  $stderr
    Log "bridge started (port 3001)"
}

function Start-JarvisApi {
    if (Test-PortInUse 8000) { return }
    if (-not (Test-Path $Python)) {
        Log "ERROR: python.exe not found at $Python"
        return
    }
    $apiOut = Join-Path $LogDir 'jarvis.log'
    $apiErr = Join-Path $LogDir 'jarvis.log.err'
    Start-Process -FilePath $Python `
                  -ArgumentList "`"$MainPy`"" `
                  -WorkingDirectory $JarvisDir `
                  -WindowStyle Hidden `
                  -RedirectStandardOutput $apiOut `
                  -RedirectStandardError  $apiErr
    Log "jarvis API started (port 8000)"
}

# ── Initial startup ───────────────────────────────────────────────────────────
Start-Bridge
Start-Sleep -Seconds 4
Start-JarvisApi

# ── Watchdog loop — check every 60 s, restart anything that died ──────────────
while ($true) {
    Start-Sleep -Seconds 60
    Start-Bridge
    Start-JarvisApi
}
