'use strict';

/**
 * Jarvis WhatsApp Bridge
 * Wraps whatsapp-web.js in a local REST API on port 3001.
 * Session is persisted in ../memory/whatsapp_session/ after first QR scan.
 */

const express = require('express');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const path = require('path');

const PORT = 3001;
const SESSION_DIR = path.join(__dirname, '..', 'memory', 'whatsapp_session');

// ── State ──────────────────────────────────────────────────────
let clientStatus = 'initializing'; // initializing | qr_ready | authenticated | ready | disconnected
let clientReady = false;
let qrData = null;

// ── WhatsApp client ────────────────────────────────────────────
// On Linux VPS set CHROMIUM_PATH=/usr/bin/chromium-browser (ARM-native build).
// On Windows leave unset — puppeteer uses its own bundled Chromium.
const puppeteerConfig = {
  headless: true,
  args: [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-accelerated-2d-canvas',
    '--no-first-run',
    '--disable-gpu',
  ],
};
if (process.env.CHROMIUM_PATH) {
  puppeteerConfig.executablePath = process.env.CHROMIUM_PATH;
}

const client = new Client({
  authStrategy: new LocalAuth({
    clientId: 'jarvis',
    dataPath: SESSION_DIR,
  }),
  puppeteer: puppeteerConfig,
});

client.on('qr', (qr) => {
  clientStatus = 'qr_ready';
  qrData = qr;
  console.log('\n══════════════════════════════════════');
  console.log('  JARVIS WhatsApp Bridge — Scan QR:');
  console.log('══════════════════════════════════════\n');
  qrcode.generate(qr, { small: true });
  console.log('\nOpen WhatsApp → Linked Devices → Link a Device\n');
});

client.on('authenticated', () => {
  clientStatus = 'authenticated';
  qrData = null;
  console.log('✓ WhatsApp authenticated — session saved.');
});

client.on('ready', () => {
  clientStatus = 'ready';
  clientReady = true;
  console.log('✓ WhatsApp bridge READY on http://localhost:' + PORT);
});

client.on('disconnected', (reason) => {
  clientStatus = 'disconnected';
  clientReady = false;
  console.warn('WhatsApp disconnected:', reason);
  // Attempt reconnect
  setTimeout(() => {
    console.log('Attempting reconnect…');
    client.initialize().catch(console.error);
  }, 5000);
});

client.on('auth_failure', (msg) => {
  clientStatus = 'disconnected';
  clientReady = false;
  console.error('WhatsApp auth failure:', msg);
});

// ── Jarvis session state ───────────────────────────────────────
// Tracks whether a "Hey Jarvis" session is active.
// Timer resets on ANY activity (user message OR Jarvis reply).
// After 5 minutes of complete silence the session closes automatically.

const processedIds = new Set();      // prevent double-processing same message
// Tracks bodies of messages Jarvis is about to send or has just sent.
// Added SYNCHRONOUSLY before await sendMessage() so message_create sees it
// even if the event fires before the promise resolves (race-condition fix).
const jarvisSentBodies = new Map();  // body -> pending count
let waSessionActive = false;
let waSessionTimer = null;
const WA_SESSION_TIMEOUT = 5 * 60 * 1000; // 5 minutes

function touchWaSession() {
  if (waSessionTimer) clearTimeout(waSessionTimer);
  waSessionTimer = setTimeout(() => {
    waSessionActive = false;
    console.log('[WA] Session closed — 5 min idle');
  }, WA_SESSION_TIMEOUT);
}

// ── Incoming message handler ───────────────────────────────────
// message_create fires for every message YOU send (including self-chat).
// message only fires for incoming messages from others, so self-chat is missed.
client.on('message_create', async (msg) => {
  const body = (msg.body || '').trim();
  const id   = msg.id?.id || '';

  if (!msg.fromMe) return;          // only self-messages trigger Jarvis

  // Skip messages Jarvis sent — checked by BODY before the await resolves (race-condition safe)
  if (jarvisSentBodies.has(body)) {
    const n = jarvisSentBodies.get(body) - 1;
    if (n <= 0) jarvisSentBodies.delete(body);
    else jarvisSentBodies.set(body, n);
    return;
  }

  if (processedIds.has(id)) return; // no double-fire
  processedIds.add(id);
  if (processedIds.size > 200) {    // keep Set bounded
    processedIds.delete(processedIds.values().next().value);
  }

  const hasJarvisTrigger = body.toLowerCase().includes('jarvis');

  // Start a new session on trigger word, or continue an active one
  if (!waSessionActive && !hasJarvisTrigger) return;
  if (hasJarvisTrigger && !waSessionActive) {
    waSessionActive = true;
    console.log('[WA] Jarvis session activated');
  }
  touchWaSession(); // reset idle clock on every user message

  console.log('[WA] Forwarding to Jarvis:', body.substring(0, 80));
  try {
    await fetch('http://127.0.0.1:8000/webhook/whatsapp', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ chat_id: msg.from, body, message_id: id }),
    });
  } catch (e) {
    console.error('[WA] Forward to Jarvis failed:', e.message);
  }
});

// ── Express app ────────────────────────────────────────────────
const app = express();
app.use(express.json());

// Only accept connections from localhost
app.use((req, res, next) => {
  const ip = req.ip || req.connection.remoteAddress || '';
  const isLocal = ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1';
  if (!isLocal) {
    return res.status(403).json({ error: 'Forbidden — local access only' });
  }
  next();
});

// ── Routes ─────────────────────────────────────────────────────

/**
 * GET /status — Health check
 */
app.get('/status', (req, res) => {
  res.json({
    status: clientStatus,
    ready: clientReady,
    qr_available: !!qrData,
  });
});

/**
 * GET /health — Deep WhatsApp Web connection check via client.getState().
 * Used by the Jarvis watchdog to detect silent stale sessions
 * (where /status reports "ready" but messages no longer flow).
 * Returns: { healthy: bool, state: "CONNECTED"|other|null, ready: bool }
 */
app.get('/health', async (req, res) => {
  try {
    if (!clientReady) {
      return res.json({ healthy: false, state: null, ready: false, reason: 'not_ready' });
    }
    // client.getState() returns "CONNECTED" when WA Web socket is healthy.
    // Other values = unhealthy (OPENING / CONFLICT / DEPRECATED_VERSION / PAIRING / etc.)
    const state = await Promise.race([
      client.getState(),
      new Promise((_, rej) => setTimeout(() => rej(new Error('getState timeout')), 4000)),
    ]);
    const healthy = state === 'CONNECTED';
    res.json({ healthy, state: state || null, ready: clientReady });
  } catch (e) {
    res.json({ healthy: false, state: null, ready: clientReady, error: String(e.message || e) });
  }
});

/**
 * GET /qr — Return QR data for display
 */
app.get('/qr', (req, res) => {
  if (!qrData) {
    return res.json({ success: false, error: 'No QR available', status: clientStatus });
  }
  res.json({ success: true, qr: qrData });
});

/**
 * GET /chats?limit=20 — List recent chats
 */
app.get('/chats', async (req, res) => {
  if (!clientReady) {
    return res.status(503).json({ error: 'WhatsApp not ready', status: clientStatus });
  }
  try {
    const limit = parseInt(req.query.limit) || 20;
    const chats = await client.getChats();
    const result = chats.slice(0, limit).map(chat => ({
      id: chat.id._serialized,
      name: chat.name,
      isGroup: chat.isGroup,
      unreadCount: chat.unreadCount,
      lastMessage: chat.lastMessage ? {
        body: chat.lastMessage.body?.slice(0, 100),
        timestamp: chat.lastMessage.timestamp,
        fromMe: chat.lastMessage.fromMe,
      } : null,
    }));
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

/**
 * GET /messages?contact=NAME&limit=20 — Get messages from a chat
 */
app.get('/messages', async (req, res) => {
  if (!clientReady) {
    return res.status(503).json({ error: 'WhatsApp not ready', status: clientStatus });
  }
  try {
    const { contact, limit = 20 } = req.query;
    if (!contact) return res.status(400).json({ error: 'contact parameter required' });

    const chats = await client.getChats();
    // Find by name (case-insensitive) or by number
    const chat = chats.find(c =>
      c.name.toLowerCase() === contact.toLowerCase() ||
      c.id._serialized.includes(contact.replace(/[^0-9]/g, ''))
    );

    if (!chat) {
      return res.status(404).json({ error: `Chat not found for: ${contact}` });
    }

    const messages = await chat.fetchMessages({ limit: parseInt(limit) });
    const result = messages.map(m => ({
      id: m.id._serialized,
      body: m.body,
      fromMe: m.fromMe,
      author: m.author || (m.fromMe ? 'Me' : chat.name),
      timestamp: m.timestamp,
      type: m.type,
    }));
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

/**
 * POST /send — Send a message
 * Body: { contact: string, message: string }
 */
app.post('/send', async (req, res) => {
  if (!clientReady) {
    return res.status(503).json({ success: false, error: 'WhatsApp not ready', status: clientStatus });
  }
  try {
    const { contact, message, allow_group, chat_id } = req.body;
    if (!contact || !message) {
      return res.status(400).json({ success: false, error: 'contact and message required' });
    }

    const chats = await client.getChats();

    // ─── If caller already resolved a chat_id, use it directly (no fuzzy match) ───
    if (chat_id) {
      const direct = chats.find(c => c.id._serialized === chat_id);
      if (!direct) {
        return res.status(404).json({ success: false, error: `chat_id not found: ${chat_id}` });
      }
      if (direct.isGroup && !allow_group) {
        return res.status(400).json({
          success: false, error: 'Refusing to send to a group chat without allow_group=true',
          chat: { id: direct.id._serialized, name: direct.name, isGroup: true },
        });
      }
      await direct.sendMessage(message);
      return res.json({ success: true, sent_to: direct.name, chat_id: direct.id._serialized, isGroup: direct.isGroup });
    }

    // ─── Resolve by name with STRICT matching ────────────────────────────────────
    const needle = String(contact).trim().toLowerCase();
    const cleanNumber = String(contact).replace(/[^0-9]/g, '');

    // Build candidate buckets, individuals first
    const sortByIndividualFirst = (a, b) => (a.isGroup ? 1 : 0) - (b.isGroup ? 1 : 0);

    const exact     = chats.filter(c => c.name && c.name.toLowerCase() === needle);
    const startsW   = chats.filter(c => c.name && c.name.toLowerCase().startsWith(needle) && c.name.toLowerCase() !== needle);
    const substring = chats.filter(c =>
      c.name && c.name.toLowerCase().includes(needle) &&
      !exact.includes(c) && !startsW.includes(c)
    );
    // Phone-number match ONLY if needle has >= 8 digits — avoids `.includes('')` bug
    const numberMatch = (cleanNumber.length >= 8)
      ? chats.filter(c => c.id._serialized.includes(cleanNumber))
      : [];

    let candidates = [...exact, ...startsW, ...substring, ...numberMatch]
      .filter((c, i, a) => a.indexOf(c) === i)   // dedupe
      .sort(sortByIndividualFirst);

    // If multiple candidates AND not a single exact individual match, refuse + show options
    const exactIndividuals = exact.filter(c => !c.isGroup);
    if (exactIndividuals.length === 1) {
      candidates = exactIndividuals;             // unambiguous winner
    } else if (candidates.length > 1) {
      return res.status(409).json({
        success: false,
        error: 'Ambiguous contact — multiple chats matched',
        candidates: candidates.slice(0, 8).map(c => ({
          chat_id: c.id._serialized,
          name: c.name,
          isGroup: c.isGroup,
        })),
        hint: 'Re-call /send with chat_id="<exact id>" (and allow_group=true if it is a group).',
      });
    }

    const chat = candidates[0];

    if (!chat) {
      // Try sending by number directly (only if we have enough digits)
      if (cleanNumber.length >= 10) {
        const numberWithCountry = cleanNumber.startsWith('91') ? cleanNumber : `91${cleanNumber}`;
        await client.sendMessage(`${numberWithCountry}@c.us`, message);
        return res.json({ success: true, sent_to: contact });
      }
      return res.status(404).json({ success: false, error: `Contact not found: ${contact}` });
    }

    // SAFETY: refuse to send to a group unless the caller said allow_group=true
    if (chat.isGroup && !allow_group) {
      return res.status(400).json({
        success: false,
        error: `'${contact}' resolved to a GROUP chat ('${chat.name}'). Refusing to send.`,
        chat: { id: chat.id._serialized, name: chat.name, isGroup: true },
        hint: 'Re-call /send with chat_id="<exact id>" and allow_group=true to send to this group.',
      });
    }

    await chat.sendMessage(message);
    res.json({ success: true, sent_to: chat.name, chat_id: chat.id._serialized });
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

/**
 * POST /send-by-id — Send a message directly to a WhatsApp chat ID.
 * Used by Jarvis to reply to incoming messages without contact-name lookup.
 * Also resets the Jarvis session idle timer so conversations stay alive.
 */
app.post('/send-by-id', async (req, res) => {
  if (!clientReady) {
    return res.status(503).json({ success: false, error: 'WhatsApp not ready', status: clientStatus });
  }
  const { chat_id, message } = req.body;
  if (!chat_id || !message) {
    return res.status(400).json({ success: false, error: 'chat_id and message required' });
  }
  // Register body SYNCHRONOUSLY before await — message_create may fire before
  // the promise resolves, so ID-based tracking is always too late.
  jarvisSentBodies.set(message, (jarvisSentBodies.get(message) || 0) + 1);
  // Safety cleanup after 10s in case message_create never fires for this message
  setTimeout(() => {
    const n = (jarvisSentBodies.get(message) || 1) - 1;
    if (n <= 0) jarvisSentBodies.delete(message);
    else jarvisSentBodies.set(message, n);
  }, 10000);

  try {
    await client.sendMessage(chat_id, message);
    touchWaSession(); // Jarvis replied — reset the 5-min idle clock
    res.json({ success: true });
  } catch (e) {
    // Undo the pending-send registration on failure
    const n = (jarvisSentBodies.get(message) || 1) - 1;
    if (n <= 0) jarvisSentBodies.delete(message);
    else jarvisSentBodies.set(message, n);
    res.status(500).json({ success: false, error: e.message });
  }
});

// ── Start ──────────────────────────────────────────────────────
app.listen(PORT, '127.0.0.1', () => {
  console.log(`\nJARVIS WhatsApp Bridge starting on http://127.0.0.1:${PORT}`);
  console.log('Initializing WhatsApp client…\n');
  client.initialize().catch(e => {
    console.error('WhatsApp initialization error:', e.message);
  });
});

process.on('SIGINT', async () => {
  console.log('\nShutting down WhatsApp bridge…');
  try { await client.destroy(); } catch {}
  process.exit(0);
});
