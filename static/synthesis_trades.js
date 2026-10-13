// 进行中的交易（左栏小列表）
(async () => {
  const box = document.getElementById('open-trades-list'); if (!box) return;
  try {
    const res = await fetch('/api/trades?status=open'); const data = await res.json();
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const items = (data.items || []).slice(0, 8);
    box.innerHTML = items.length ? items.map(i => `<a href="/trades?id=${encodeURIComponent(i.id)}" title="${esc(i.plan_text)}">${esc(i.title)}<span>${esc((i.source_day || '').slice(5).replace('-', '/'))}</span></a>`).join('') : '<span class="lnk">暂无</span>';
  } catch { box.innerHTML = ''; }
})();
