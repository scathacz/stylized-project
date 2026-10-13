'use strict';
/* 今日页：以“当天”为中心 —— 记笔记 → 让 Grok Bot 总结 → 读综合 → 跟踪交易 → 回看历史。 */
const $ = id => document.getElementById(id);
const array = v => Array.isArray(v) ? v : [];
const text = v => v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
const esc = v => text(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const dayUrl = day => '/api/days/' + encodeURIComponent(day);
const layers = { macro: '宏观', industry: '行业', stock: '个股' };
const WEEK = '日一二三四五六';
const PIN = { tradingplan: { mark: '我的计划', kind: 'plan' }, thought: { mark: '想法', kind: 'thought' } };
const PENDING_MIN = 20;
const state = { date: null, days: [], notes: null, packed: null, synth: null, trades: [], roster: [], providers: [], busy: false, filter: 'all', query: '', expanding: null, poll: null, load: 0, synthDays: {} };

function message(id, value = '', error = false) { const el = $(id); if (!el) return; el.textContent = value; el.classList.toggle('error', error); }
async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : `请求未完成（${res.status}）`); e.status = res.status; throw e; }
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
function safeUrl(v) { try { const u = new URL(text(v)); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
function link(v, label) { const url = safeUrl(v); return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label || url)} ↗</a>` : ''; }
function hm(iso) { return iso ? text(iso).slice(11, 16) : ''; }
function md(iso) { return iso ? text(iso).slice(5, 10).replace('-', '/') : ''; }
function stamp(iso) { if (!iso) return ''; return text(iso).slice(0, 10) === localDate() ? hm(iso) : `${md(iso)} ${hm(iso)}`; }
function plain(s) { return text(s).replace(/\*\*|__|`/g, '').replace(/^[#>\-\s*]+/gm, '').trim(); }
function clip(s, n) { s = plain(s); return s.length > n ? s.slice(0, n) + '…' : s; }

/* ---------- 日期 ---------- */
function dayTitle(date) {
  const [y, m, d] = date.split('-').map(Number); const w = new Date(y, m - 1, d).getDay();
  const EN = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  return `<span class="d-num">${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')}</span><span class="d-meta"><b>周${WEEK[w]}</b><span>${EN[w]} · ${y}</span></span>`;
}
function setDay(date, push = true) {
  state.date = date; ++state.load;
  if (push) history.replaceState(null, '', date === localDate() ? '/' : `/?day=${date}`);
  document.querySelectorAll('[data-nav="synthesis"],[data-nav="today"]').forEach(a => { const base = a.dataset.nav === 'synthesis' ? '/synthesis' : '/'; a.href = `${base}?day=${date}`; });
  $('day-title').innerHTML = dayTitle(date); $('day-kicker').textContent = date === localDate() ? 'Today — 今日' : 'Archive — 回看';
  $('day-pick').value = date; $('day-today').hidden = date === localDate();
  $('flow-3-link').href = `/synthesis?day=${date}`;
  const i = state.days.findIndex(d => d.date === date);
  const older = state.days.find(d => d.date < date), newer = [...state.days].reverse().find(d => d.date > date);
  $('day-prev').disabled = !older; $('day-next').disabled = !newer && date >= localDate();
  void i; restoreDraft(); renderHistory(); loadDay();
}
function step(dir) {
  const date = state.date;
  if (dir < 0) { const d = state.days.find(x => x.date < date); if (d) setDay(d.date); }
  else { const d = [...state.days].reverse().find(x => x.date > date); setDay(d ? d.date : localDate()); }
}

/* ---------- 载入当天 ---------- */
async function loadDay() {
  const token = state.load, date = state.date;
  $('note-groups').innerHTML = '<span class="skeleton w40"></span><span class="skeleton"></span><span class="skeleton w80"></span><span class="skeleton w60"></span>';
  const [notes, synth, packed] = await Promise.allSettled([api('/api/notes/' + date), api(dayUrl(date) + '/synthesis'), api(dayUrl(date))]);
  if (token !== state.load) return;
  state.notes = notes.status === 'fulfilled' ? notes.value : { items: [] };
  state.synth = synth.status === 'fulfilled' ? synth.value : null;
  state.packed = packed.status === 'fulfilled' ? packed.value : null;
  if (notes.status === 'rejected') message('day-msg', '笔记未能载入：' + notes.reason.message, true); else message('day-msg');
  renderNotes(); renderSynth(); renderFlow(); renderAdvanced();
}

/* ---------- 笔记 ---------- */
const pinOf = name => PIN[text(name).toLowerCase()] || null;
function renderNotes() {
  const items = array(state.notes?.items);
  $('notes-count').textContent = items.length ? `${items.length} 条` : '';
  if (!items.length) { $('note-groups').innerHTML = `<div class="empty">这一天还没有笔记。<br>把群里、研报、自己的计划整段贴进上面，按作者自动分组。</div>`; return; }
  const groups = new Map();
  for (const it of items) { const k = it.source || '随手记'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); }
  const order = [...groups.keys()].sort((a, b) => (pinOf(b)?.kind === 'plan') - (pinOf(a)?.kind === 'plan'));
  $('note-groups').innerHTML = order.map(name => {
    const pin = pinOf(name), list = groups.get(name);
    return `<section class="group${pin ? ' ' + pin.kind : ''}"><div class="group-head"><b>${esc(name)}</b>${pin ? `<span class="tag-mine">${esc(pin.mark)}</span>` : ''}<span>${list.length} 条</span></div>${list.map(it => {
      const t = text(it.text), long = t.length > 280;
      return `<div class="note-item${it.important ? ' imp' : ''}" data-id="${esc(it.id)}"><span class="t">${esc(long ? t.slice(0, 280) + '…' : plain(t) === t ? t : t.replace(/\*\*/g, ''))}</span>${long ? '<button type="button" class="more" data-more>展开</button>' : ''}<button type="button" class="del" data-del="${esc(it.id)}" title="删除这条" aria-label="删除这条">×</button></div>`;
    }).join('')}</section>`;
  }).join('');
}
$('note-groups').addEventListener('click', async e => {
  const more = e.target.closest('[data-more]');
  if (more) { const box = more.closest('.note-item'); const it = array(state.notes?.items).find(x => text(x.id) === box.dataset.id); box.querySelector('.t').textContent = text(it?.text).replace(/\*\*/g, ''); more.remove(); return; }
  const del = e.target.closest('[data-del]'); if (!del || state.busy) return;
  if (!confirm('删除这条笔记？')) return;
  try { await api('/api/notes/items/' + encodeURIComponent(del.dataset.del), { method: 'DELETE' }); await refreshDays(); loadDay(); }
  catch (err) { message('save-msg', err.message, true); }
});
const DRAFT = () => 'desk.home.draft.' + state.date;
function restoreDraft() { try { $('raw').value = localStorage.getItem(DRAFT()) || ''; } catch { $('raw').value = ''; } $('draft-state').textContent = $('raw').value ? '已恢复未保存的草稿' : '草稿自动保存在此浏览器'; }
$('raw').addEventListener('input', () => { try { localStorage.setItem(DRAFT(), $('raw').value); } catch { /* ignore */ } $('draft-state').textContent = `${$('raw').value.length} 字 · 草稿已暂存`; });
$('raw').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); saveNotes(); } });
function renderAuthors() {
  const names = state.roster.length ? state.roster : ['TradingPlan', 'Fuelish', '狼大', 'VITO', '策略', '雨神', '猫'];
  $('authors').innerHTML = '<span class="muted small">插入作者：</span>' + names.map(n => `<button type="button" class="${pinOf(n)?.kind === 'plan' ? 'plan' : ''}" data-author="${esc(n)}">${esc(n)}</button>`).join('');
}
$('authors').addEventListener('click', e => {
  const b = e.target.closest('[data-author]'); if (!b) return;
  const ta = $('raw'); const pos = ta.selectionStart ?? ta.value.length; const before = ta.value.slice(0, pos);
  const ins = (before && !before.endsWith('\n') ? '\n' : '') + (before.trim() ? '\n' : '') + b.dataset.author + '\n';
  ta.setRangeText(ins, pos, ta.selectionEnd ?? pos, 'end'); ta.focus(); ta.dispatchEvent(new Event('input'));
});
async function saveNotes() {
  const raw = $('raw').value; if (!raw.trim()) { message('save-msg', '先写下或粘贴一段笔记。', true); $('raw').focus(); return; }
  if (state.busy) return; state.busy = true; $('btn-save').disabled = true; message('save-msg', '正在保存…');
  try {
    const r = await post(`/api/notes/${state.date}/items`, { text: raw, source: '' });
    $('raw').value = ''; try { localStorage.removeItem(DRAFT()); } catch { /* ignore */ }
    $('draft-state').textContent = '草稿自动保存在此浏览器';
    const n = array(r.items).length; message('save-msg', `已保存。当天共 ${n || '—'} 条。${state.synth?.result ? '综合判断是在此之前生成的，可以重新总结。' : '下一步：让 Grok Bot 总结。'}`);
    await refreshDays(); await loadDay();
  } catch (e) { message('save-msg', '未能保存：' + e.message + '。草稿已保留。', true); }
  finally { state.busy = false; $('btn-save').disabled = false; }
}
$('btn-save').addEventListener('click', saveNotes);

/* ---------- Grok Bot 状态 ---------- */
const PKEY = d => 'desk.grok.pending.' + d;
function pendingAt(d) { try { const t = Number(localStorage.getItem(PKEY(d)) || 0); return t && Date.now() - t < PENDING_MIN * 60000 ? t : 0; } catch { return 0; } }
function grokStatus() {
  const r = state.synth?.result; const created = r?.created_at ? Date.parse(r.created_at) : 0; const p = pendingAt(state.date);
  if (p && (!created || created < p)) return { k: 'busy', label: '分析中', time: `已发送 ${new Date(p).toTimeString().slice(0, 5)}，通常需几分钟` };
  if (r) {
    const notesAt = state.days.find(d => d.date === state.date)?.updated_at; const stale = notesAt && created && Date.parse(notesAt) > created + 60000;
    return { k: stale ? 'warn' : 'ok', label: stale ? '已完成 · 笔记有更新' : '已完成', time: stamp(r.created_at) };
  }
  return { k: '', label: '未总结', time: '' };
}
function renderSynth() {
  const st = grokStatus();
  $('grok-chip').className = 'chip ' + st.k; $('grok-state').textContent = st.label; $('grok-time').textContent = st.time;
  const btn = $('btn-grok'); btn.disabled = st.k === 'busy';
  btn.innerHTML = st.k === 'busy' ? '分析中…' : st.label === '未总结' ? '让 Grok Bot 总结' : '重新总结';
  window.Helix?.setBusy(st.k === 'busy');   clearTimeout(state.poll); if (st.k === 'busy') state.poll = setTimeout(pollSynth, 15000);
  const r = state.synth?.result; const href = `/synthesis?day=${state.date}`;
  $('sx-model').textContent = r ? `${r.model || '—'} · ${stamp(r.created_at)}` : '';
  if (!r) { $('sx-body').innerHTML = `<p class="muted">${st.k === 'busy' ? 'Grok Bot 正在写分析、优先级和计划评审，完成后这里自动更新。' : array(state.notes?.items).length ? '笔记已就绪。点右上角「让 Grok Bot 总结」，几分钟后在这里看结论。' : '先记笔记，再让 Grok Bot 总结。'}</p>`; return; }
  const a = text(r.analysis); const sec = a.match(/##\s*综合判断\s*\n+([\s\S]*?)(\n##\s|$)/);
  const head = clip((sec ? sec[1] : a).split(/\n\s*\n/)[0], 150);
  const p1 = text(r.priorities).match(/^#{1,4}\s*(P1[^\n]*)/m) || text(r.priorities).match(/^\s*(?:1\.|-)\s*([^\n]+)/m);
  const pr = text(r.plan_review).match(/总体结论[:：]\s*([^\n]+)/);
  const talks = array(state.synth.chat).filter(m => m.role === 'assistant').length;
  $('sx-body').innerHTML = `<p class="sx-head">${esc(head || '（无综合判断段落）')}</p>${p1 ? `<p class="sx-p"><b>优先级 · P1</b>${esc(clip(p1[1], 90))}</p>` : ''}${pr ? `<p class="sx-p"><b>计划评审</b>${esc(clip(pr[1], 90))}</p>` : ''}<div class="sx-foot"><span class="muted small">${talks ? `已追问 ${talks} 轮` : ''}</span><a class="btn primary" href="${href}">阅读并追问</a></div>`;
}
async function pollSynth() {
  const date = state.date;
  try { const s = await api(dayUrl(date) + '/synthesis'); if (date !== state.date) return; state.synth = s; } catch { /* keep polling */ }
  renderSynth(); renderFlow(); if (grokStatus().k !== 'busy') { state.synthDays[date] = !!state.synth?.result; renderHistory(); }
}
async function askGrok() {
  const date = state.date; if (!array(state.notes?.items).length) { message('grok-msg', '这一天还没有笔记，先记几条再总结。', true); return; }
  if (state.synth?.result && !confirm('已有一份综合判断。重新总结会覆盖它（对话保留），继续？')) return;
  $('btn-grok').disabled = true; message('grok-msg', '正在发送…');
  try {
    await api(dayUrl(date) + '/grokbot', { method: 'POST', body: JSON.stringify({ type: 'summary' }) });
    try { localStorage.setItem(PKEY(date), String(Date.now())); } catch { /* ignore */ }
    message('grok-msg', '已发送。Grok Bot 正在分析，完成后本页自动更新。');
  } catch (e) { message('grok-msg', e.message, true); }
  renderSynth(); renderFlow();
}
$('btn-grok').addEventListener('click', askGrok);

/* ---------- 流程条 ---------- */
function renderFlow() {
  const n = array(state.notes?.items).length; const st = grokStatus(); const open = state.trades.length;
  const set = (i, cls, sub) => { $('flow-' + i).className = cls; $(`flow-${i}-sub`).textContent = sub; };
  set(1, n ? 'done' : 'now', n ? `${n} 条已记录` : '粘贴当天笔记');
  set(2, st.k === 'ok' || st.k === 'warn' ? 'done' : n ? 'now' : '', st.label + (st.time && st.k !== 'busy' ? ' · ' + st.time : ''));
  set(3, st.k === 'ok' || st.k === 'warn' ? 'now' : '', state.synth?.result ? '分析 · 优先级 · 计划评审' : '等待总结');
  set(4, '', open ? `${open} 笔进行中` : '暂无进行中');
}

/* ---------- 交易 / 历史 ---------- */
async function loadTrades() {
  try { state.trades = array((await api('/api/trades?status=open')).items); } catch { state.trades = []; }
  $('tr-count').textContent = state.trades.length ? `${state.trades.length}` : '';
  $('tr-list').innerHTML = state.trades.map(t => `<li><a href="/trades?id=${encodeURIComponent(t.id)}"><span class="trade-title">${esc(t.title)}</span><small>${array(t.entries).length ? array(t.entries).length + ' 条跟踪 · ' : ''}${md(t.last_activity || t.created_at)}</small></a></li>`).join('') || '<li class="muted small">没有进行中的交易。TradingPlan 里的计划会自动导入。</li>';
  renderFlow();
}
async function refreshDays() { state.days = array((await api('/api/days')).days); renderHistory(); }
async function loadSynthFlags() {
  const recent = state.days.slice(0, 20);
  await Promise.all(recent.map(async d => { try { state.synthDays[d.date] = !!(await api(dayUrl(d.date) + '/synthesis')).result; } catch { /* ignore */ } }));
  renderHistory();
}
function renderHistory() {
  const q = $('history-search').value.trim().toLowerCase();
  $('hi-count').textContent = `${state.days.length} 日`;
  const days = state.days.filter(d => !q || d.date.includes(q));
  $('day-list').innerHTML = days.map(d => `<li><button type="button" data-day="${esc(d.date)}"${d.date === state.date ? ' aria-current="date"' : ''}><span class="hd">${esc(d.date.slice(5).replace('-', ' / '))} <span class="muted small">周${WEEK[new Date(d.date + 'T00:00').getDay()]}</span></span><span class="st"><small>${esc(d.n ?? 0)} 条</small>${state.synthDays[d.date] ? '<span class="chip ok">已总结</span>' : state.synthDays[d.date] === false ? '<span class="chip">未总结</span>' : ''}</span></button></li>`).join('') || (q ? '' : '<li class="muted small">还没有记录。</li>');
}
let searchTimer;
$('history-search').addEventListener('input', () => {
  renderHistory(); clearTimeout(searchTimer); const q = $('history-search').value.trim(); if (!q) return;
  searchTimer = setTimeout(async () => {
    try {
      const items = array((await api('/api/search?q=' + encodeURIComponent(q))).results);
      if ($('history-search').value.trim() !== q) return;
      $('day-list').insertAdjacentHTML('beforeend', items.slice(0, 20).map(it => `<li><button type="button" data-day="${esc(it.date)}"><span><small>${esc(it.date)} · ${esc(it.source || '笔记')}</small><span class="excerpt">${esc(text(it.text).slice(0, 60))}</span></span></button></li>`).join('') || (!$('day-list').children.length ? '<li class="muted small">没有匹配。</li>' : ''));
    } catch { /* 正文搜索失败不影响日期筛选 */ }
  }, 250);
});
$('day-list').addEventListener('click', e => { const b = e.target.closest('[data-day]'); if (b) { setDay(b.dataset.day); window.scrollTo({ top: 0, behavior: 'smooth' }); } });
$('day-prev').addEventListener('click', () => step(-1));
$('day-next').addEventListener('click', () => step(1));
$('day-today').addEventListener('click', () => setDay(localDate()));
$('day-pick').addEventListener('change', () => { if (/^\d{4}-\d{2}-\d{2}$/.test($('day-pick').value)) setDay($('day-pick').value); });

/* ---------- 高级：片段 / 旧综合 / 智能导入 / 归档 ---------- */
const stanceLabels = { hold: '持有 / 观望', watch: '关注', add: '增持', reduce: '减持', short: '做空', switch: '调仓', working: '研究中', buy: '买入', sell: '卖出', neutral: '中性', bullish: '偏多', bearish: '偏空' };
function moodBadge(m) { return `<span class="badge ${m === '偏多' ? 'up' : m === '偏空' ? 'down' : ''}">${esc(m || '未判断')}</span>`; }
function options(values, chosen) { return Object.entries(values).map(([v, l]) => `<option value="${esc(v)}"${v === chosen ? ' selected' : ''}>${esc(l)}</option>`).join(''); }
function snippetHtml(s) {
  const ex = array(s.expansions); const vp = s.extras?.viewpoint; const pin = pinOf(s.source);
  return `<article class="slip${pin ? ' pin-' + pin.kind : ''}" id="snippet-${esc(s.id)}" tabindex="-1"><div class="slip-meta"><span>${esc(layers[s.layer] || s.layer || '未分类')} / ${esc(s.source || '随手记')}</span><span>#${esc(s.id)}</span></div>${vp ? `<h4>${esc(vp)}</h4>` : ''}<p class="slip-body">${esc(clip(s.text, vp ? 160 : 300))}</p><div class="tags">${moodBadge(s.mood)}${[...array(s.tickers), ...array(s.industries)].slice(0, 8).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div><details><summary>原文与证据 · ${ex.length} 条补充</summary><pre>${esc(s.text)}</pre>${ex.map(e => `<section><p>${esc(state.providers.find(p => p.id === e.provider)?.label || e.provider)} · #${esc(e.id)}</p><pre>${esc(e.result)}</pre>${array(e.meta?.urls).map(u => typeof u === 'string' ? link(u) : link(u?.url, u?.title)).join(' ')}</section>`).join('')}</details><footer><span class="muted small">${esc(stanceLabels[s.stance] || s.stance || '观望')}</span><button type="button" data-expand="${esc(s.id)}">补充证据 ↗</button></footer><details><summary>校正分类与情绪</summary><form class="edit-grid" data-edit="${esc(s.id)}"><select name="layer">${options(layers, s.layer)}</select><select name="mood">${options({ '偏多': '偏多', '中性': '中性', '偏空': '偏空', '分歧': '分歧' }, s.mood)}</select><button type="submit">保存</button></form><p class="feedback" role="status"></p></details></article>`;
}
function renderSnippets() {
  const all = array(state.packed?.snippets); const q = state.query.toLowerCase();
  const items = all.filter(s => (state.filter === 'all' || s.layer === state.filter || array(s.layers).includes(state.filter)) && (!q || `${s.text} ${s.source} ${array(s.tickers).join(' ')} ${array(s.industries).join(' ')}`.toLowerCase().includes(q)));
  $('snippet-list').innerHTML = items.map(snippetHtml).join('') || '<p class="muted small">没有片段。</p>';
  $('visible-count').textContent = `${items.length} / ${all.length}`;
  document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b.dataset.filter === state.filter));
}
function renderAdvanced() {
  const p = state.packed; renderSnippets();
  const c = p?.conclusion || {}; const legacy = [c.summary, c.macro, c.industry, c.stock].filter(Boolean).map(plain).join('\n\n');
  $('legacy-conclusion').textContent = legacy ? clip(legacy, 600) : '这一天没有旧版综合结论。';
  $('day-raw').textContent = p?.raw || '暂无原始记录';
  $('archive-path').innerHTML = p?.archive_path ? `本地留存：${esc(p.archive_path)} · <a href="${dayUrl(p.date)}/markdown" download>下载 Markdown ↓</a>` : (p?.notion_url ? link(p.notion_url, '查看 Notion 归档') : '');
  document.querySelectorAll('[data-archive]').forEach(b => { b.disabled = !p || state.busy; });
}
$('filters').addEventListener('click', e => { const b = e.target.closest('[data-filter]'); if (b) { state.filter = b.dataset.filter; renderSnippets(); } });
$('snippet-search').addEventListener('input', e => { state.query = e.target.value; renderSnippets(); });
$('snippet-list').addEventListener('click', e => { const b = e.target.closest('[data-expand]'); if (b) openExpand(b.dataset.expand); });
$('snippet-list').addEventListener('submit', async e => {
  const form = e.target.closest('[data-edit]'); if (!form) return; e.preventDefault(); if (state.busy) return;
  const status = form.parentElement.querySelector('.feedback'); const v = new FormData(form); state.busy = true; status.textContent = '正在保存…';
  try { state.packed = await api('/api/snippets/' + encodeURIComponent(form.dataset.edit), { method: 'PATCH', body: JSON.stringify({ layer: v.get('layer'), mood: v.get('mood') }) }); renderAdvanced(); }
  catch (err) { status.textContent = err.message; status.classList.add('error'); }
  finally { state.busy = false; }
});
document.querySelector('.adv-body').addEventListener('click', async e => {
  const b = e.target.closest('[data-archive]'); if (!b || !state.packed || state.busy) return;
  const kind = b.dataset.archive; const date = state.date; state.busy = true; renderAdvanced();
  const out = kind === 'conclude' ? 'day-msg' : 'archive-msg';
  message(out, kind === 'conclude' ? '正在用 DeepSeek 重新综合…' : kind === 'notion' ? '正在归档至本地与 Notion…' : '正在保存本地 Markdown…');
  try {
    const r = await post(dayUrl(date) + (kind === 'conclude' ? '/conclude' : '/archive'), kind === 'conclude' ? { use_llm: true } : { to_notion: kind === 'notion' });
    if (date === state.date) { state.packed = kind === 'conclude' ? r : (r.day || state.packed); }
    message(out, kind === 'conclude' ? '旧版综合已更新。' : `已保存本地 Markdown${r.archive_path ? '：' + r.archive_path : '。'}${r.notion_url ? '\nNotion 归档成功。' : ''}${r.notion_error ? '\nNotion 未能写入：' + r.notion_error : ''}`, !!r.notion_error);
  } catch (err) { message(out, err.message, true); }
  finally { state.busy = false; renderAdvanced(); }
});
$('btn-ingest').addEventListener('click', async () => {
  const raw = $('ingest-raw').value; if (!raw.trim()) { message('ingest-msg', '先粘贴一段文本。', true); return; }
  if (state.busy) return; state.busy = true; $('btn-ingest').disabled = true; message('ingest-msg', $('smart-toggle').checked ? '正在识别日期、分类与情绪…' : '正在按规则整理…');
  try {
    const r = await post('/api/ingest', { text: raw, fallback_date: $('date-override').value || state.date || localDate(), use_llm: $('smart-toggle').checked });
    $('ingest-raw').value = ''; const dates = array(r.dates);
    message('ingest-msg', `已收下 ${dates.join('、') || '本次'} 的记录。${array(r.warnings).map(text).join('\n')}`);
    await refreshDays(); if (dates.length) setDay(dates[dates.length - 1]); else loadDay();
  } catch (e) { message('ingest-msg', '未能完成整理：' + e.message, true); }
  finally { state.busy = false; $('btn-ingest').disabled = false; }
});
function providerChanged() { const p = state.providers.find(x => x.id === $('dlg-provider').value); $('provider-hint').textContent = p?.hint || ''; $('paste-field').hidden = $('dlg-provider').value !== 'paste'; }
function openExpand(id) {
  const s = array(state.packed?.snippets).find(x => text(x.id) === text(id)); if (!s || state.busy) return;
  state.expanding = { id: s.id, date: state.date }; $('dlg-quote').textContent = `${s.source || '笔记'} · ${s.text}`; $('dlg-query').value = ''; $('dlg-paste').value = ''; message('dlg-msg'); providerChanged(); $('expand-dlg').showModal();
}
$('dlg-provider').addEventListener('change', providerChanged);
$('dlg-close').addEventListener('click', () => $('expand-dlg').close());
$('expand-form').addEventListener('submit', async e => {
  e.preventDefault(); if (!state.expanding || state.busy) return; const provider = $('dlg-provider').value;
  if (provider === 'paste' && !$('dlg-paste').value.trim()) { message('dlg-msg', '请先贴入一段证据。', true); return; }
  state.busy = true; $('dlg-go').disabled = true; message('dlg-msg', provider === 'paste' ? '正在保留证据…' : '正在展开观点，可能需要一些时间…');
  try { const packed = await post('/api/snippets/' + encodeURIComponent(state.expanding.id) + '/expand', { provider, query: $('dlg-query').value, paste: $('dlg-paste').value }); if (state.expanding.date === state.date) { state.packed = packed; renderAdvanced(); } $('expand-dlg').close(); }
  catch (err) { message('dlg-msg', err.message, true); }
  finally { state.busy = false; $('dlg-go').disabled = false; }
});

/* ---------- 启动 ---------- */
async function boot() {
  const qs = new URLSearchParams(location.search).get('day');
  const [days, health, roster] = await Promise.allSettled([api('/api/days'), api('/api/health'), api('/api/roster')]);
  state.days = days.status === 'fulfilled' ? array(days.value.days) : [];
  if (roster.status === 'fulfilled') state.roster = array(roster.value.roster);
  renderAuthors();
  const h = health.status === 'fulfilled' ? health.value : {};
  state.providers = array(h.providers).length ? h.providers : [{ id: 'paste', label: '粘贴外部证据' }];
  $('dlg-provider').innerHTML = state.providers.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('');
  $('health').textContent = `连接：DeepSeek ${h.deepseek?.ok ? '已配置' : '未配置'} · Notion ${h.notion?.ok ? '已配置' : '未配置'}`;
  setDay(/^\d{4}-\d{2}-\d{2}$/.test(qs || '') ? qs : localDate(), false);
  loadTrades(); loadSynthFlags();
  if (location.hash === '#history') setTimeout(() => $('history').scrollIntoView({ behavior: 'smooth' }), 300);
}
boot();
window.addEventListener('hashchange', () => { if (location.hash === '#history') document.getElementById('history').scrollIntoView({ behavior: 'smooth' }); });
