'use strict';
(() => {
const $ = id => document.getElementById(id);
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const today = () => { const d = new Date(), p = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
const cnDate = iso => { const [y, m, d] = String(iso).split('-'); return `${y}年${Number(m)}月${Number(d)}日`; };
const params = new URLSearchParams(location.search);
let day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('day') || '') ? params.get('day') : today();
const TABS = { analysis: ['01', '分析'], priorities: ['02', '优先级'], plan_review: ['03', '交易计划评审'] };
const EMPTY = { analysis: '还没有分析。点左下「让 Grok Bot 总结」生成。', priorities: '还没有优先级。点左下「让 Grok Bot 总结」生成。', plan_review: '点「让 Grok Bot 总结」生成计划评审' };
const state = { result: null, chat: [], updated: null, tab: 'analysis', waitChat: 0, waitSummary: null, poll: null, load: 0 };
const POLL_MS = 8000, POLL_MAX = 10 * 60 * 1000;

async function req(path, options = {}) {
  const ctrl = new AbortController(); const timer = setTimeout(() => ctrl.abort(), options.timeout || 30000);
  try {
    const res = await fetch(path, { ...options, signal: ctrl.signal, headers: { 'Content-Type': 'application/json' } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求没成功（${res.status}）`);
    return data;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('等了太久没回来，稍后再试。');
    if (e instanceof TypeError) throw new Error('连不上本地服务，确认 app.py 还在跑。');
    throw e;
  } finally { clearTimeout(timer); }
}
const dayApi = s => `/api/days/${encodeURIComponent(day)}${s}`;
function flash(msg = '', error = false) { const el = $('sync'); el.textContent = msg; el.classList.toggle('error', error); }
function timeLabel(iso) { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; }

// ---- 迷你 markdown：先转义再加格式，安全 ----
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n'); const out = []; let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (/^```/.test(l)) { const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`); continue; }
    let m = l.match(/^(#{1,6})\s+(.*)$/);
    if (m) { const n = Math.min(m[1].length + 1, 5); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(l)) { out.push('<hr>'); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(`<div class="tbl"><table><thead><tr>${head.map(h => `<th>${inline(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if (/^>\s?/.test(l)) { const buf = []; while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, '')); out.push(`<blockquote>${md(buf.join('\n'))}</blockquote>`); continue; }
    if (/^\s*([-*+•]|\d+[.)、])\s+/.test(l)) {
      const ordered = /^\s*\d/.test(l); const items = [];
      while (i < lines.length && (/^\s*([-*+•]|\d+[.)、])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const t = lines[i++];
        if (/^\s*([-*+•]|\d+[.)、])\s+/.test(t) && !/^\s{2,}/.test(t)) items.push(t.replace(/^\s*([-*+•]|\d+[.)、])\s+/, ''));
        else items[items.length - 1] += '\n' + t.trim();
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.map(t => { const [first, ...rest] = t.split('\n'); return `<li>${inline(first)}${rest.length ? md(rest.map(x => x.replace(/^([-*+•]|\d+[.)、])\s+/, '- ')).join('\n')) : ''}</li>`; }).join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\s*([-*+•]|\d+[.)、])\s+|\s*\|)/.test(lines[i])) buf.push(lines[i++]);
    if (!buf.length) buf.push(lines[i++]);
    out.push(`<p>${buf.map(inline).join('<br>')}</p>`);
  }
  return out.join('');
}

function renderNav() {
  document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === state.tab)));
  const r = state.result || {};
  $('meta-time').textContent = timeLabel(r.created_at || state.updated) || '—';
  $('meta-model').textContent = r.model || '—';
}
function renderDoc() {
  const [k, title] = TABS[state.tab]; const r = state.result || {};
  $('doc-kicker').textContent = `${k} · ${cnDate(day)}`; $('doc-title').textContent = title;
  const text = String(r[state.tab] || '').trim();
  let html = text ? md(text) : `<div class="empty"><p>${esc(EMPTY[state.tab])}</p></div>`;
  if (state.tab === 'analysis' && r.thinking) html += `<details class="think"><summary>思考过程（点开看）</summary><pre>${esc(r.thinking)}</pre></details>`;
  if (state.tab === 'analysis' && r.note) html = `<p class="note">${esc(r.note)}</p>` + html;
  if (state.waitSummary) html = `<div class="pending-bar"><canvas class="helix-mini" data-helix="mini"></canvas>Grok Bot 正在总结，完成后自动刷新…</div>` + html;
  $('doc').innerHTML = html; $('doc').parentElement.scrollTop = 0;
}
function msgHtml(m) {
  if (m.role === 'user') return `<div class="msg user"><span class="who">我${m.at ? ' · ' + esc(timeLabel(m.at)) : ''}</span><div class="bubble">${esc(m.content)}</div></div>`;
  if (m.role === 'error') return `<div class="msg fail"><span class="who">没发成功</span><div class="bubble">${esc(m.content)}</div></div>`;
  return `<div class="msg assistant"><span class="who">${esc(m.model || 'Grok Bot')}${m.at ? ' · ' + esc(timeLabel(m.at)) : ''}</span><div class="bubble md">${md(m.content)}</div>${m.thinking ? `<details class="think"><summary>思考过程</summary><pre>${esc(m.thinking)}</pre></details>` : ''}</div>`;
}
function renderChat() {
  const box = $('chat');
  const body = state.chat.length ? state.chat.map(msgHtml).join('') : `<p class="chat-empty">对中间的计划有疑问，或手上有新消息，直接在下面说。<br>比如：「P1 如果明天高开 3% 还追吗？」</p>`;
  box.innerHTML = body + (state.waitChat ? `<div class="msg assistant pending"><span class="who">Grok Bot</span><div class="bubble"><canvas class="helix-mini" data-helix="mini"></canvas>Grok Bot 正在思考… <span id="chat-sec"></span></div></div>` : '');
  box.scrollTop = box.scrollHeight;
  $('btn-send').disabled = !!state.waitChat; $('btn-clear-chat').disabled = !!state.waitChat || !state.chat.length;
}
const assistantCount = chat => chat.filter(m => m.role === 'assistant').length;

function apply(data) {
  state.result = data.result || null; state.updated = data.updated_at || null;
  state.chat = Array.isArray(data.chat) ? data.chat : [];
}
async function load() {
  const token = ++state.load;
  $('day').value = day; $('day-name').textContent = cnDate(day); document.title = `交易笔记 · ${cnDate(day)} 综合`;
  flash('正在翻开…');
  try {
    const data = await req(dayApi('/synthesis'), { timeout: 20000 });
    if (token !== state.load) return;
    apply(data); flash(); renderNav(); renderDoc(); renderChat();
  } catch (e) { if (token === state.load) flash(e.message, true); }
}

// ---- 轮询：等新的 assistant 消息 或 result 更新 ----
function startPoll() {
  if (state.poll) return;
  const started = Date.now(); const forDay = day;
  const tick = setInterval(() => { const el = $('chat-sec'); if (el) el.textContent = `${Math.round((Date.now() - started) / 1000)} 秒`; }, 1000);
  const stop = msg => { clearInterval(tick); clearTimeout(state.poll); state.poll = null; if (msg) flash(msg, true); };
  const step = async () => {
    if (forDay !== day) return stop();
    if (Date.now() - started > POLL_MAX) { state.waitChat = 0; state.waitSummary = null; renderChat(); renderDoc(); return stop('等了 10 分钟还没回来，稍后刷新看看。'); }
    try {
      const data = await req(dayApi('/synthesis'), { timeout: 20000 });
      if (forDay !== day) return stop();
      const chat = Array.isArray(data.chat) ? data.chat : [];
      const stamp = (data.result && data.result.created_at) || '';
      let changed = false;
      if (state.waitChat && assistantCount(chat) >= state.waitChat) { state.waitChat = 0; changed = true; flash('Grok Bot 回复了。'); }
      if (state.waitSummary && stamp && stamp !== state.waitSummary.stamp) { state.waitSummary = null; changed = true; flash('总结已更新。'); }
      if (changed || chat.length !== state.chat.length) { apply(data); renderNav(); renderDoc(); renderChat(); }
    } catch { /* 下一轮再试 */ }
    if (state.waitChat || state.waitSummary) state.poll = setTimeout(step, POLL_MS); else stop();
  };
  state.poll = setTimeout(step, POLL_MS);
}

async function send(event) {
  event?.preventDefault();
  const input = $('ask-input'); const text = input.value.trim();
  if (!text || state.waitChat) return;
  const forDay = day; const before = assistantCount(state.chat);
  state.chat = state.chat.filter(m => m.role !== 'error'); state.chat.push({ role: 'user', content: text, at: new Date().toISOString() });
  input.value = ''; state.waitChat = before + 1; renderChat();
  try {
    const data = await req(dayApi('/synthesis/chat'), { method: 'POST', body: JSON.stringify({ message: text }) });
    if (forDay !== day) return;
    if (Array.isArray(data.chat)) state.chat = data.chat;
    renderChat(); startPoll();
  } catch (e) {
    if (forDay !== day) return;
    state.waitChat = 0; state.chat.push({ role: 'error', content: `${e.message}　（这句已存进对话，但没能通知 Grok Bot）` });
    renderChat();
  }
}
async function grok() {
  const btn = $('btn-grok'); btn.disabled = true; flash('正在发送给 Grok Bot…');
  try {
    await req(dayApi('/grokbot'), { method: 'POST', body: JSON.stringify({ type: 'summary' }) });
    state.waitSummary = { stamp: (state.result && state.result.created_at) || '' };
    flash('已发送，Grok Bot 正在总结（约几分钟）'); renderDoc(); startPoll();
  } catch (e) { flash(e.message, true); }
  finally { btn.disabled = false; }
}
async function clearChat() {
  if (!state.chat.length || state.waitChat || !confirm('清空这一天的对话？中间的内容会保留。')) return;
  try { const data = await req(dayApi('/synthesis/chat'), { method: 'DELETE' }); state.chat = data.chat || []; renderChat(); }
  catch (e) { flash(e.message, true); }
}

document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => { state.tab = b.dataset.tab; renderNav(); renderDoc(); }));
document.addEventListener('keydown', e => { if (e.altKey && ['1', '2', '3'].includes(e.key)) { state.tab = Object.keys(TABS)[+e.key - 1]; renderNav(); renderDoc(); } });
$('ask').addEventListener('submit', send);
$('ask-input').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send(e); });
$('btn-clear-chat').addEventListener('click', clearChat);
$('btn-grok').addEventListener('click', grok);
$('day').addEventListener('change', () => {
  const v = $('day').value; if (!/^\d{4}-\d{2}-\d{2}$/.test(v) || v === day) return;
  day = v; state.waitChat = 0; state.waitSummary = null; if (state.poll) { clearTimeout(state.poll); state.poll = null; }
  history.replaceState(null, '', `/synthesis?day=${day}`); load();
});
load();
})();
