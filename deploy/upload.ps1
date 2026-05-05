# ============================================================
# Jarvis — Upload to Oracle VPS
# Run this from your Windows laptop after getting VPS IP + SSH key.
# Usage: .\deploy\upload.ps1 -VpsIp "X.X.X.X" -KeyPath "C:\path\to\key.pem"
# ============================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$VpsIp,

    [Parameter(Mandatory=$true)]
    [string]$KeyPath
)

$JARVIS_DIR = "C:\Claude\Jarvis"
$REMOTE = "ubuntu@${VpsIp}:/opt/jarvis"

Write-Host ""
Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   Jarvis — Uploading to VPS          ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""
Write-Host "  VPS IP:  $VpsIp"
Write-Host "  Key:     $KeyPath"
Write-Host ""

# ── Create /opt/jarvis on VPS ──────────────────────────────────
Write-Host "[1/4] Creating /opt/jarvis on VPS..."
ssh -i $KeyPath -o StrictHostKeyChecking=no "ubuntu@$VpsIp" "sudo mkdir -p /opt/jarvis && sudo chown ubuntu:ubuntu /opt/jarvis"

# ── Upload Jarvis code (exclude venv, node_modules, __pycache__) ──
Write-Host "[2/4] Uploading Jarvis code..."
$exclude = @(
    "--exclude=.venv",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=whatsapp_bridge/node_modules",
    "--exclude=*.log",
    "--exclude=logsstartup.log",
    "--exclude=startup_out2.log",
    "--exclude=startup_err2.log"
)
$rsync_args = @("-avz", "--progress") + $exclude + @(
    "-e", "ssh -i `"$KeyPath`" -o StrictHostKeyChecking=no",
    "$JARVIS_DIR/",
    $REMOTE
)
& rsync $rsync_args

if ($LASTEXITCODE -ne 0) {
    # Fallback to scp if rsync not available
    Write-Host "  rsync not found, using scp..."
    # Zip first
    $zipPath = "$env:TEMP\jarvis_deploy.zip"
    Compress-Archive -Path "$JARVIS_DIR\*" -DestinationPath $zipPath -Force `
        -CompressionLevel Fastest
    scp -i $KeyPath -o StrictHostKeyChecking=no $zipPath "ubuntu@${VpsIp}:/opt/jarvis/jarvis.zip"
    ssh -i $KeyPath "ubuntu@$VpsIp" "cd /opt/jarvis && unzip -q jarvis.zip && rm jarvis.zip"
    Remove-Item $zipPath
}

# ── Upload credentials (memory/ folder) ───────────────────────
Write-Host "[3/4] Uploading credentials (google, microsoft tokens)..."
$creds = @(
    "google_credentials.json",
    "google_client_secret.json",
    "microsoft_credentials.json"
)
foreach ($f in $creds) {
    $src = "$JARVIS_DIR\memory\$f"
    if (Test-Path $src) {
        scp -i $KeyPath -o StrictHostKeyChecking=no $src "ubuntu@${VpsIp}:/opt/jarvis/memory/$f"
        Write-Host "  ✅ Uploaded: $f"
    } else {
        Write-Host "  ⚠️  Not found (skip): $f"
    }
}

# ── Upload WhatsApp session (optional — avoids re-scan) ────────
$waSession = "$JARVIS_DIR\memory\whatsapp_session"
if (Test-Path $waSession) {
    Write-Host "[3b] Uploading WhatsApp session (avoids re-scan)..."
    $rsync_wa = @("-avz", "-e", "ssh -i `"$KeyPath`" -o StrictHostKeyChecking=no",
        "$waSession/", "ubuntu@${VpsIp}:/opt/jarvis/memory/whatsapp_session/")
    & rsync $rsync_wa 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ WhatsApp session uploaded"
    } else {
        scp -i $KeyPath -r $waSession "ubuntu@${VpsIp}:/opt/jarvis/memory/" 2>$null
    }
}

# ── Run setup script on VPS ────────────────────────────────────
Write-Host ""
Write-Host "[4/4] Running setup script on VPS..."
Write-Host "  (This takes 3-5 minutes — installing Python deps + Node deps)"
Write-Host ""
ssh -i $KeyPath -o StrictHostKeyChecking=no "ubuntu@$VpsIp" `
    "chmod +x /opt/jarvis/deploy/setup.sh && bash /opt/jarvis/deploy/setup.sh"

Write-Host ""
Write-Host "══════════════════════════════════════════"
Write-Host "  Upload complete!"
Write-Host ""
Write-Host "  Open in browser:"
Write-Host "  http://${VpsIp}:8000/wa/qr              ← scan WhatsApp QR"
Write-Host "  http://${VpsIp}:8000/auth/microsoft/device ← connect OneDrive"
Write-Host "  http://${VpsIp}:8000/health              ← health check"
Write-Host "══════════════════════════════════════════"
