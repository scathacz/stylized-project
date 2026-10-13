'use strict';
/* 三页共用顶栏：<header id="gnav" data-active="today|synthesis|trades|history"></header> */
(function () {
  const el = document.getElementById('gnav'); if (!el) return;
  const active = el.dataset.active || '';
  const qs = new URLSearchParams(location.search).get('day');
  const day = /^\d{4}-\d{2}-\d{2}$/.test(qs || '') ? qs : '';
  const d = new Date(); const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const withDay = p => day ? `${p}?day=${day}` : p;
  const links = [['note', '速记', '/note'], ['trades', '交易跟踪', '/trades'], ['synthesis', '综合判断', withDay('/synthesis')], ['today', '今日', withDay('/')], ['history', '历史', '/#history']];
  el.className = 'gnav';
  el.innerHTML = `<a class="gnav-brand" href="/" aria-label="交易笔记首页"><span class="mark" aria-hidden="true"><canvas data-helix="mark"></canvas></span><b>交易笔记</b></a>`
    + `<nav aria-label="全局导航"><ul class="gnav-links">${links.map(([k, label, href]) => `<li><a href="${href}" data-nav="${k}"${k === active ? ' aria-current="page"' : ''}>${label}</a></li>`).join('')}</ul></nav>`
    + `<span class="gnav-day">${today.replaceAll('-', '.')}</span>`;
})();

