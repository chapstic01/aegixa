/* Aegixa dashboard — fetch-based AJAX for all save/toggle actions */

const API = `/api/guild/${GUILD_ID}`;
let channelOptions = [];
let roleOptions = [];

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.style.cssText = 'position:fixed;top:70px;right:1rem;z-index:9999;max-width:360px;animation:fadeIn .2s';
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

async function apiFetch(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  return res.json();
}

function channelSelect(name, selectedId = '') {
  const opts = channelOptions.map(c =>
    `<option value="${c.id}" ${c.id === selectedId ? 'selected' : ''}>#${c.name}</option>`
  ).join('');
  return `<select name="${name}" class="input">\n<option value="">— None —</option>\n${opts}</select>`;
}

function roleSelect(id, selectedId = '') {
  const opts = roleOptions.map(r =>
    `<option value="${r.id}" ${r.id === selectedId ? 'selected' : ''} style="color:#${r.color ? r.color.toString(16).padStart(6,'0') : 'dbdee1'}">${r.name}</option>`
  ).join('');
  return `<select id="${id}" class="input">\n<option value="">— Select role —</option>\n${opts}</select>`;
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    if (tab === 'console-link') { window.location.href = '/console'; return; }
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`)?.classList.remove('hidden');
    tabLoaders[tab]?.();
  });
});

const tabLoaders = {
  logs:         loadLogs,
  features:     loadFeatures,
  filters:      loadFilters,
  words:        loadWords,
  roles:        loadRoles,
  warnings:     loadWarnings,
  alerts:       loadAlerts,
  moderation:   loadModeration,
  auditlog:     loadAuditLog,
  giveaways:    loadGiveaways,
  reactionroles:loadReactionRoles,
  sticky:       loadSticky,
};

// ---------------------------------------------------------------------------
// Initial load
// ---------------------------------------------------------------------------

async function init() {
  const [channels, roles] = await Promise.all([
    apiFetch(`${API}/channels`),
    apiFetch(`${API}/roles_list`),
  ]);
  channelOptions = channels;
  roleOptions = roles;
  loadLogs();
}

init();

// ---------------------------------------------------------------------------
// LOGS
// ---------------------------------------------------------------------------

const LOG_TYPES = ['general','spam','member','edit','delete','voice','roles','channels'];
const LOG_LABELS = {
  general: 'General / Commands', spam: 'Spam / Automod', member: 'Members (Join/Leave)',
  edit: 'Message Edits', delete: 'Message Deletes', voice: 'Voice Activity',
  roles: 'Role Changes', channels: 'Channel Updates',
};

async function loadLogs() {
  const data = await apiFetch(`${API}/logs`);
  const form = document.getElementById('logs-form');
  form.innerHTML = LOG_TYPES.map(t => `
    <div class="form-group">
      <label class="form-label">${LOG_LABELS[t]}</label>
      ${channelSelect(t, data[t] || '')}
    </div>
  `).join('');
}

async function saveLogs() {
  const form = document.getElementById('logs-form');
  const body = {};
  LOG_TYPES.forEach(t => {
    const sel = form.querySelector(`[name="${t}"]`);
    if (sel) body[t] = sel.value || null;
  });
  const res = await apiFetch(`${API}/logs`, { method: 'POST', body: JSON.stringify(body) });
  res.ok ? toast('Log channels saved.') : toast('Failed to save.', 'error');
}

// ---------------------------------------------------------------------------
// FEATURES
// ---------------------------------------------------------------------------

const FEATURE_LABELS = {
  spam_filter: 'Spam Filter', word_filter: 'Word Filter', image_block: 'Image & GIF Block',
  sticker_block: 'Sticker Block', external_emoji_block: 'External Emoji Block',
  logging: 'Logging', role_automation: 'Role Automation',
  message_management: 'Message Management', automod: 'Automod (Master)',
  dashboard: 'Dashboard', announcements: 'Announcements',
};

async function loadFeatures() {
  const data = await apiFetch(`${API}/features`);
  const list = document.getElementById('features-list');
  list.innerHTML = Object.entries(data).map(([name, enabled]) => `
    <div class="toggle-item">
      <div>
        <div class="toggle-label">${FEATURE_LABELS[name] || name}</div>
      </div>
      <label class="switch">
        <input type="checkbox" ${enabled ? 'checked' : ''} onchange="setFeature('${name}', this.checked)"/>
        <span class="slider"></span>
      </label>
    </div>
  `).join('');
}

async function setFeature(name, enabled) {
  const res = await apiFetch(`${API}/features/${name}`, {
    method: 'POST', body: JSON.stringify({ enabled }),
  });
  if (!res.ok) toast('Failed to update feature.', 'error');
}

// ---------------------------------------------------------------------------
// FILTERS
// ---------------------------------------------------------------------------

const FILTER_LABELS = {
  spam: 'Spam (links + mass mentions)', word: 'Word Filter',
  image: 'Image & GIF Block', sticker: 'Sticker Block',
  external_emoji: 'External Emoji', link: 'Link Filter',
};
const PUNISHMENTS = ['none', 'warn', 'mute', 'kick', 'ban'];

async function loadFilters() {
  const data = await apiFetch(`${API}/filters`);
  const list = document.getElementById('filters-list');
  list.innerHTML = Object.entries(data).map(([name, f]) => `
    <div class="filter-item">
      <div class="toggle-label">${FILTER_LABELS[name] || name}</div>
      <div class="filter-actions">
        <select class="input" onchange="setFilter('${name}', null, this.value)">
          ${PUNISHMENTS.map(p => `<option ${f.punishment === p ? 'selected' : ''}>${p}</option>`).join('')}
        </select>
        <label class="switch">
          <input type="checkbox" ${f.enabled ? 'checked' : ''} onchange="setFilter('${name}', this.checked, null)"/>
          <span class="slider"></span>
        </label>
        <button class="btn btn-sm btn-primary" onclick="saveFilter('${name}')">Save</button>
      </div>
    </div>
  `).join('');
}

const filterChanges = {};
function setFilter(name, enabled, punishment) {
  if (!filterChanges[name]) filterChanges[name] = {};
  if (enabled !== null) filterChanges[name].enabled = enabled;
  if (punishment !== null) filterChanges[name].punishment = punishment;
}

async function saveFilter(name) {
  const body = filterChanges[name] || {};
  const res = await apiFetch(`${API}/filters/${name}`, { method: 'POST', body: JSON.stringify(body) });
  res.ok ? toast(`Filter "${name}" saved.`) : toast('Failed to save.', 'error');
}

// ---------------------------------------------------------------------------
// WORDS
// ---------------------------------------------------------------------------

async function loadWords() {
  const words = await apiFetch(`${API}/words`);
  renderWords(words);
}

function renderWords(words) {
  const list = document.getElementById('words-list');
  list.innerHTML = words.map(w => `
    <span class="tag">
      ${w}
      <span class="tag-remove" onclick="removeWord('${w}')">×</span>
    </span>
  `).join('');
}

async function addWord() {
  const input = document.getElementById('word-input');
  const word = input.value.trim().toLowerCase();
  if (!word) return;
  const res = await apiFetch(`${API}/words`, { method: 'POST', body: JSON.stringify({ word }) });
  if (res.ok) { input.value = ''; loadWords(); }
  else toast('Failed to add word.', 'error');
}

document.getElementById('word-input')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') addWord();
});

async function removeWord(word) {
  const res = await apiFetch(`${API}/words/${encodeURIComponent(word)}`, { method: 'DELETE' });
  if (res.ok) loadWords();
  else toast('Failed to remove word.', 'error');
}

// ---------------------------------------------------------------------------
// ROLE RULES
// ---------------------------------------------------------------------------

async function loadRoles() {
  const [swaps, grants] = await Promise.all([
    apiFetch(`${API}/roleswap`),
    apiFetch(`${API}/rolegrant`),
  ]);

  const roleMap = Object.fromEntries(roleOptions.map(r => [r.id, r.name]));

  document.getElementById('swap-trigger').innerHTML = roleOptions.map(r => `<option value="${r.id}">${r.name}</option>`).join('');
  document.getElementById('swap-remove').innerHTML  = roleOptions.map(r => `<option value="${r.id}">${r.name}</option>`).join('');
  document.getElementById('grant-trigger').innerHTML = roleOptions.map(r => `<option value="${r.id}">${r.name}</option>`).join('');
  document.getElementById('grant-grant').innerHTML   = roleOptions.map(r => `<option value="${r.id}">${r.name}</option>`).join('');

  document.getElementById('swap-rules').innerHTML = swaps.length
    ? swaps.map(r => `
        <div class="toggle-item">
          <span><b>${roleMap[r.trigger_role_id] || r.trigger_role_id}</b> → remove <b>${roleMap[r.remove_role_id] || r.remove_role_id}</b>${r.note ? ` <span class="text-muted">(${r.note})</span>` : ''}</span>
          <button class="btn btn-sm btn-danger" onclick="deleteSwap(${r.id})">Remove</button>
        </div>`).join('')
    : '<p class="text-muted">No swap rules.</p>';

  document.getElementById('grant-rules').innerHTML = grants.length
    ? grants.map(r => `
        <div class="toggle-item">
          <span><b>${roleMap[r.trigger_role_id] || r.trigger_role_id}</b> → grant <b>${roleMap[r.grant_role_id] || r.grant_role_id}</b>${r.note ? ` <span class="text-muted">(${r.note})</span>` : ''}</span>
          <button class="btn btn-sm btn-danger" onclick="deleteGrant(${r.id})">Remove</button>
        </div>`).join('')
    : '<p class="text-muted">No grant rules.</p>';
}

async function addSwap() {
  const trigger = document.getElementById('swap-trigger').value;
  const remove  = document.getElementById('swap-remove').value;
  const note    = document.getElementById('swap-note').value;
  if (!trigger || !remove) return toast('Select both roles.', 'error');
  const res = await apiFetch(`${API}/roleswap`, { method: 'POST', body: JSON.stringify({ trigger_role_id: trigger, remove_role_id: remove, note }) });
  res.ok ? (toast('Swap rule added.'), loadRoles()) : toast('Failed.', 'error');
}

async function deleteSwap(id) {
  const res = await apiFetch(`${API}/roleswap/${id}`, { method: 'DELETE' });
  res.ok ? (toast('Rule removed.'), loadRoles()) : toast('Failed.', 'error');
}

async function addGrant() {
  const trigger = document.getElementById('grant-trigger').value;
  const grant   = document.getElementById('grant-grant').value;
  const note    = document.getElementById('grant-note').value;
  if (!trigger || !grant) return toast('Select both roles.', 'error');
  const res = await apiFetch(`${API}/rolegrant`, { method: 'POST', body: JSON.stringify({ trigger_role_id: trigger, grant_role_id: grant, note }) });
  res.ok ? (toast('Grant rule added.'), loadRoles()) : toast('Failed.', 'error');
}

async function deleteGrant(id) {
  const res = await apiFetch(`${API}/rolegrant/${id}`, { method: 'DELETE' });
  res.ok ? (toast('Rule removed.'), loadRoles()) : toast('Failed.', 'error');
}

// ---------------------------------------------------------------------------
// WARNINGS
// ---------------------------------------------------------------------------

async function loadWarnings() {
  const warnings = await apiFetch(`${API}/warnings`);
  const wrap = document.getElementById('warnings-table-wrap');
  if (!warnings.length) { wrap.innerHTML = '<p class="text-muted">No warnings.</p>'; return; }
  wrap.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>User</th><th>Moderator</th><th>Reason</th><th>Date</th><th></th></tr></thead>
        <tbody>
          ${warnings.map(w => `
            <tr>
              <td>${w.id}</td>
              <td>${w.user_name}</td>
              <td>${w.moderator_name}</td>
              <td>${w.reason || '—'}</td>
              <td>${(w.created_at || '').slice(0, 10)}</td>
              <td><button class="btn btn-sm btn-danger" onclick="deleteWarning(${w.id})">Remove</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

async function deleteWarning(id) {
  const res = await apiFetch(`${API}/warnings/${id}`, { method: 'DELETE' });
  res.ok ? (toast('Warning removed.'), loadWarnings()) : toast('Failed.', 'error');
}

// ---------------------------------------------------------------------------
// ALERTS
// ---------------------------------------------------------------------------

async function loadAlerts() {
  const config = await apiFetch(`${API}/config`);
  const form = document.getElementById('alerts-form');
  const guild = config.guild || {};
  form.innerHTML = `
    <div class="form-group" style="margin-bottom:.75rem">
      <label class="form-label">Alert Channel</label>
      ${channelSelect('alert_channel', guild.alert_channel_id || '')}
    </div>
    <div class="form-group" style="margin-bottom:.75rem">
      <label class="form-label">Announcement Channel</label>
      ${channelSelect('announcement_channel', guild.announcement_channel_id || '')}
    </div>`;
}

async function saveAlerts() {
  toast('Alert settings saved.');
}

// ---------------------------------------------------------------------------
// MODERATION TAB
// ---------------------------------------------------------------------------

async function loadModeration() {
  const settings = await apiFetch(`${API}/settings`);
  const input = document.getElementById('threshold-input');
  if (input) input.value = settings.auto_ban_threshold || 0;
}

async function doModAction() {
  const member   = document.getElementById('mod-member').value.trim();
  const action   = document.getElementById('mod-action').value;
  const reason   = document.getElementById('mod-reason').value.trim() || 'No reason provided';
  const duration = document.getElementById('mod-duration').value.trim() || '10m';
  const result   = document.getElementById('mod-result');

  if (!member) return toast('Enter a member username or ID.', 'error');

  result.innerHTML = '<div class="spinner" style="margin:.5rem 0;width:24px;height:24px;border-width:2px"></div>';
  const res = await apiFetch(`${API}/modaction`, {
    method: 'POST',
    body: JSON.stringify({ action, member, reason, duration }),
  });
  if (res.ok) {
    result.innerHTML = `<div class="alert alert-success">${res.message}</div>`;
  } else {
    result.innerHTML = `<div class="alert alert-error">${res.error}</div>`;
  }
}

async function saveThreshold() {
  const val = parseInt(document.getElementById('threshold-input').value) || 0;
  const res = await apiFetch(`${API}/settings`, { method: 'POST', body: JSON.stringify({ auto_ban_threshold: val }) });
  res.ok ? toast(`Auto-ban threshold set to ${val}.`) : toast('Failed.', 'error');
}

// ---------------------------------------------------------------------------
// AUDIT LOG TAB
// ---------------------------------------------------------------------------

async function loadAuditLog() {
  const actions = await apiFetch(`${API}/auditlog`);
  const wrap = document.getElementById('audit-table-wrap');
  if (!actions.length) { wrap.innerHTML = '<p class="text-muted">No mod actions recorded.</p>'; return; }
  wrap.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Action</th><th>Moderator</th><th>Target</th><th>Reason</th><th>Date</th></tr></thead>
        <tbody>
          ${actions.map(a => `
            <tr>
              <td><code>${a.action_type}</code></td>
              <td>${a.moderator_name}</td>
              <td>${a.target_name}</td>
              <td>${a.reason || '—'}</td>
              <td>${(a.created_at || '').slice(0,16)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------------------
// GIVEAWAYS TAB
// ---------------------------------------------------------------------------

async function loadGiveaways() {
  const giveaways = await apiFetch(`${API}/giveaways`);
  const list = document.getElementById('giveaways-list');
  if (!giveaways.length) { list.innerHTML = '<p class="text-muted">No active giveaways.</p>'; return; }
  list.innerHTML = giveaways.map(g => `
    <div class="toggle-item">
      <div>
        <div class="toggle-label">🎉 ${g.prize}</div>
        <div class="text-muted small">Winners: ${g.winners} · Ends: ${g.ends_at} · ID: ${g.id}</div>
      </div>
    </div>`).join('');
}

// ---------------------------------------------------------------------------
// REACTION ROLES TAB
// ---------------------------------------------------------------------------

async function loadReactionRoles() {
  const rrs = await apiFetch(`${API}/reactionroles`);
  const list = document.getElementById('rr-list');
  if (!rrs.length) { list.innerHTML = '<p class="text-muted">No reaction roles configured. Use <code>/reactionrole add</code> in Discord.</p>'; return; }
  list.innerHTML = rrs.map(r => `
    <div class="toggle-item">
      <div>
        <span class="toggle-label">${r.emoji} → ${r.role_name}</span>
        <div class="text-muted small">Message ID: ${r.message_id}</div>
      </div>
      <button class="btn btn-sm btn-danger" onclick="deleteRR(${r.message_id}, '${r.emoji}')">Remove</button>
    </div>`).join('');
}

async function deleteRR(messageId, emoji) {
  const res = await apiFetch(`${API}/reactionroles/${messageId}/${encodeURIComponent(emoji)}`, { method: 'DELETE' });
  res.ok ? (toast('Reaction role removed.'), loadReactionRoles()) : toast('Failed.', 'error');
}

// ---------------------------------------------------------------------------
// STICKY TAB
// ---------------------------------------------------------------------------

async function loadSticky() {
  const list = document.getElementById('sticky-list');
  list.innerHTML = '<p class="text-muted">Sticky messages are managed via <code>/sticky set</code> and <code>/sticky clear</code> in Discord. Check each channel to see active stickies.</p>';
}
