# -*- coding: utf-8 -*-
"""布局审计：真实样例（含格式标记和一张图）压满后查溢出、断点、写字区尺寸、XSS 转义。"""
import io, sys, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\codex测试\desk-notes")
sys.path.insert(0, r"D:\codex测试\desk-notes\prototype")
from playwright.sync_api import sync_playwright
from _http import start_note_server

HTML, DATA = start_note_server()
fails = []


def check(name, got, want=True):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + f"{name}  ->  {got!r}")
    if not ok:
        fails.append(name)


AUDIT = """() => {
  const de = document.documentElement;
  const over = [...document.querySelectorAll('body *')].filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && (r.right > de.clientWidth + 1 || r.left < -1);
  }).map(el => el.tagName + '.' + (el.className || '') + ' right=' + Math.round(el.getBoundingClientRect().right));
  const box = s => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return {x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)}; };
  return {
    docW: de.clientWidth, scrollW: de.scrollWidth,
    overflowing: over.slice(0, 6),
    left: box('.left'), right: box('.right'), ta: box('#input'), out: box('#out'),
    chips: box('#chips'),
    outScrolls: (() => { const o = document.querySelector('#out'); return o.scrollHeight > o.clientHeight + 2; })(),
    chipRows: new Set([...document.querySelectorAll('#chips .chip')].map(e => Math.round(e.getBoundingClientRect().top))).size,
    chipCount: document.querySelectorAll('#chips .chip').length,
    lines: document.querySelectorAll('#out .ln').length,
    groups: document.querySelectorAll('#out .grp').length,
    thumbs: document.querySelectorAll('#out img.thumb').length,
    bold: document.querySelectorAll('#out .tx b').length,
    imp: document.querySelectorAll('#out .ln.imp').length,
    collapsed: [...document.querySelectorAll('.chip, .ln, .pane, #q')].filter(e => {
      const r = e.getBoundingClientRect(); return r.width < 8 || r.height < 8; }).length,
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h, label in [(1440, 940, "桌面 1440x940"), (1100, 820, "窄桌面 1100"), (900, 800, "平板 900"), (420, 880, "手机 420")]:
        pg = b.new_page(viewport={"width": w, "height": h})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(HTML); pg.wait_for_timeout(400)
        pg.evaluate("""async () => {
          const day = document.querySelector('#day').value;
          await fetch('/api/notes/' + day, {method:'DELETE'});
        }""")
        pg.reload(); pg.wait_for_timeout(400)
        pg.click("#btn-sample")
        pg.wait_for_function("() => document.querySelectorAll('#out .ln').length >= 18", timeout=15000)
        r = pg.evaluate(AUDIT)
        print(f"== {label} ==")
        check("无横向滚动", r["scrollW"] <= r["docW"] + 1)
        check("无元素溢出视口", r["overflowing"], [])
        check("无塌陷元素", r["collapsed"], 0)
        check("17 条文字 + 1 张图 / 5 组", (r["lines"], r["groups"], r["thumbs"]), (18, 5, 1))
        check("示例里的加粗渲染出来了", r["bold"], 2)
        check("加粗的行被标为重要", r["imp"], 2)
        check("作者行在写字区上方", r["chips"]["y"] < r["ta"]["y"])
        check("无 JS 报错", errs, [])
        if w >= 880:
            check("左输入右输出并排", r["right"]["x"] > r["left"]["x"] + r["left"]["w"] - 10)
            check("写字区够大（>=260px 高）", r["ta"]["h"] >= 260)
        else:
            check("窄屏堆叠", r["right"]["y"] > r["left"]["y"] + 100)
            check("写字区够大（>=240px 高）", r["ta"]["h"] >= 240)
        print(f"     写字区 {r['ta']['w']}x{r['ta']['h']}，输出区 {r['out']['w']}x{r['out']['h']}（内部滚动={r['outScrolls']}）")
        print(f"     chip {r['chipCount']} 个占 {r['chipRows']} 行")
        pg.close()

    print("== 极端内容：400 字无空格串 + emoji + HTML 注入 + 半截标记 ==")
    pg = b.new_page(viewport={"width": 1440, "height": 940})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(HTML); pg.wait_for_timeout(400)
    pg.evaluate("""async () => {
      const day = document.querySelector('#day').value;
      await fetch('/api/notes/' + day, {method:'DELETE'});
    }""")
    pg.reload(); pg.wait_for_timeout(400)
    for s in ["A" * 400, "多行\n\n中间空行\n结尾", "🇺🇸 emoji ✓ 「引号」 <script>alert(1)</script>",
              "半截标记 ==没闭合 和 **没闭合 和 [多]没闭合"]:
        pg.fill("#input", s); pg.press("#input", "Control+Enter")
        pg.wait_for_function(
            """(t) => [...document.querySelectorAll('#out .ln .tx')].some(e => (e.textContent||'').includes(t))""",
            arg=s.strip().split("\n")[0][:20],
            timeout=8000,
        )
    r = pg.evaluate(AUDIT)
    check("长串不撑破布局", r["overflowing"], [])
    check("HTML 被转义（无注入）", pg.locator("#out script").count(), 0)
    check("script 字样按原文显示", "<script>alert(1)</script>" in pg.inner_text("#out"))
    check("空行原样保留", "多行\n\n中间空行" in pg.inner_text("#out"))
    check("没闭合的标记不会吃掉文字", "半截标记 ==没闭合 和 **没闭合 和 [多]没闭合" in pg.inner_text("#out"))
    check("无 JS 报错", errs, [])
    pg.close()
    b.close()

print("\n=== 布局审计:", "全部通过" if not fails else f"{len(fails)} 项异常 -> {fails}", "===")
sys.exit(1 if fails else 0)
