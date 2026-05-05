#!/bin/bash
# ============================================================
# Jarvis VPS Setup Script — Oracle Free Tier (Ubuntu 22.04 ARM)
# Run once after uploading Jarvis to the server.
# Usage: bash /opt/jarvis/deploy/setup.sh
# ============================================================

set -e
JARVIS_DIR="/opt/jarvis"
USER="ubuntu"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     JARVIS — VPS Setup Script        ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. System packages ─────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    nodejs npm \
    chromium-browser \
    git curl wget unzip \
    netfilter-persistent iptables-persistent

echo "      Python: $(python3.11 --version)"
echo "      Node:   $(node --version)"
echo "      Chromium: $(chromium-browser --version 2>/dev/null || echo 'not found')"

# ── 2. Python venv + dependencies ─────────────────────────────
echo ""
echo "[2/7] Setting up Python virtual environment..."
cd "$JARVIS_DIR"
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet
echo "      Python dependencies installed."

# ── 3. Node.js dependencies ────────────────────────────────────
echo ""
echo "[3/7] Installing WhatsApp bridge dependencies..."
cd "$JARVIS_DIR/whatsapp_bridge"
npm install --quiet 2>/dev/null
echo "      Node dependencies installed."

# ── 4. Configure .env for VPS ─────────────────────────────────
echo ""
echo "[4/7] Configuring .env for VPS..."
cd "$JARVIS_DIR"
# Change HOST to listen on all interfaces
sed -i 's/^HOST=127\.0\.0\.1/HOST=0.0.0.0/' .env
echo "      HOST set to 0.0.0.0"

# ── 5. Install systemd services ────────────────────────────────
echo ""
echo "[5/7] Installing systemd services..."
sudo cp "$JARVIS_DIR/deploy/jarvis.service"    /etc/systemd/system/jarvis.service
sudo cp "$JARVIS_DIR/deploy/jarvis-wa.service" /etc/systemd/system/jarvis-wa.service
sudo systemctl daemon-reload
sudo systemctl enable jarvis jarvis-wa
echo "      Services installed and enabled."

# ── 6. Open firewall port 8000 ─────────────────────────────────
echo ""
echo "[6/7] Opening firewall port 8000..."
sudo iptables -C INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true
echo "      Port 8000 open."

# ── 7. Start services ──────────────────────────────────────────
echo ""
echo "[7/7] Starting Jarvis services..."
sudo systemctl start jarvis-wa
sleep 3
sudo systemctl start jarvis
sleep 5

# ── Status check ───────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "  Setup complete! Checking status..."
echo "══════════════════════════════════════════"

if sudo systemctl is-active --quiet jarvis; then
    echo "  ✅ Jarvis (FastAPI):      RUNNING"
else
    echo "  ❌ Jarvis (FastAPI):      FAILED — check: sudo journalctl -u jarvis -n 30"
fi

if sudo systemctl is-active --quiet jarvis-wa; then
    echo "  ✅ WhatsApp bridge:       RUNNING"
else
    echo "  ❌ WhatsApp bridge:       FAILED — check: sudo journalctl -u jarvis-wa -n 30"
fi

# Try health check
sleep 3
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✅ Health check:          PASSED"
    VPS_IP=$(curl -sf https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "  🌐 Jarvis is live at: http://$VPS_IP:8000"
    echo ""
    echo "  Next steps:"
    echo "  1. Visit http://$VPS_IP:8000/wa/qr — scan WhatsApp QR"
    echo "  2. Visit http://$VPS_IP:8000/auth/microsoft/device — connect OneDrive"
    echo "  3. Test: send 'Hey Jarvis, what time is it?' on WhatsApp"
else
    echo "  ⚠️  Health check: not responding yet (may need a few more seconds)"
    echo "     Try: curl http://localhost:8000/health"
fi
echo ""
