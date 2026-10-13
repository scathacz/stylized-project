'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const KINDS = ['跟踪', '想法变化', '加减仓', '风险'];
const RESULTS = ['盈', '亏', '平', '未做'];
const state = { items: [], sel: null, kind: '跟踪', closing: false, editing: null };

async function req(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json' } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 ${res.status}`);
  return data;
}
function flash(msg = '', error = false) { const el = $('sync'); el.textContent = msg; el.classList.toggle('error', error); if (msg && !error) setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 2600); }
function stamp(iso) { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; }
function dayLabel(day) { if (!day) return '手动'; const [, m, d] = day.split('-'); return `${+m}月${+d}日`; }
function held(it) {
  const start = new Date((it.source_day || it.created_at || '').slice(0, 10) + 'T00:00:00');
  const end = it.status === 'closed' && it.closed_at ? new Date(it.closed_at) : new Date();
  const n = Math.max(0, Math.floor((end - start) / 86400000));
  return isNaN(n) ? '' : `${n} 天`;
}
function lastUpdate(it) { const e = (it.entries || []).at(-1); return e ? stamp(e.at) : stamp(it.created_at); }

function itemHtml(it) {
  const cur = it.id === state.sel ? ' aria-current="true"' : '';
  return `<li><button type="button" class="t-item ${it.status}" data-id="${esc(it.id)}"${cur}><b>${esc(it.title)}</b><small><span>${esc(dayLabel(it.source_day))}</span><em>${esc(held(it))}</em><span>${it.status === 'closed' ? (it.result ? esc(it.result) : '已了结') : '更新 ' + esc(lastUpdate(it))}</span></small></button></li>`;
}
function renderList() {
  const open = state.items.filter(i => i.status === 'open');
  const closed = state.items.filter(i => i.status !== 'open');
  $('open-list').innerHTML = open.map(itemHtml).join('') || '<li class="t-empty">没有进行中的计划</li>';
  $('closed-list').innerHTML = closed.map(itemHtml).join('') || '<li class="t-empty">还没有了结的交易</li>';
  $('open-count').textContent = open.length || '';
  $('closed-count').textContent = closed.length || '';
}

function entryHtml(e) {
  if (state.editing === e.id) {
    return `<li data-eid="${esc(e.id)}"><div class="tl-head"><time>${esc(stamp(e.at))}</time><span class="tl-kind">${esc(e.kind)}</span></div><textarea class="tl-edit" rows="3">${esc(e.text)}</textarea><div class="t-row"><button type="button" class="lnk" data-act="edit-cancel">取消</button><button type="button" class="t-btn solid" data-act="edit-save">保存</button></div></li>`;
  }
  return `<li data-eid="${esc(e.id)}"><div class="tl-head"><time>${esc(stamp(e.at))}</time><span class="tl-kind">${esc(e.kind)}</span>${e.edited_at ? '<span>已修改</span>' : ''}<span class="tl-tools"><button type="button" class="lnk" data-act="edit">修改</button><button type="button" class="lnk" data-act="del-entry">删除</button></span></div><p class="tl-text">${esc(e.text)}</p></li>`;
}
function renderDetail() {
  const it = state.items.find(i => i.id === state.sel);
  const box = $('detail');
  if (!it) { box.innerHTML = `<div class="t-wrap"><div class="empty">左侧选一条计划；没有的话，点「从笔记导入」或「手动添加」。</div></div>`; return; }
  const closed = it.status === 'closed';
  const lesson = closed ? `<section class="t-lesson"><h3>了结心得${it.result ? `<span class="res">${esc(it.result)}</span>` : ''}</h3><p>${esc(it.closing_note || '（未写心得）')}</p><div class="t-meta"><span>了结于 <em>${esc(stamp(it.closed_at))}</em></span><span>持有 <em>${esc(held(it))}</em></span></div></section>` : '';
  const entries = (it.entries || []).map(entryHtml).join('');
  const kinds = KINDS.map(k => `<button type="button" data-kind="${esc(k)}" aria-pressed="${k === state.kind}">${esc(k)}</button>`).join('') + `<input id="kind-free" maxlength="20" placeholder="自定义标签" value="${KINDS.includes(state.kind) ? '' : esc(state.kind)}" />`;
  const closeForm = state.closing ? `<form class="t-form" id="close-form"><label for="close-note">了结心得 · 复盘时最想记住的</label><textarea id="close-note" rows="5" placeholder="这笔做对 / 做错了什么？下次怎么做？">${esc(it.closing_note || '')}</textarea><label>结果</label><div class="t-results kinds">${RESULTS.map(r => `<button type="button" data-result="${r}" aria-pressed="${it.result === r}">${r}</button>`).join('')}<input id="close-result" maxlength="40" placeholder="或写：+8% / 止损…" value="${RESULTS.includes(it.result) ? '' : esc(it.result || '')}" /></div><div class="t-row"><button type="button" class="lnk" id="close-cancel">取消</button><button type="submit" class="t-btn solid">确认了结</button></div></form>` : '';
  box.innerHTML = `<div class="t-wrap">
    <header class="doc-head"><span class="kicker">${closed ? 'Closed · 已了结' : 'Open · 进行中'} · ${esc(dayLabel(it.source_day))}</span><h2>${esc(it.title)}</h2><div class="t-meta"><span>持有 <em>${esc(held(it))}</em></span><span>记录 <em>${(it.entries || []).length}</em> 条</span><span>最近 <em>${esc(lastUpdate(it))}</em></span></div></header>
    ${lesson}
    <blockquote class="t-quote">${esc(it.plan_text)}<cite>— TradingPlan · ${esc(it.source_day || '手动添加')}</cite></blockquote>
    <p class="t-sec">跟踪记录</p>
    ${entries ? `<ol class="timeline">${entries}</ol>` : '<p class="t-empty" style="padding:0 0 22px">还没有记录。盘中的感受、想法的变化，都写在下面。</p>'}
    ${closeForm}
    <form class="t-add" id="add-form"><div class="kinds">${kinds}</div><textarea id="add-text" rows="3" spellcheck="false" placeholder="此刻的感受、看到的变化、想法怎么变了…  Ctrl+Enter 记下"></textarea><div class="t-foot"><span class="hint">Ctrl+Enter 记下</span><span class="t-row">${closed ? '<button type="button" class="t-btn" id="btn-reopen">重新打开</button>' : (state.closing ? '' : '<button type="button" class="t-btn warn" id="btn-close">了结</button>')}<button type="submit" class="t-btn solid">记下</button></span></div></form>
    <div class="t-danger"><span class="hint lnk">创建于 ${esc(stamp(it.created_at))}</span><button type="button" class="lnk" id="btn-delete">删除这条交易</button></div>
  </div>`;
}
function render() { renderList(); renderDetail(); }

function upsert(item) {
  const i = state.items.findIndex(x => x.id === item.id);
  if (i >= 0) state.items[i] = item; else state.items.unshift(item);
}
async function load() {
  const data = await req('/api/trades');
  state.items = data.items;
  if (!state.items.some(i => i.id === state.sel)) state.sel = (state.items[0] || {}).id || null;
  render();
}
async function act(fn, ok) {
  try { const item = await fn(); if (item && item.id) upsert(item); await load(); if (ok) flash(ok); }
  catch (err) { flash(err.message, true); }
}
function select(id) {
  state.sel = id; state.closing = false; state.editing = null;
  history.replaceState(null, '', '/trades?id=' + encodeURIComponent(id));
  render();
}

document.addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.id) return select(t.dataset.id);
  if (t.dataset.kind) { state.kind = t.dataset.kind; document.querySelectorAll('[data-kind]').forEach(b => b.setAttribute('aria-pressed', b === t)); $('kind-free').value = ''; return; }
  if (t.dataset.result) { document.querySelectorAll('[data-result]').forEach(b => b.setAttribute('aria-pressed', b === t && b.getAttribute('aria-pressed') !== 'true')); $('close-result').value = ''; return; }
  const id = state.sel;
  const li = t.closest('[data-eid]'); const eid = li && li.dataset.eid;
  switch (t.id || t.dataset.act) {
    case 'btn-import': return act(() => req('/api/trades/import', { method: 'POST', body: '{}' }).then(d => { flash(d.added.length ? `导入 ${d.added.length} 条新计划` : '没有新的计划'); }));
    case 'btn-new': $('new-form').hidden = false; $('new-title').focus(); return;
    case 'new-cancel': $('new-form').hidden = true; return;
    case 'btn-close': state.closing = true; renderDetail(); $('close-note').focus(); return;
    case 'close-cancel': state.closing = false; renderDetail(); return;
    case 'btn-reopen': return act(() => req(`/api/trades/${id}/reopen`, { method: 'POST' }), '已重新打开');
    case 'btn-delete': if (confirm('删除这条交易及其全部记录？')) { state.sel = null; act(() => req(`/api/trades/${id}`, { method: 'DELETE' }), '已删除'); } return;
    case 'edit': state.editing = eid; renderDetail(); return;
    case 'edit-cancel': state.editing = null; renderDetail(); return;
    case 'edit-save': { const text = li.querySelector('textarea').value.trim(); if (!text) return; state.editing = null; return act(() => req(`/api/trades/${id}/entries/${eid}`, { method: 'PATCH', body: JSON.stringify({ text }) }), '已修改'); }
    case 'del-entry': if (confirm('删除这条记录？')) act(() => req(`/api/trades/${id}/entries/${eid}`, { method: 'DELETE' })); return;
  }
});
document.addEventListener('input', e => { if (e.target.id === 'kind-free') { const v = e.target.value.trim(); state.kind = v || '跟踪'; document.querySelectorAll('[data-kind]').forEach(b => b.setAttribute('aria-pressed', !v && b.dataset.kind === '跟踪')); } });
document.addEventListener('submit', async e => {
  e.preventDefault();
  const id = state.sel;
  if (e.target.id === 'add-form') {
    const text = $('add-text').value.trim(); if (!text || !id) return;
    await act(() => req(`/api/trades/${id}/entries`, { method: 'POST', body: JSON.stringify({ text, kind: state.kind }) }), '已记下');
    const box = $('add-text'); if (box) box.focus();
  } else if (e.target.id === 'close-form') {
    const pressed = document.querySelector('[data-result][aria-pressed="true"]');
    const result = $('close-result').value.trim() || (pressed ? pressed.dataset.result : '');
    state.closing = false;
    await act(() => req(`/api/trades/${id}/close`, { method: 'POST', body: JSON.stringify({ closing_note: $('close-note').value, result }) }), '已了结，心得已存');
  } else if (e.target.id === 'new-form') {
    const body = { title: $('new-title').value.trim(), plan_text: $('new-plan').value.trim() };
    if (!body.title && !body.plan_text) return;
    try { const item = await req('/api/trades', { method: 'POST', body: JSON.stringify(body) }); $('new-form').reset(); $('new-form').hidden = true; state.sel = item.id; await load(); flash('已添加'); }
    catch (err) { flash(err.message, true); }
  }
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    const f = e.target.closest && e.target.closest('form'); if (f) { e.preventDefault(); f.requestSubmit(); }
    else if (e.target.classList.contains('tl-edit')) { e.preventDefault(); e.target.closest('li').querySelector('[data-act="edit-save"]').click(); }
  }
});
state.sel = new URLSearchParams(location.search).get('id');
load().catch(err => flash(err.message, true));
