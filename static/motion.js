'use strict';
/* 克制的动效：入场错峰、顶栏滑动指示、数字滚动、/synthesis 正文切换过渡。 */
(function () {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ready = fn => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn();
  ready(() => {
    // 入场：卡片 / 区块错峰上浮
    const items = document.querySelectorAll('.hero, .flow, .card, .nav, .doc-head, .chatpane, .tnav, .t-wrap, .main > *');
    items.forEach((el, i) => { el.style.setProperty('--d', `${Math.min(i, 8) * 30}ms`); el.classList.add('rise'); });
    // 顶栏滑动指示
    const ul = document.querySelector('.gnav-links');
    if (ul) {
      const ind = document.createElement('li'); ind.className = 'gnav-ind'; ind.setAttribute('aria-hidden', 'true'); ul.prepend(ind);
      const cur = () => ul.querySelector('a[aria-current=page]');
      const to = (a, instant) => { if (!a) { ind.style.opacity = 0; return; } ind.style.transition = instant ? 'none' : ''; ind.style.opacity = 1; ind.style.width = a.offsetWidth + 'px'; ind.style.transform = `translateX(${a.parentElement.offsetLeft}px)`; };
      ul.classList.add('has-ind'); to(cur(), true); requestAnimationFrame(() => to(cur(), true));
      ul.addEventListener('mouseover', e => { const a = e.target.closest('a'); if (a) to(a); });
      ul.addEventListener('mouseleave', () => to(cur()));
      addEventListener('resize', () => to(cur(), true));
      document.fonts?.ready.then(() => to(cur(), true));
    }
    // /synthesis：正文替换时淡入
    const doc = document.getElementById('doc');
    const title = document.getElementById('doc-title');
    if (doc && title) new MutationObserver(() => { doc.classList.remove('md-in'); void doc.offsetWidth; doc.classList.add('md-in'); }).observe(title, { childList: true, characterData: true, subtree: true });
  });
})();
