/* Aegixa Console — channel browser + send-as-bot */

let currentGuildId = null;
let currentChannelId = null;
let currentChannelName = null;
let loadingMore = false;
let oldestMessageId = null;

// ---------------------------------------------------------------------------
// Guild selection
// ---------------------------------------------------------------------------

async function selectGuild() {
  const sel = document.getElementById('guild-select');
  currentGuildId = sel.value;
  currentChannelId = null;
  document.getElementById('channel-list').innerHTML = '';
  document.getElementById('messages-container').innerHTML = '<div class="console-placeholder">Select a channel.</div>';
  document.getElementById('messages-header').classList.add('hidden');
  document.getElementById('send-area').classList.add('hidden');
  if (!currentGuildId) return;

  const channels = await fetch(`/console/guild/${currentGuildId}/channels`).then(r => r.json());
  renderChannels(channels);
}

function renderChannels(channels) {
  const list = document.getElementById('channel-list');
  let html = '';
  let lastCategory = null;
  for (const ch of channels) {
    if (ch.category !== lastCategory) {
      html += `<div class="channel-category">${ch.category || 'No Category'}</div>`;
      lastCategory = ch.category;
    }
    html += `<div class="channel-item" data-id="${ch.id}" onclick="selectChannel('${ch.id}', '${escHtml(ch.name)}')">${escHtml(ch.name)}</div>`;
  }
  list.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Channel selection
// ---------------------------------------------------------------------------

async function selectChannel(channelId, channelName) {
  currentChannelId = channelId;
  currentChannelName = channelName;
  oldestMessageId = null;

  document.querySelectorAll('.channel-item').forEach(el => el.classList.toggle('active', el.dataset.id === channelId));
  document.getElementById('channel-name').textContent = channelName;
  document.getElementById('messages-header').classList.remove('hidden');
  document.getElementById('send-area').classList.remove('hidden');

  const container = document.getElementById('messages-container');
  container.innerHTML = '<div class="spinner"></div>';

  const messages = await fetchMessages(channelId);
  container.innerHTML = '';
  if (!messages.length) {
    container.innerHTML = '<div class="console-placeholder">No messages.</div>';
    return;
  }
  // Messages come newest-first; reverse to show oldest at top
  messages.reverse().forEach(m => container.appendChild(renderMessage(m)));
  container.scrollTop = container.scrollHeight;
  oldestMessageId = messages[0]?.id || null;

  // Infinite scroll upward
  container.addEventListener('scroll', onScroll);
}

async function fetchMessages(channelId, before = null) {
  let url = `/console/guild/${currentGuildId}/channel/${channelId}/messages?limit=50`;
  if (before) url += `&before=${before}`;
  return fetch(url).then(r => r.json());
}

async function onScroll() {
  const container = document.getElementById('messages-container');
  if (container.scrollTop > 100 || loadingMore || !oldestMessageId) return;
  loadingMore = true;
  const older = await fetchMessages(currentChannelId, oldestMessageId);
  if (older.length) {
    const prevHeight = container.scrollHeight;
    older.reverse().forEach(m => container.insertBefore(renderMessage(m), container.firstChild));
    container.scrollTop = container.scrollHeight - prevHeight;
    oldestMessageId = older[older.length - 1]?.id || oldestMessageId;
  }
  loadingMore = false;
}

// ---------------------------------------------------------------------------
// Message rendering
// ---------------------------------------------------------------------------

function renderMessage(msg) {
  const div = document.createElement('div');
  div.className = 'message-row';
  const time = new Date(msg.timestamp).toLocaleString();

  let content = escHtml(msg.content || '');
  // Resolve <@id> mentions to styled @Name spans
  content = content.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
    const name = (msg.mentions || {})[id] || id;
    return `<span class="mention">@${escHtml(name)}</span>`;
  });

  let embedsHtml = '';
  for (const e of msg.embeds || []) {
    const borderColor = e.color ? `#${e.color.toString(16).padStart(6,'0')}` : '#5865f2';
    embedsHtml += `<div class="msg-embed" style="border-left-color:${borderColor}">`;
    if (e.title) embedsHtml += `<div class="embed-title">${escHtml(e.title)}</div>`;
    if (e.description) embedsHtml += `<div class="embed-desc">${escHtml(e.description)}</div>`;
    if (e.fields?.length) {
      embedsHtml += '<div style="margin-top:.5rem;display:flex;flex-wrap:wrap;gap:.5rem">';
      for (const f of e.fields) {
        embedsHtml += `<div style="min-width:${f.inline ? '140px' : '100%'}"><b>${escHtml(f.name)}</b><br/>${escHtml(f.value)}</div>`;
      }
      embedsHtml += '</div>';
    }
    if (e.image) embedsHtml += `<img class="embed-image" src="${e.image}" alt="embed image"/>`;
    if (e.footer) embedsHtml += `<div style="font-size:.75rem;color:#949ba4;margin-top:.4rem">${escHtml(e.footer)}</div>`;
    embedsHtml += '</div>';
  }

  let attachmentsHtml = '';
  for (const a of msg.attachments || []) {
    if (a.content_type && a.content_type.startsWith('image/')) {
      attachmentsHtml += `<div class="msg-attachment"><img src="${a.url}" alt="${escHtml(a.filename)}" loading="lazy"/></div>`;
    } else {
      attachmentsHtml += `<div class="msg-attachment"><a href="${a.url}" target="_blank">${escHtml(a.filename)}</a></div>`;
    }
  }

  let reactionsHtml = '';
  if (msg.reactions?.length) {
    reactionsHtml = '<div style="margin-top:.4rem;display:flex;flex-wrap:wrap;gap:.35rem">';
    for (const r of msg.reactions) {
      reactionsHtml += `<span style="background:var(--bg-tertiary);border-radius:6px;padding:.15rem .45rem;font-size:.85rem">${r.emoji} ${r.count}</span>`;
    }
    reactionsHtml += '</div>';
  }

  div.innerHTML = `
    <img class="msg-avatar" src="${msg.author.avatar}" alt="${escHtml(msg.author.name)}" onerror="this.style.display='none'"/>
    <div class="msg-body">
      <div class="msg-header">
        <span class="msg-author">${escHtml(msg.author.name)}${msg.author.bot ? ' <span style="background:#5865f2;color:#fff;font-size:.65rem;padding:.1rem .35rem;border-radius:4px;font-weight:700">BOT</span>' : ''}</span>
        <span class="msg-time">${time}</span>
      </div>
      ${content ? `<div class="msg-content">${content}</div>` : ''}
      ${embedsHtml}${attachmentsHtml}${reactionsHtml}
    </div>`;
  return div;
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------

async function sendMessage() {
  const input = document.getElementById('send-input');
  const content = input.value.trim();
  if (!content || !currentGuildId || !currentChannelId) return;

  const res = await fetch(`/console/guild/${currentGuildId}/channel/${currentChannelId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (res.ok) {
    input.value = '';
  } else {
    alert('Failed to send message.');
  }
}

document.getElementById('send-input')?.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ---------------------------------------------------------------------------
// Member search
// ---------------------------------------------------------------------------

let memberSearchTimer;
async function searchMembers() {
  clearTimeout(memberSearchTimer);
  memberSearchTimer = setTimeout(async () => {
    if (!currentGuildId) return;
    const q = document.getElementById('member-search').value.trim();
    const members = await fetch(`/console/guild/${currentGuildId}/members?q=${encodeURIComponent(q)}`).then(r => r.json());
    const list = document.getElementById('member-list');
    list.innerHTML = members.map(m => `
      <div class="member-item">
        <img class="member-avatar" src="${m.avatar}" alt="${escHtml(m.display_name)}" onerror="this.src=''"/>
        <span>${escHtml(m.display_name)}</span>
      </div>`).join('');
  }, 300);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
