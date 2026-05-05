/* ═══════════════════════════════════════════════════════════════
   J.A.R.V.I.S — WebSocket streaming client
   ═══════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────────────────────
let ws = null;
// Persist session across page reloads — restore from localStorage or create new
let sessionId = localStorage.getItem('jarvis_session_id') || generateSessionId();
localStorage.setItem('jarvis_session_id', sessionId);
let isProcessing = false;
let currentAssistantBubble = null;
let currentAssistantContent = '';
let reconnectTimer = null;
let reconnectDelay = 1000;
// Expose ws + helpers globally so voice-mode.js can reuse the same socket
Object.defineProperty(window, 'ws', { get: () => ws, configurable: true });

// ── DOM refs ───────────────────────────────────────────────────
const messagesArea    = document.getElementById('messagesArea');
const messageInput    = document.getElementById('messageInput');
const sendBtn         = document.getElementById('sendBtn');
const typingIndicator = document.getElementById('typingIndicator');
const typingLabel     = document.getElementById('typingLabel');
const wsStatusDot     = document.getElementById('wsStatusDot');
const wsStatusText    = document.getElementById('wsStatusText');
const connIndicator   = document.getElementById('connIndicator');
const modelBadge      = document.getElementById('modelBadge');
const modelName       = document.getElementById('modelName');
const welcomeScreen   = document.getElementById('welcomeScreen');
const charCount       = document.getElementById('charCount');
const sessionList     = document.getElementById('sessionList');
const newChatBtn      = document.getElementById('newChatBtn');
const sidebarToggle   = document.getElementById('sidebarToggle');
const sidebar         = document.getElementById('sidebar');
const attachBtn       = document.getElementById('attachBtn');
const fileInput       = document.getElementById('fileInput');

// ── WebSocket ──────────────────────────────────────────────────
function connectWS() {
  if (ws && ws.readyState < 2) return;
  // Use wss:// when the page is HTTPS (e.g. via Tailscale), ws:// otherwise.
  // Browsers block insecure WebSockets on HTTPS pages (mixed content).
  const wsProto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
  ws = new WebSocket(`${wsProto}//${location.host}/ws/${sessionId}`);

  ws.onopen = () => {
    reconnectDelay = 1000;
    setConnectionState('connected');
    clearTimeout(reconnectTimer);
  };

  ws.onclose = () => {
    setConnectionState('disconnected');
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
      connectWS();
    }, reconnectDelay);
  };

  ws.onerror = () => setConnectionState('error');

  ws.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); }
    catch { return; }
    handleStreamEvent(data);
  };
}

function handleStreamEvent(event) {
  // Forward text + done to voice mode so it can speak the reply via TTS
  const voiceActive = window.voiceMode && window.voiceMode.isActive && window.voiceMode.isActive();
  switch (event.type) {
    case 'text':
      appendStreamToken(event.content);
      if (voiceActive && window.voiceMode.handleAssistantText) {
        window.voiceMode.handleAssistantText(event.content, false);
      }
      break;

    case 'tool_start':
      appendToolCard(event.tool_name);
      break;

    case 'tool_end':
      updateToolCard(event.tool_name);
      break;

    case 'model_info':
      updateModelBadge(event.model);
      break;

    case 'done':
      finalizeAssistantMessage();
      setProcessing(false);
      fetchUsage(); // refresh credit display after every reply
      if (voiceActive && window.voiceMode.handleAssistantText) {
        window.voiceMode.handleAssistantText('', true);
      }
      break;

    case 'error':
      appendErrorMessage(event.message);
      setProcessing(false);
      break;

    case 'notification':
      appendScheduledNotification(event.task_name, event.content, event.timestamp);
      incrementNotificationBadge();
      break;

    case 'usage_updated':
      // Pushed by the server after a WhatsApp reply (or any other off-UI activity).
      // Refreshes the credits widget so it stays in sync with billing in real time.
      fetchUsage();
      break;
  }
}

// ── Sending messages ───────────────────────────────────────────
function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || isProcessing) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showToast('Not connected to Jarvis. Reconnecting…', 'error');
    connectWS();
    return;
  }

  hideWelcome();
  appendUserMessage(text);
  messageInput.value = '';
  autoResizeInput();
  updateCharCount();
  sendBtn.disabled = true;

  setProcessing(true);
  ws.send(JSON.stringify({ type: 'message', content: text, session_id: sessionId }));
  refreshSessionsAfterMessage();
}

// ── Message rendering ──────────────────────────────────────────
function appendUserMessage(text) {
  const group = createMessageGroup('user', 'You');
  const bubble = group.querySelector('.msg-bubble');
  const content = document.createElement('div');
  content.className = 'msg-content';
  content.textContent = text;
  bubble.appendChild(content);
  messagesArea.appendChild(group);
  scrollToBottom();
}

function ensureAssistantGroup() {
  if (currentAssistantBubble) return;
  const group = createMessageGroup('assistant', 'J.A.R.V.I.S');
  currentAssistantBubble = group.querySelector('.msg-content');
  currentAssistantContent = '';
  messagesArea.appendChild(group);
}

function appendStreamToken(token) {
  ensureAssistantGroup();
  // Auto-collapse the tool group when real text starts flowing
  if (currentToolGroupEl && currentToolGroupEl.classList.contains('tg-open')) {
    currentToolGroupEl.classList.remove('tg-open');
    currentToolGroupEl.classList.add('tg-collapsed');
    _renderToolGroup();
  }
  currentAssistantContent += token;
  currentAssistantBubble.innerHTML = renderMarkdown(currentAssistantContent);
  scrollToBottom();
}

function finalizeAssistantMessage() {
  if (currentAssistantBubble && currentAssistantContent) {
    currentAssistantBubble.innerHTML = renderMarkdown(currentAssistantContent);
  }
  // If there was no text (tool-only response), collapse the tool group
  if (currentToolGroupEl && currentToolList.length) {
    currentToolGroupEl.classList.remove('tg-open');
    currentToolGroupEl.classList.add('tg-collapsed');
    _renderToolGroup();
  }
  currentAssistantBubble = null;
  currentAssistantContent = '';
  currentToolGroupEl = null;
  currentToolList = [];
  scrollToBottom();
}

function appendErrorMessage(msg) {
  const group = createMessageGroup('assistant', 'J.A.R.V.I.S');
  const bubble = group.querySelector('.msg-bubble');
  bubble.style.borderLeftColor = 'var(--danger)';
  const content = document.createElement('div');
  content.className = 'msg-content';
  content.innerHTML = `<span style="color:var(--danger)">⚠ ${escapeHtml(msg)}</span>`;
  bubble.appendChild(content);
  messagesArea.appendChild(group);
  scrollToBottom();
  currentAssistantBubble = null;
  currentAssistantContent = '';
}

function createMessageGroup(role, name) {
  const group = document.createElement('div');
  group.className = `message-group ${role}`;

  if (role === 'assistant') {
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = 'J';
    group.appendChild(avatar);
  }

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';

  const meta = document.createElement('div');
  meta.className = 'msg-meta';

  const nameEl = document.createElement('span');
  nameEl.className = 'msg-name';
  nameEl.textContent = name;
  meta.appendChild(nameEl);

  const timeEl = document.createElement('span');
  timeEl.className = 'msg-time';
  timeEl.textContent = now();
  meta.appendChild(timeEl);

  if (role === 'assistant') {
    const modelEl = document.createElement('span');
    modelEl.className = `msg-model ${modelName.textContent.toLowerCase()}`;
    modelEl.textContent = modelName.textContent;
    meta.appendChild(modelEl);
    bubble._modelEl = modelEl;
  }

  const content = document.createElement('div');
  content.className = 'msg-content';

  bubble.appendChild(meta);
  bubble.appendChild(content);
  group.appendChild(bubble);
  return group;
}

// ── Tool group (Claude-style collapsible) ──────────────────────
let currentToolGroupEl = null;
let currentToolList    = []; // [{name, label, status: 'running'|'done'}]

function _getOrCreateToolGroup() {
  if (currentToolGroupEl) return currentToolGroupEl;
  ensureAssistantGroup();
  const bubble = currentAssistantBubble.closest('.msg-bubble');
  const el = document.createElement('div');
  el.className = 'tool-group tg-open';
  bubble.insertBefore(el, currentAssistantBubble);
  currentToolGroupEl = el;
  return el;
}

function _renderToolGroup() {
  const el = currentToolGroupEl;
  if (!el) return;
  const total     = currentToolList.length;
  const allDone   = currentToolList.every(t => t.status === 'done');
  const collapsed = el.classList.contains('tg-collapsed');

  const label = allDone
    ? `Used ${total} tool${total !== 1 ? 's' : ''}`
    : `Working…`;

  const listHtml = currentToolList.map(tc => `
    <div class="tg-item ${tc.status}">
      ${tc.status === 'done'
        ? `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`
        : `<div class="tg-spin"></div>`}
      <span>${tc.label}</span>
    </div>`).join('');

  el.innerHTML = `
    <button class="tg-toggle" type="button">
      <svg class="tg-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
      <span>${label}</span>
    </button>
    <div class="tg-list">${listHtml}</div>`;

  el.querySelector('.tg-toggle').addEventListener('click', () => {
    el.classList.toggle('tg-collapsed');
    el.classList.toggle('tg-open');
    _renderToolGroup();
  });
}

function appendToolCard(toolName) {
  currentToolList.push({ name: toolName, label: humanizeToolName(toolName), status: 'running' });
  _getOrCreateToolGroup();
  _renderToolGroup();
  // Keep typing indicator visible — fast-pulse mode shows tool is executing
  typingIndicator.classList.add('tool-active');
  stopPhraseCycle();
  typingLabel.textContent = `Using ${humanizeToolName(toolName)}…`;
  scrollToBottom();
}

function updateToolCard(toolName) {
  const tc = currentToolList.find(t => t.name === toolName && t.status === 'running');
  if (tc) tc.status = 'done';
  _renderToolGroup();
  // Tool finished — drop back to slow-pulse "thinking" mode
  typingIndicator.classList.remove('tool-active');
  typingLabel.textContent = 'Processing results…';
  startPhraseCycle();
}

function showToolIndicator(toolName) {
  typingLabel.textContent = `Using ${humanizeToolName(toolName)}…`;
}

// ── Fun rotating phrases for the "thinking" state ──────────────
const THINKING_PHRASES = [
  'Engaging systems…',
  'Consulting the database…',
  'Crunching numbers…',
  'Spinning up neurons…',
  'Triangulating context…',
  'Plotting trajectory…',
  'Routing through tools…',
  'Thinking at 200 IQ…',
  'Aligning satellites…',
  'Compiling the brilliant…',
];
let _phraseTimer = null;
let _phraseIdx   = 0;

function startPhraseCycle() {
  stopPhraseCycle();
  _phraseIdx = Math.floor(Math.random() * THINKING_PHRASES.length);
  // Don't immediately overwrite — only swap if currently in "Processing…"-ish state
  _phraseTimer = setInterval(() => {
    // Don't override custom labels (Using X, Processing results, etc.)
    if (typingIndicator.classList.contains('tool-active')) return;
    _phraseIdx = (_phraseIdx + 1) % THINKING_PHRASES.length;
    typingLabel.textContent = THINKING_PHRASES[_phraseIdx];
  }, 1700);
}
function stopPhraseCycle() {
  if (_phraseTimer) { clearInterval(_phraseTimer); _phraseTimer = null; }
}

// ── State helpers ──────────────────────────────────────────────
function setProcessing(val) {
  isProcessing = val;
  typingIndicator.style.display = val ? 'flex' : 'none';
  if (val) {
    typingIndicator.classList.remove('tool-active');
    typingLabel.textContent = THINKING_PHRASES[Math.floor(Math.random() * THINKING_PHRASES.length)];
    startPhraseCycle();
  } else {
    stopPhraseCycle();
    typingIndicator.classList.remove('tool-active');
    typingLabel.textContent = 'Processing…';
  }
  // Toggle send ↔ stop mode on the button
  if (val) {
    sendBtn.classList.add('is-stop');
    sendBtn.disabled = false;     // always clickable so user can stop
    sendBtn.title = 'Stop response';
  } else {
    sendBtn.classList.remove('is-stop');
    sendBtn.disabled = messageInput.value.trim().length === 0;
    sendBtn.title = 'Send message';
  }
}

// ── Stop mid-response ──────────────────────────────────────────
function stopResponse() {
  if (!isProcessing) return;
  // Closing the WebSocket makes the server's WebSocketDisconnect fire, stopping the agent loop
  if (ws) { try { ws.close(); } catch {} }
  // Keep whatever already streamed
  if (currentAssistantBubble) {
    if (!currentAssistantContent.trim()) appendStreamToken('…');
    finalizeAssistantMessage();
  }
  setProcessing(false);
  setTimeout(connectWS, 200); // reconnect for next message
}

function setConnectionState(state) {
  wsStatusDot.className = `status-dot ${state === 'connected' ? 'connected' : 'error'}`;
  wsStatusText.textContent = state === 'connected' ? 'Connected' : state === 'disconnected' ? 'Reconnecting…' : 'Error';
  connIndicator.className = `conn-indicator ${state !== 'connected' ? 'offline' : ''}`;
  connIndicator.querySelector('span').textContent = state === 'connected' ? 'LIVE' : 'OFFLINE';
}

function updateModelBadge(model) {
  if (model.startsWith('groq:')) {
    modelBadge.className = 'model-badge groq';
    modelName.textContent = 'Groq';
  } else if (model.startsWith('ollama:')) {
    const name = model.replace('ollama:', '').split(':')[0];
    modelBadge.className = 'model-badge local';
    modelName.textContent = name.charAt(0).toUpperCase() + name.slice(1);
  } else {
    const isOpus = model.includes('opus');
    modelBadge.className = `model-badge ${isOpus ? 'opus' : 'sonnet'}`;
    modelName.textContent = isOpus ? 'Opus' : 'Sonnet';
  }
}

function hideWelcome() {
  const w = document.getElementById('welcomeScreen');
  if (w) { w.style.display = 'none'; }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesArea.scrollTop = messagesArea.scrollHeight;
  });
}

function autoResizeInput() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
}

function updateCharCount() {
  const len = messageInput.value.length;
  charCount.textContent = `${len} / 10000`;
  charCount.style.color = len > 9000 ? 'var(--danger)' : len > 8000 ? 'var(--warning)' : 'var(--text-muted)';
}

// ── Markdown renderer (lightweight) ────────────────────────────
function renderMarkdown(text) {
  return text
    // Code blocks
    .replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><code class="lang-${lang}">${escapeHtml(code.trim())}</code></pre>`)
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Headers
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold + italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // HR
    .replace(/^---$/gm, '<hr>')
    // Blockquote
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    // Unordered list
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
    // Ordered list
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // Links
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    // Paragraphs (double newline)
    .replace(/\n\n/g, '</p><p>')
    .replace(/^(.+)$/, '<p>$1</p>')
    // Single newlines inside text
    .replace(/\n/g, '<br>');
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Toast ──────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  toast.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${escapeHtml(message)}</span>`;
  document.getElementById('toastContainer').appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── Utility ────────────────────────────────────────────────────
function generateSessionId() {
  return 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

function now() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function humanizeToolName(name) {
  const labels = {
    web_search: 'Searching the web',
    fetch_url: 'Reading webpage',
    read_file: 'Reading file',
    write_file: 'Writing file',
    list_directory: 'Listing directory',
    search_files: 'Searching files',
    file_operation: 'File operation',
    run_command: 'Running command',
    launch_application: 'Launching app',
    take_screenshot: 'Taking screenshot',
    system_info: 'System diagnostics',
    clipboard: 'Clipboard',
    run_python: 'Running Python',
    drive_search: 'Searching Drive',
    drive_read: 'Reading Drive file',
    drive_create: 'Creating Drive file',
    drive_list_recent: 'Listing Drive files',
    gmail_send_email: 'Sending email',
    gmail_read_inbox: 'Reading inbox',
    gmail_search_emails: 'Searching Gmail',
    gmail_create_draft: 'Creating email draft',
    gmail_reply_email: 'Replying to email',
    calendar_list_events: 'Checking calendar',
    calendar_create_event: 'Creating event',
    calendar_get_event: 'Getting event details',
    calendar_update_event: 'Updating event',
    calendar_delete_event: 'Deleting event',
    whatsapp_list_chats: 'Listing WhatsApp chats',
    whatsapp_read_messages: 'Reading WhatsApp messages',
    whatsapp_send_message: 'Sending WhatsApp message',
    linkedin_get_profile: 'Checking LinkedIn profile',
    linkedin_list_posts: 'Getting LinkedIn posts',
    linkedin_create_post: 'Creating LinkedIn post',
    linkedin_get_analytics: 'Getting LinkedIn analytics',
    youtube_search: 'Searching YouTube',
    youtube_get_transcript: 'Getting video transcript',
    youtube_channel_analytics: 'Getting channel analytics',
    youtube_get_video_info: 'Getting video info',
    zomato_search_restaurants: 'Searching restaurants',
    zomato_get_menu: 'Getting menu',
    zomato_place_order: 'Placing order',
    zomato_track_order: 'Tracking order',
    zomato_get_addresses: 'Getting addresses',
    remember: 'Saving to memory',
    recall: 'Checking memory',
    schedule_task: 'Creating scheduled task',
    list_schedules: 'Listing scheduled tasks',
    cancel_schedule: 'Cancelling scheduled task',
    // YouTube
    youtube_get_video_info: 'Getting video info',
    youtube_get_transcript: 'Getting transcript',
    youtube_get_comments: 'Getting comments',
    youtube_search: 'Searching YouTube',
    youtube_get_channel_info: 'Getting channel info',
    youtube_analyze_video: 'Analyzing video',
    youtube_research_topic: 'Researching topic',
    youtube_list_my_videos: 'Listing my videos',
    youtube_get_channel_analytics: 'Getting channel analytics',
    youtube_get_video_analytics: 'Getting video analytics',
    youtube_post_comment: 'Posting comment',
    youtube_reply_to_comment: 'Replying to comment',
    youtube_update_video: 'Updating video',
    youtube_delete_video: 'Deleting video',
    // LinkedIn
    linkedin_get_profile: 'Getting LinkedIn profile',
    linkedin_get_dashboard: 'Getting LinkedIn dashboard',
    linkedin_list_posts: 'Getting LinkedIn posts',
    linkedin_create_post: 'Creating LinkedIn post',
    linkedin_delete_post: 'Deleting LinkedIn post',
    linkedin_get_post_analytics: 'Getting post analytics',
    linkedin_get_all_post_analytics: 'Getting all analytics',
    linkedin_get_network_size: 'Getting network size',
    linkedin_scrape_my_posts: 'Scraping posts',
    linkedin_scrape_profile: 'Scraping profile',
    linkedin_update_cache: 'Updating LinkedIn cache',
    // Zomato
    zomato_get_restaurants_for_keyword: 'Searching restaurants',
    zomato_get_restaurant_menu_by_categories: 'Getting menu',
    zomato_get_menu_items_listing: 'Getting menu items',
    zomato_create_cart: 'Creating cart',
    zomato_get_cart_offers: 'Getting offers',
    zomato_checkout_cart: 'Checking out',
    zomato_get_saved_addresses_for_user: 'Getting addresses',
    zomato_get_order_history: 'Getting order history',
    zomato_get_order_tracking_info: 'Tracking order',
  };
  return labels[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ── Event Listeners ────────────────────────────────────────────
messageInput.addEventListener('input', () => {
  autoResizeInput();
  updateCharCount();
  sendBtn.disabled = messageInput.value.trim().length === 0 || isProcessing;
});

messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', () => {
  if (isProcessing) stopResponse();
  else sendMessage();
});

newChatBtn.addEventListener('click', () => {
  sessionId = generateSessionId();
  localStorage.setItem('jarvis_session_id', sessionId);
  // Reconnect WebSocket with new session ID
  if (ws) { ws.onclose = null; ws.close(); }
  messagesArea.innerHTML = '';
  const welcome = document.createElement('div');
  welcome.id = 'welcomeScreen';
  welcome.className = 'welcome-screen';
  welcome.innerHTML = `
    <div class="welcome-arc"><div class="arc-reactor large">
      <div class="arc-ring arc-ring-1"></div>
      <div class="arc-ring arc-ring-2"></div>
      <div class="arc-ring arc-ring-3"></div>
      <div class="arc-core"></div>
    </div></div>
    <h2 class="welcome-title">New session started, Prem.</h2>
    <p class="welcome-subtitle">What would you like to work on?</p>`;
  messagesArea.appendChild(welcome);
  connectWS();
  messageInput.focus();
  showToast('New session started', 'success', 2000);
  loadSessions();
});

// Sidebar toggle — works on both desktop (.collapsed) and mobile (.open)
function toggleSidebar() {
  if (window.innerWidth <= 700) {
    // Mobile: slide in/out via .open
    sidebar.classList.toggle('open');
    document.body.classList.toggle('sidebar-open-mobile', sidebar.classList.contains('open'));
  } else {
    // Desktop: collapse/expand via .collapsed
    sidebar.classList.toggle('collapsed');
  }
}
sidebarToggle.addEventListener('click', toggleSidebar);

// Mobile: tap outside the sidebar closes it
document.addEventListener('click', (e) => {
  if (window.innerWidth > 700) return;
  if (!sidebar.classList.contains('open')) return;
  // Don't close if click is inside the sidebar or on the toggle itself
  if (sidebar.contains(e.target) || sidebarToggle.contains(e.target)) return;
  sidebar.classList.remove('open');
  document.body.classList.remove('sidebar-open-mobile');
});

// On window resize (e.g. rotate phone), keep the state sane
window.addEventListener('resize', () => {
  if (window.innerWidth > 700) {
    // Desktop — drop mobile-only classes
    sidebar.classList.remove('open');
    document.body.classList.remove('sidebar-open-mobile');
  }
});

// Quick action buttons
document.querySelectorAll('.action-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const prompt = btn.dataset.prompt;
    messageInput.value = prompt;
    autoResizeInput();
    updateCharCount();
    sendBtn.disabled = false;
    sendMessage();
    // After picking a quick action, close the sidebar on mobile
    if (window.innerWidth <= 700) {
      sidebar.classList.remove('open');
      document.body.classList.remove('sidebar-open-mobile');
    } else {
      sidebar.classList.add('collapsed');
    }
  });
});

// Suggestion chips
document.addEventListener('click', (e) => {
  const chip = e.target.closest('.suggestion-chip');
  if (!chip) return;
  const prompt = chip.dataset.prompt;
  messageInput.value = prompt;
  autoResizeInput();
  updateCharCount();
  sendBtn.disabled = false;
  sendMessage();
});

// File attach
attachBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => {
  const files = Array.from(e.target.files);
  if (files.length === 0) return;
  const names = files.map(f => f.name).join(', ');
  messageInput.value = (messageInput.value + ` [Attached: ${names}]`).trim();
  autoResizeInput();
  updateCharCount();
  sendBtn.disabled = false;
  fileInput.value = '';
});

// ── Scheduled Notifications ────────────────────────────────────
let notificationCount = 0;

function appendScheduledNotification(taskName, content, timestamp) {
  hideWelcome();
  const group = document.createElement('div');
  group.className = 'message-group assistant notification-group';

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar notification-avatar';
  avatar.textContent = '🔔';
  group.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble notification-bubble';

  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  meta.innerHTML = `
    <span class="msg-name">Scheduled: ${escapeHtml(taskName)}</span>
    <span class="msg-time">${timestamp || now()}</span>
    <span class="msg-model sonnet" style="background:rgba(139,92,246,0.15);color:#8b5cf6">AUTO</span>
  `;

  const contentEl = document.createElement('div');
  contentEl.className = 'msg-content';
  contentEl.innerHTML = renderMarkdown(content);

  bubble.appendChild(meta);
  bubble.appendChild(contentEl);
  group.appendChild(bubble);
  messagesArea.appendChild(group);
  scrollToBottom();

  // Show toast
  showToast(`📋 Scheduled update: ${taskName}`, 'info', 5000);
}

function incrementNotificationBadge() {
  notificationCount++;
  const bell = document.getElementById('notifBell');
  if (bell) {
    bell.dataset.count = notificationCount;
    bell.classList.add('has-notif');
  }
}

// ── Session management ─────────────────────────────────────────

async function loadSessions() {
  try {
    const resp = await fetch('/sessions');
    const data = await resp.json();
    renderSessionList(data.sessions || []);
  } catch (e) {
    console.error('Failed to load sessions:', e);
  }
}

function renderSessionList(sessions) {
  if (!sessions.length) {
    sessionList.innerHTML = '<div class="session-empty">No previous sessions</div>';
    return;
  }
  sessionList.innerHTML = '';
  for (const s of sessions) {
    const item = document.createElement('div');
    item.className = 'session-item';
    if (s.session_id === sessionId) item.classList.add('active');

    const rawTitle = s.first_message || ('Session ' + s.session_id.slice(-6));
    const title = rawTitle.length > 42 ? rawTitle.slice(0, 42) + '…' : rawTitle;

    // Format date
    const d = new Date(s.last_active ? s.last_active.replace(' ', 'T') + 'Z' : s.started.replace(' ', 'T') + 'Z');
    const isToday = d.toDateString() === new Date().toDateString();
    const dateStr = isToday
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' });

    item.innerHTML = `
      <div class="session-title">${escapeHtml(title)}</div>
      <div class="session-meta">${dateStr} &middot; ${Math.ceil(s.turns / 2)} msg${s.turns > 2 ? 's' : ''}</div>
      <button class="session-delete" title="Delete session">×</button>`;

    item.dataset.sessionId = s.session_id;

    item.querySelector('.session-delete').addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch(`/sessions/${s.session_id}`, { method: 'DELETE' });
      if (s.session_id === sessionId) {
        // Deleted the active session — start a fresh one
        sessionId = generateSessionId();
        localStorage.setItem('jarvis_session_id', sessionId);
        messagesArea.innerHTML = '';
        if (welcomeScreen) messagesArea.appendChild(welcomeScreen);
        connectWS();
      }
      loadSessions();
    });

    item.addEventListener('click', () => switchSession(s.session_id, item));
    sessionList.appendChild(item);
  }
}

async function switchSession(targetId, clickedItem) {
  if (targetId === sessionId) return;

  // Update active highlight
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  if (clickedItem) clickedItem.classList.add('active');

  sessionId = targetId;
  localStorage.setItem('jarvis_session_id', sessionId);

  // Reconnect WebSocket with new session
  if (ws) { ws.onclose = null; ws.close(); }
  connectWS();

  // Clear chat and show loading
  messagesArea.innerHTML = '';
  hideWelcome();

  try {
    const resp = await fetch(`/sessions/${targetId}/history`);
    const data = await resp.json();
    if (!data.messages || data.messages.length === 0) {
      // Empty session — show welcome
      const w = document.createElement('div');
      w.id = 'welcomeScreen'; w.className = 'welcome-screen';
      w.innerHTML = '<h2 class="welcome-title">Session loaded, Prem.</h2><p class="welcome-subtitle">No messages yet in this session.</p>';
      messagesArea.appendChild(w);
      return;
    }
    for (const msg of data.messages) {
      if (msg.role === 'user') {
        appendUserMessage(msg.content);
      } else if (msg.role === 'assistant') {
        appendRestoredAssistantMessage(msg.content);
      }
    }
    scrollToBottom();
  } catch (e) {
    console.error('Failed to load session history:', e);
    showToast('Failed to load session history', 'error');
  }
}

function appendRestoredAssistantMessage(content) {
  const group = createMessageGroup('assistant', 'J.A.R.V.I.S');
  const contentEl = group.querySelector('.msg-content');
  contentEl.innerHTML = renderMarkdown(content);
  messagesArea.appendChild(group);
}

// After first message in a session, refresh the sidebar to show the new entry
function refreshSessionsAfterMessage() {
  setTimeout(loadSessions, 500);
}

// ── Credits / usage display ────────────────────────────────────
const creditsWidget = document.getElementById('creditsWidget');
const creditsText   = document.getElementById('creditsText');

// Track previous value so we only flash when it actually changes
let _lastCreditsText = null;

async function fetchUsage() {
  try {
    const r    = await fetch('/usage');
    const data = await r.json();
    if (!creditsText) return;
    const spent = data.total_cost_usd || 0;
    let newText;
    if (data.total_credits_usd != null) {
      const remaining = Math.max(0, data.total_credits_usd - spent);
      const pct = data.total_credits_usd > 0
        ? Math.round((remaining / data.total_credits_usd) * 100) : 100;
      newText = `$${remaining.toFixed(2)} of $${data.total_credits_usd.toFixed(0)}`;
      creditsWidget.classList.toggle('credits-low', pct < 20);
    } else {
      newText = `$${spent.toFixed(3)} used`;
      creditsWidget.classList.remove('credits-low');
    }
    // Only flash if the displayed value actually changed (skip first paint + identical refreshes)
    if (_lastCreditsText !== null && newText !== _lastCreditsText && creditsWidget) {
      creditsWidget.classList.remove('credits-flash');
      // Force reflow so the animation restarts even if class toggles within the same frame
      void creditsWidget.offsetWidth;
      creditsWidget.classList.add('credits-flash');
      setTimeout(() => creditsWidget.classList.remove('credits-flash'), 1100);
    }
    creditsText.textContent = newText;
    _lastCreditsText = newText;
  } catch { /* silent — server may be starting */ }
}

if (creditsWidget) {
  creditsWidget.addEventListener('click', async () => {
    const r    = await fetch('/usage');
    const data = await r.json();
    const cur  = data.total_credits_usd != null ? data.total_credits_usd.toFixed(2) : '';
    const val  = window.prompt(
      'Enter your total Anthropic credit balance (USD):\nLeave blank to show only spent amount.',
      cur
    );
    if (val === null) return; // cancelled
    const amt = parseFloat(val);
    // Empty string = clear; valid number = set
    await fetch('/usage/budget', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({amount: val.trim() === '' ? '' : (isNaN(amt) ? '' : amt)})
    });
    fetchUsage();
  });
}

// Refresh usage after every assistant reply
const _origDone = window._onStreamDone;
fetchUsage();
setInterval(fetchUsage, 60_000); // refresh every minute

// ── Init ───────────────────────────────────────────────────────
connectWS();
messageInput.focus();
loadSessions();

// Restore history for the current session on page load
(async () => {
  try {
    const resp = await fetch(`/sessions/${sessionId}/history`);
    const data = await resp.json();
    if (data.messages && data.messages.length > 0) {
      hideWelcome();
      for (const msg of data.messages) {
        if (msg.role === 'user') appendUserMessage(msg.content);
        else if (msg.role === 'assistant') appendRestoredAssistantMessage(msg.content);
      }
      scrollToBottom();
    }
  } catch (e) {
    // No history or network error — that's fine, show welcome screen
  }
})();

// ═══════════════════════════════════════════════════════════════
// Boot animation + settings popover
// Plays a ~5s Iron-Man-style intro on launch. Skippable, and
// can be permanently disabled from the ⚙ button in the header.
// Preference is persisted in localStorage (key: 'jarvisBootAnim').
//
// Refactored: the actual animation lives in window.startBootSequence
// so it can be triggered AFTER face-login (or directly if face-login
// is disabled).
// ═══════════════════════════════════════════════════════════════

// ── Mic button → voice mode toggle ─────────────────────────────
(function micButtonModule() {
  const btn = document.getElementById('micBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    if (window.voiceMode && typeof window.voiceMode.toggle === 'function') {
      window.voiceMode.toggle();
    } else {
      showToast('Voice mode is loading — try again in a moment.', 'info');
    }
  });
})();

// ═══════════════════════════════════════════════════════════════
// Gesture-only mode (no voice) — small PIP camera + navigation gestures
// (swipes left/right to toggle sidebar; draw "S" with finger to open settings)
// ═══════════════════════════════════════════════════════════════
(function gestureModeModule() {
  const btn       = document.getElementById('gestureBtn');
  const pip       = document.getElementById('gesturePip');
  const closeBtn  = document.getElementById('gesturePipClose');
  const video     = document.getElementById('gesturePipVideo');
  const canvas    = document.getElementById('gesturePipCanvas');
  if (!btn || !pip || !video || !canvas) return;

  let cameraStream = null;
  let active = false;

  async function enable() {
    if (active) return;
    if (!window.jarvisGestures) {
      showToast('Gestures still loading — try again in a sec.', 'info');
      return;
    }
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: 640, height: 480 }, audio: false
      });
      video.srcObject = cameraStream;
      await new Promise(r => { video.onloadedmetadata = () => r(); });
      pip.style.display = 'flex';
      btn.classList.add('gesture-on');
      await window.jarvisGestures.start(video, canvas, 'navigation');
      active = true;
      showToast('Gesture mode on. ✋ swipe left/right · ☝️ draw "S" for settings.', 'success');
    } catch (err) {
      console.error('[gesture-mode]', err);
      showToast(
        err && err.name === 'NotAllowedError'
          ? 'Camera permission denied.'
          : 'Could not start gesture mode.',
        'error'
      );
      disable();
    }
  }

  function disable() {
    if (window.jarvisGestures && window.jarvisGestures.isActive() && window.jarvisGestures.getMode() === 'navigation') {
      window.jarvisGestures.stop();
    }
    if (cameraStream) {
      cameraStream.getTracks().forEach(t => t.stop());
      cameraStream = null;
    }
    pip.style.display = 'none';
    btn.classList.remove('gesture-on');
    active = false;
  }

  btn.addEventListener('click', () => { active ? disable() : enable(); });
  closeBtn.addEventListener('click', disable);

  // Listen for navigation gesture events fired by gestures.js
  window.addEventListener('jarvisNavGesture', (ev) => {
    if (!active) return;
    const d = ev.detail || {};
    const sidebar = document.getElementById('sidebar');
    if (d.type === 'swipe') {
      if (d.dir === 'right') {
        if (sidebar) {
          if (window.innerWidth <= 700) sidebar.classList.add('open');
          else                          sidebar.classList.remove('collapsed');
        }
        showToast('→ Sidebar opened', 'success');
      } else if (d.dir === 'left') {
        if (sidebar) {
          if (window.innerWidth <= 700) sidebar.classList.remove('open');
          else                          sidebar.classList.add('collapsed');
        }
        showToast('← Sidebar closed', 'success');
      } else if (d.dir === 'up') {
        const m = document.getElementById('messagesArea');
        if (m) m.scrollTo({ top: 0, behavior: 'smooth' });
        showToast('↑ Scrolled to top', 'success');
      } else if (d.dir === 'down') {
        const m = document.getElementById('messagesArea');
        if (m) m.scrollTo({ top: m.scrollHeight, behavior: 'smooth' });
        showToast('↓ Scrolled to bottom', 'success');
      }
    } else if (d.type === 'stroke') {
      if (d.name === 'S') {
        const sBtn = document.getElementById('settingsBtn');
        if (sBtn) sBtn.click();
        showToast('⚙ Settings opened (drawn "S")', 'success');
      }
    }
  });
})();

window.startBootSequence = function bootAnimationModule() {
  // Boot animation has been removed — overlay HTML no longer exists.
  // Function kept as a no-op so any external callers don't error out.
  const overlay = document.getElementById('bootOverlay');
  if (!overlay) return;
  // (Defensive — if a stale cached HTML still has the overlay, hide it.)
  overlay.style.display = 'none';
  return;

  const skipBtn   = document.getElementById('bootSkip');
  const textEl    = document.getElementById('bootText');
  const sysVal    = document.getElementById('bootSystems');
  const memVal    = document.getElementById('bootMemory');
  const toolsVal  = document.getElementById('bootTools');
  const sysBar    = document.getElementById('bootBarSystems');
  const memBar    = document.getElementById('bootBarMemory');
  const toolsBar  = document.getElementById('bootBarTools');

  const TOTAL_TOOLS = 38;
  const PHASES = [
    'INITIALIZING…',
    'LOADING CORE SYSTEMS',
    'CONNECTING TO ANTHROPIC',
    'BINDING INTEGRATIONS',
    'CALIBRATING SCHEDULER',
    'WELCOME, PREM',
  ];

  let cancelled = false;
  let timers = [];
  function setT(fn, ms) { const t = setTimeout(fn, ms); timers.push(t); return t; }
  function clearAll() { timers.forEach(clearTimeout); timers = []; }

  function dismiss() {
    if (cancelled) return;
    cancelled = true;
    clearAll();
    overlay.classList.add('boot-leaving');
    setTimeout(() => { overlay.style.display = 'none'; }, 650);
  }

  skipBtn.addEventListener('click', dismiss);
  // ESC also skips
  document.addEventListener('keydown', (e) => {
    if (!cancelled && e.key === 'Escape') dismiss();
  }, { once: false });

  // Animate progress bars + counters smoothly to 100% over ~4.6s
  const DURATION = 4600;
  const startedAt = performance.now();

  function tick(now) {
    if (cancelled) return;
    const elapsed = now - startedAt;
    const p = Math.min(1, elapsed / DURATION);
    // Ease-out so it feels fast at start, settles at end
    const eased = 1 - Math.pow(1 - p, 2);
    const pctSys   = Math.round(eased * 100);
    const pctMem   = Math.round(Math.min(1, eased * 1.05) * 87); // settles at 87% (more realistic)
    const tools    = Math.round(eased * TOTAL_TOOLS);
    sysVal.textContent  = pctSys + '%';
    memVal.textContent  = pctMem + '%';
    toolsVal.textContent = tools + ' / ' + TOTAL_TOOLS;
    sysBar.style.width   = pctSys + '%';
    memBar.style.width   = pctMem + '%';
    toolsBar.style.width = (tools / TOTAL_TOOLS * 100) + '%';
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // Phase text rotates through the messages
  PHASES.forEach((msg, i) => {
    setT(() => { if (!cancelled) textEl.textContent = msg; }, i * (DURATION / PHASES.length));
  });

  // Auto-dismiss after the full sequence (+ a brief beat on the welcome line)
  setT(dismiss, DURATION + 700);
};

// Auto-start boot animation UNLESS face-login is taking over.
// face-login.js will call window.startBootSequence() itself when its
// flow completes (welcome / skip / camera-denied / etc.).
if (!window.__faceLoginPending) {
  document.addEventListener('DOMContentLoaded', () => window.startBootSequence(), { once: true });
  if (document.readyState !== 'loading') window.startBootSequence();
}

// Safety net — if the boot overlay is still visible 12 seconds after page load
// (e.g. a tab was throttled or a JS error halted the animation), just hide it
// so the user reaches the chat UI. Does NOT restart anything — only hides.
setTimeout(() => {
  const bootOv = document.getElementById('bootOverlay');
  if (bootOv && bootOv.style.display !== 'none') {
    bootOv.style.display = 'none';
  }
  const faceOv = document.getElementById('faceLoginOverlay');
  if (faceOv && faceOv.style.display !== 'none' && faceOv.style.display !== '') {
    // Only hide face overlay if user hasn't intentionally engaged (button click)
    // — heuristic: if it has a "stage=loading" still visible, network is hung
    const loadingStage = faceOv.querySelector('[data-stage="loading"]');
    if (loadingStage && loadingStage.style.display === 'flex') {
      faceOv.style.display = 'none';
    }
  }
}, 12000);

// ═══════════════════════════════════════════════════════════════
// Settings popover — single toggle for boot animation
// ═══════════════════════════════════════════════════════════════
(function settingsModule() {
  const btn = document.getElementById('settingsBtn');
  if (!btn) return;
  const PREF_KEY = 'jarvisBootAnim';
  let popover = null;

  function isOn() { return localStorage.getItem(PREF_KEY) !== 'off'; }

  function isFaceOn()    { return localStorage.getItem('jarvisFaceLogin') === 'on'; }
  function getFaceMode() { return localStorage.getItem('jarvisFaceMode') || 'ask'; }
  // Force amber theme on every load (themes feature was removed)
  document.documentElement.removeAttribute('data-theme');
  localStorage.removeItem('jarvisTheme');
  function isVoiceOn()   { return localStorage.getItem('jarvisVoiceMode') === 'on'; }
  function isVoiceCamOn(){ return localStorage.getItem('jarvisVoiceCamera') !== 'off'; }
  function getVoiceRate(){ return localStorage.getItem('jarvisVoiceRate') || '1.0'; }
  function getVoiceId()  { return localStorage.getItem('jarvisVoiceVoiceId') || ''; }
  function listTtsVoices() {
    if (!('speechSynthesis' in window)) return [];
    const voices = speechSynthesis.getVoices() || [];
    return voices.filter(v => v.lang && v.lang.toLowerCase().startsWith('en'));
  }

  function buildPopover() {
    const div = document.createElement('div');
    div.className = 'settings-popover';
    div.innerHTML = `
      <div class="settings-popover-title">Settings</div>
      <div class="settings-row">
        <span>Boot animation on launch</span>
        <div class="settings-toggle ${isOn() ? 'on' : ''}" id="bootAnimToggle"></div>
      </div>
      <div class="settings-divider"></div>
      <div class="settings-row">
        <span>Face-scan login</span>
        <div class="settings-toggle ${isFaceOn() ? 'on' : ''}" id="faceLoginToggle"></div>
      </div>
      <div class="settings-row settings-sub" id="faceModeRow" ${isFaceOn() ? '' : 'style="display:none"'}>
        <span>Default mode</span>
        <select class="settings-select" id="faceModeSelect">
          <option value="ask"${getFaceMode()==='ask'?' selected':''}>Ask each time</option>
          <option value="secure"${getFaceMode()==='secure'?' selected':''}>Secure (owner only)</option>
          <option value="public"${getFaceMode()==='public'?' selected':''}>Public (anyone)</option>
        </select>
      </div>
      <div class="settings-row settings-sub" id="faceReEnrollRow" ${isFaceOn() ? '' : 'style="display:none"'}>
        <button class="settings-link" id="faceReEnrollBtn">Re-enroll my face</button>
      </div>
      <div class="settings-divider"></div>
      <div class="settings-row">
        <span>Voice mode</span>
        <div class="settings-toggle ${isVoiceOn() ? 'on' : ''}" id="voiceToggle"></div>
      </div>
      <div class="settings-row settings-sub" id="voiceCamRow" ${isVoiceOn() ? '' : 'style="display:none"'}>
        <span>Camera in voice mode</span>
        <div class="settings-toggle ${isVoiceCamOn() ? 'on' : ''}" id="voiceCamToggle"></div>
      </div>
      <div class="settings-row settings-sub" id="voiceVoiceRow" ${isVoiceOn() ? '' : 'style="display:none"'}>
        <span>TTS voice</span>
        <select class="settings-select" id="voiceVoiceSelect"></select>
      </div>
      <div class="settings-row settings-sub" id="voiceRateRow" ${isVoiceOn() ? '' : 'style="display:none"'}>
        <span>Speaking rate</span>
        <select class="settings-select" id="voiceRateSelect">
          <option value="0.85"${getVoiceRate()==='0.85'?' selected':''}>0.85x</option>
          <option value="1.0"${getVoiceRate()==='1.0'?' selected':''}>1.0x</option>
          <option value="1.15"${getVoiceRate()==='1.15'?' selected':''}>1.15x</option>
          <option value="1.3"${getVoiceRate()==='1.3'?' selected':''}>1.3x</option>
        </select>
      </div>
      <div class="settings-row settings-sub" id="voiceGestureRow" ${isVoiceOn() ? '' : 'style="display:none"'}>
        <span>Hand gestures (camera mode)</span>
        <div class="settings-toggle ${localStorage.getItem('jarvisGestures')==='on' ? 'on' : ''}" id="voiceGestureToggle"></div>
      </div>
      <div class="settings-row settings-sub" id="voiceSkeletonRow" ${(isVoiceOn() && localStorage.getItem('jarvisGestures')==='on') ? '' : 'style="display:none"'}>
        <span>Show hand skeleton</span>
        <div class="settings-toggle ${localStorage.getItem('jarvisGesturesSkeleton')!=='off' ? 'on' : ''}" id="voiceSkeletonToggle"></div>
      </div>
    `;
    btn.parentElement.style.position = btn.parentElement.style.position || 'relative';
    btn.parentElement.appendChild(div);

    // Boot animation toggle
    const toggle = div.querySelector('#bootAnimToggle');
    toggle.addEventListener('click', () => {
      const newVal = !isOn();
      localStorage.setItem(PREF_KEY, newVal ? 'on' : 'off');
      toggle.classList.toggle('on', newVal);
      if (typeof showToast === 'function') {
        showToast(`Boot animation ${newVal ? 'enabled' : 'disabled'}.`, 'success');
      }
    });

    // Face-login toggle
    const faceToggle = div.querySelector('#faceLoginToggle');
    const faceModeRow     = div.querySelector('#faceModeRow');
    const faceReEnrollRow = div.querySelector('#faceReEnrollRow');
    if (faceToggle) faceToggle.addEventListener('click', () => {
      const newVal = !isFaceOn();
      localStorage.setItem('jarvisFaceLogin', newVal ? 'on' : 'off');
      faceToggle.classList.toggle('on', newVal);
      if (faceModeRow)     faceModeRow.style.display     = newVal ? 'flex' : 'none';
      if (faceReEnrollRow) faceReEnrollRow.style.display = newVal ? 'flex' : 'none';
    });
    const faceModeSelect = div.querySelector('#faceModeSelect');
    if (faceModeSelect) faceModeSelect.addEventListener('change', (e) => {
      localStorage.setItem('jarvisFaceMode', e.target.value);
    });
    const faceReEnrollBtn = div.querySelector('#faceReEnrollBtn');
    if (faceReEnrollBtn) faceReEnrollBtn.addEventListener('click', () => {
      if (typeof window.jarvisClearFaceData === 'function') window.jarvisClearFaceData();
    });

    // Voice mode controls
    const voiceToggle    = div.querySelector('#voiceToggle');
    const voiceCamRow    = div.querySelector('#voiceCamRow');
    const voiceVoiceRow  = div.querySelector('#voiceVoiceRow');
    const voiceRateRow   = div.querySelector('#voiceRateRow');
    const voiceCamToggle = div.querySelector('#voiceCamToggle');
    const voiceSelect    = div.querySelector('#voiceVoiceSelect');
    const voiceRateSelect= div.querySelector('#voiceRateSelect');
    if (voiceSelect) {
      const populateVoices = () => {
        voiceSelect.innerHTML = '';
        const voices = listTtsVoices();
        const cur = getVoiceId();
        if (voices.length === 0) {
          voiceSelect.innerHTML = '<option value="">(default)</option>';
        } else {
          voiceSelect.appendChild(new Option('(system default)', ''));
          for (const v of voices) {
            const opt = new Option(`${v.name} (${v.lang})`, v.name);
            if (v.name === cur) opt.selected = true;
            voiceSelect.appendChild(opt);
          }
        }
      };
      populateVoices();
      if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = populateVoices;
    }
    if (voiceToggle) voiceToggle.addEventListener('click', () => {
      const newVal = !isVoiceOn();
      localStorage.setItem('jarvisVoiceMode', newVal ? 'on' : 'off');
      voiceToggle.classList.toggle('on', newVal);
      if (voiceCamRow)   voiceCamRow.style.display   = newVal ? 'flex' : 'none';
      if (voiceVoiceRow) voiceVoiceRow.style.display = newVal ? 'flex' : 'none';
      if (voiceRateRow)  voiceRateRow.style.display  = newVal ? 'flex' : 'none';
    });
    if (voiceCamToggle) voiceCamToggle.addEventListener('click', () => {
      const newVal = !isVoiceCamOn();
      localStorage.setItem('jarvisVoiceCamera', newVal ? 'on' : 'off');
      voiceCamToggle.classList.toggle('on', newVal);
    });
    if (voiceSelect) voiceSelect.addEventListener('change', (e) => {
      localStorage.setItem('jarvisVoiceVoiceId', e.target.value || '');
    });
    if (voiceRateSelect) voiceRateSelect.addEventListener('change', (e) => {
      localStorage.setItem('jarvisVoiceRate', e.target.value);
    });

    // Hand gesture toggle
    const gestureToggle  = div.querySelector('#voiceGestureToggle');
    const skeletonRow    = div.querySelector('#voiceSkeletonRow');
    const skeletonToggle = div.querySelector('#voiceSkeletonToggle');
    if (gestureToggle) gestureToggle.addEventListener('click', () => {
      const newVal = localStorage.getItem('jarvisGestures') !== 'on';
      localStorage.setItem('jarvisGestures', newVal ? 'on' : 'off');
      gestureToggle.classList.toggle('on', newVal);
      if (skeletonRow) skeletonRow.style.display = newVal ? 'flex' : 'none';
    });
    if (skeletonToggle) skeletonToggle.addEventListener('click', () => {
      const newVal = localStorage.getItem('jarvisGesturesSkeleton') === 'off';
      localStorage.setItem('jarvisGesturesSkeleton', newVal ? 'on' : 'off');
      skeletonToggle.classList.toggle('on', newVal);
    });

    return div;
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (popover) { popover.remove(); popover = null; return; }
    popover = buildPopover();
  });

  // Close on outside click
  document.addEventListener('click', (e) => {
    if (popover && !popover.contains(e.target) && e.target !== btn) {
      popover.remove(); popover = null;
    }
  });
})();
