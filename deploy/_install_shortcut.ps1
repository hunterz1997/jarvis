# Helper script: creates the Windows Startup shortcut for Jarvis silent launcher.
# Called by setup_autostart.bat.

param(
    [Parameter(Mandatory=$true)] [string]$Ps1Path,
    [Parameter(Mandatory=$true)] [string]$ShortcutPath,
    [Parameter(Mandatory=$true)] [string]$WorkingDir
)

$ErrorActionPreference = 'Stop'

try {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($ShortcutPath)
    $sc.TargetPath       = 'powershell.exe'
    $sc.Arguments        = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Ps1Path`""
    $sc.WorkingDirectory = $WorkingDir
    $sc.WindowStyle      = 7    # 7 = Minimized (hidden)
    $sc.Description      = 'Starts Jarvis WhatsApp bridge + API silently at login'
    $sc.Save()
    if (Test-Path $ShortcutPath) {
        Write-Host "OK: shortcut created at $ShortcutPath"
        exit 0
    } else {
        Write-Host "FAIL: shortcut not present after Save()"
        exit 2
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
