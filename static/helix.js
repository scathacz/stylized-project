'use strict';
/* 线描双螺旋（单色、细线、慢）。<canvas data-helix="hero|mark|mini">，自动挂载。
   el.__helix.setBusy(true) → “思考中”：一道细光沿螺旋缓慢扫过，速度不变。 */
(function () {
  const P = {
    hero: { turns: 3.2, amp: .34, lw: .75, rungs: 44, alpha: .22, speed: .16, samples: 220 },
    mark: { turns: 1.25, amp: .36, lw: 1, rungs: 6, alpha: .9, speed: .5, samples: 48 },
    mini: { turns: 1.6, amp: .36, lw: .9, rungs: 8, alpha: .75, speed: .9, samples: 56, busy: true },
  };
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const live = new Set();
  function mount(cv) {
    if (cv.__helix) return cv.__helix;
    const o = { ...(P[cv.dataset.helix] || P.hero) };
    const ctx = cv.getContext('2d'); let w = 0, h = 0, dpr = 1, t = 0, last = 0, visible = true, busy = !!o.busy, sh = busy ? 1 : 0, sweep = 0;
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim() || '#0a0a0a';
    function size() { const r = cv.getBoundingClientRect(); dpr = Math.min(devicePixelRatio || 1, 2); w = r.width; h = r.height; cv.width = Math.max(1, Math.round(w * dpr)); cv.height = Math.max(1, Math.round(h * dpr)); }
    const lift = u => sh * 2.2 * Math.exp(-Math.pow((u - sweep) / .07, 2));
    function draw() {
      if (!w || !h) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = ink; ctx.lineCap = 'round'; ctx.lineWidth = o.lw;
      const amp = h * o.amp, cy = h / 2, N = o.samples, k = o.turns * Math.PI * 2;
      for (const off of [0, Math.PI]) {
        let px = 0, py = 0;
        for (let i = 0; i <= N; i++) {
          const u = i / N, p = u * k + t + off, x = u * w, y = cy + Math.sin(p) * amp;
          if (i) { const z = (Math.cos(p - k / N / 2) + 1) / 2; ctx.globalAlpha = Math.min(1, o.alpha * (.18 + .82 * z) * (1 + lift(u))); ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke(); }
          px = x; py = y;
        }
      }
      ctx.lineWidth = o.lw * .7;
      for (let j = 0; j < o.rungs; j++) {
        const u = (j + .5) / o.rungs, p = u * k + t, x = u * w, y1 = cy + Math.sin(p) * amp, y2 = cy - Math.sin(p) * amp;
        ctx.globalAlpha = Math.min(1, o.alpha * .45 * (.25 + .75 * Math.abs(Math.sin(p))) * (1 + lift(u)));
        ctx.beginPath(); ctx.moveTo(x, y1); ctx.lineTo(x, y2); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    function frame(now) {
      const dt = Math.min(.05, (now - (last || now)) / 1000); last = now;
      t += dt * o.speed; sh += ((busy ? 1 : 0) - sh) * Math.min(1, dt * 2.5);
      sweep = sh > .01 ? (sweep + dt * .32) % 1.3 : -.2; draw();
    }
    const api = { el: cv, frame, draw, size, setBusy(v) { busy = !!v || !!o.busy; if (reduce.matches) { sh = 0; draw(); } }, get visible() { return visible; }, reset() { last = 0; } };
    cv.__helix = api; size(); draw();
    new ResizeObserver(() => { size(); draw(); }).observe(cv);
    if ('IntersectionObserver' in window) new IntersectionObserver(es => { visible = es[0].isIntersecting; api.reset(); }).observe(cv);
    live.add(api); loop(); return api;
  }
  let raf = 0;
  function tick(now) {
    raf = 0; if (document.hidden || reduce.matches) return;
    for (const a of live) { if (!a.el.isConnected) { live.delete(a); continue; } if (a.visible) a.frame(now); }
    if (live.size) raf = requestAnimationFrame(tick);
  }
  function loop() { if (!raf && !document.hidden && !reduce.matches) raf = requestAnimationFrame(tick); }
  document.addEventListener('visibilitychange', () => { live.forEach(a => a.reset()); loop(); });
  reduce.addEventListener?.('change', () => { live.forEach(a => a.draw()); loop(); });
  function scan(root) { if (root.matches?.('canvas[data-helix]')) mount(root); root.querySelectorAll?.('canvas[data-helix]').forEach(mount); }
  window.Helix = { mount, scan, setBusy(v) { document.querySelectorAll('canvas[data-helix="hero"],canvas[data-helix="mark"]').forEach(c => c.__helix?.setBusy(v)); } };
  const start = () => { scan(document); new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && scan(n)))).observe(document.body, { childList: true, subtree: true }); };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', start) : start();
})();
