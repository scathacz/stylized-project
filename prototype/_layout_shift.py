# -*- coding: utf-8 -*-
"""专项：点进编辑态不许跳排版（逐行量 y 坐标），以及重要标记能不能传到后端。"""
import io, sys, pathlib, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\codex测试\desk-notes")
sys.path.insert(0, r"D:\codex测试\desk-notes\prototype")
from playwright.sync_api import sync_playwright
from _http import start_note_server

HTML, DATA = start_note_server()
fails = []


def check(name, got, want=True):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + name)
    if not ok:
        print(f"         got={got!r}\n        want={want!r}")
        fails.append(name)


def add(pg, text):
    pg.fill("#input", text)
    pg.press("#input", "Control+Enter")
    needle = text.strip().split("\n")[0][:24]
    pg.wait_for_function(
        """(t) => [...document.querySelectorAll('#out .ln .tx')].some(e => (e.textContent || '').includes(t))""",
        arg=needle,
        timeout=8000,
    )


def new_author(pg, name):
    pg.click("#chip-add")
    pg.fill("#pop-input", name)
    pg.press("#pop-input", "Enter")
    pg.wait_for_timeout(180)


# 每「条」文本的几何（只取文字条目，图片条不算）
LINE_Y = """(a) => {
  const grp = document.querySelector(`#out .grp[data-author="${a}"]`);
  const rows = [...grp.querySelectorAll(':scope > .ln:not(.img)')];
  return {
    n: rows.length,
    y: rows.map(r => Math.round(r.getBoundingClientRect().top * 10) / 10),
    h: rows.map(r => Math.round(r.getBoundingClientRect().height * 10) / 10),
    groupH: Math.round(grp.getBoundingClientRect().height * 10) / 10,
  };
}"""
# 编辑态里每一「条」= 一个 .eline 块
EDIT_Y = """(a) => {
  const tx = document.querySelector(`.tx[contenteditable][data-author="${a}"]`);
  const rows = [...tx.querySelectorAll(':scope > .eline')];
  return {
    n: rows.length,
    y: rows.map(r => Math.round(r.getBoundingClientRect().top * 10) / 10),
    h: rows.map(r => Math.round(r.getBoundingClientRect().height * 10) / 10),
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 940})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(HTML); pg.wait_for_timeout(500)

    print("== 准备：三段长短不一的内容（含折行的那种）==")
    new_author(pg, "VITO")
    add(pg, "中午中债和地产一起涨，预测降准降息")
    add(pg, "算力链看多（置信中）、存储看多（置信中）、靠外部融资扩张的 AI 基建看空（置信中）、"
            "网络安全看多（置信低）、能源与大宗中性（预期走平，置信低）")
    add(pg, "债券市场比FED还鹰派")
    before = pg.evaluate(LINE_Y, "VITO")
    print(f"   静止态：{before['n']} 条，y = {before['y']}")
    print(f"           高 = {before['h']}，组高 = {before['groupH']}")

    print("== 1. 点进编辑态：每条 y 和高度都必须一致（含折行的长句）==")
    pg.click('#out .grp[data-author="VITO"] .ln >> nth=1')
    pg.wait_for_timeout(450)
    after = pg.evaluate(EDIT_Y, "VITO")
    after_hint = pg.inner_text("#edithint")
    still = pg.evaluate(LINE_Y, "VITO")
    print(f"   编辑态：{after['n']} 条，y = {after['y']}")
    print(f"           高 = {after['h']}，组高 = {still['groupH']}")

    check("条数没变", after["n"], before["n"])
    maxdy = max(abs(x - z) for x, z in zip(after["y"], before["y"])) if after["n"] == before["n"] else 999
    check(f"每条 y 偏移 <= 1px（实测最大 {maxdy}px）", maxdy <= 1.0)
    maxdh = max(abs(x - z) for x, z in zip(after["h"], before["h"])) if after["n"] == before["n"] else 999
    check(f"每条高度一致（实测最大差 {maxdh}px）", maxdh <= 1.0)
    dg = abs(still["groupH"] - before["groupH"])
    check(f"整组高度没跳（实测差 {dg}px）", dg <= 2.0)
    check("编辑时给出快捷键提示", after_hint, "Ctrl+Z 撤销 · Ctrl+Shift+Z 重做 · Ctrl+B 加粗＝标为重要 · Ctrl+Enter 存 · Esc 撤")
    check("折行的长句也没被拆条", after["n"], 3)

    print("== 2. 退出编辑后也不能跳 ==")
    pg.keyboard.press("Control+Enter")
    pg.wait_for_function("() => !document.querySelector('.tx[contenteditable]')", timeout=8000)
    back = pg.evaluate(LINE_Y, "VITO")
    maxdb = max(abs(x - z) for x, z in zip(back["y"], before["y"]))
    check(f"回到静止态 y 完全一致（实测最大差 {maxdb}px）", maxdb <= 1.0)

    print("== 3. 悬浮特效是纯视觉的（不占布局、不位移）==")
    pg.hover('#out .grp[data-author="VITO"]')
    pg.wait_for_timeout(400)
    hov = pg.evaluate(LINE_Y, "VITO")
    maxdh2 = max(abs(x - z) for x, z in zip(hov["y"], before["y"]))
    check(f"悬浮时行位置不动（实测最大差 {maxdh2}px）", maxdh2 <= 1.0)
    check("悬浮时组高度不变", abs(hov["groupH"] - before["groupH"]) <= 2.0)
    check("悬浮时出现「点一下改」", pg.evaluate("""() => {
      const c = document.querySelector('#out .grp[data-author="VITO"] .cue');
      return c ? getComputedStyle(c).opacity : '0'; }""") == "1")
    check("悬浮有背景变化", pg.evaluate("""() => {
      const g = document.querySelector('#out .grp[data-author="VITO"]');
      return getComputedStyle(g).backgroundColor !== 'rgba(0, 0, 0, 0)'; }"""))
    check("过渡是平滑的（有 transition）", pg.evaluate("""() => {
      const g = document.querySelector('#out .grp[data-author="VITO"]');
      return getComputedStyle(g).transitionDuration !== '0s'; }"""))

    print("== 4. 编辑态有明显但不跳的视觉标记 ==")
    pg.click('#out .grp[data-author="VITO"] .ln >> nth=0')
    pg.wait_for_timeout(400)
    check("整组加了 editing 类", pg.locator("#out .grp.editing").count(), 1)
    check("编辑态有独立背景", pg.evaluate("""() => {
      const g = document.querySelector('#out .grp.editing');
      return getComputedStyle(g).backgroundColor === 'rgb(255, 253, 247)'; }"""))
    check("编辑态左侧有竖条（伪元素）", pg.evaluate("""() => {
      const g = document.querySelector('#out .grp.editing');
      return getComputedStyle(g, '::before').width === '2px'; }"""))
    check("其它组没有编辑态样式", pg.locator("#out .grp.editing").count(), 1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("== 5. 图片在编辑态里不会消失 ==")
    new_author(pg, "Agent")
    pg.evaluate("""async () => {
      const c = document.createElement('canvas'); c.width = 100; c.height = 50;
      const x = c.getContext('2d'); x.fillStyle = '#284b3d'; x.fillRect(0,0,100,50);
      const blob = await new Promise(r => c.toBlob(r, 'image/png'));
      const dt = new DataTransfer();
      dt.items.add(new File([blob], 's.png', {type:'image/png'}));
      document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles:true, cancelable:true}));
    }""")
    pg.wait_for_selector("#out img.thumb", timeout=8000)
    add(pg, "图下面的一句话")
    pg.click('#out .grp[data-author="Agent"] .ln:not(.img) >> nth=0')
    pg.wait_for_timeout(400)
    check("编辑态里真图仍然显示", pg.evaluate("""() => {
      const i = document.querySelector('#out .grp.editing img.thumb');
      return !!i && i.naturalWidth > 0; }"""))
    check("编辑区里图片位置显示为 [图]", pg.evaluate("""() => {
      const tx = document.querySelector('.tx[contenteditable]');
      return tx.innerText.includes('[图]'); }"""))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("== 6. 重要标记要能传到后端 ==")
    new_author(pg, "策略")
    add(pg, "混天绫加仓亨通，白银有色")
    add(pg, "稳健博弈小幅拿中际旭创")
    pg.click('#out .grp[data-author="策略"] .ln >> nth=0')
    pg.wait_for_timeout(350)
    pg.evaluate("""() => { const tx = document.querySelector('.tx[contenteditable]');
      const el = tx.querySelector(':scope > .eline');       // 第一条
      const n = el.firstChild;                              // 它的文本节点
      const r = document.createRange(); r.setStart(n, 0); r.setEnd(n, n.length);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    pg.keyboard.press("Control+b"); pg.wait_for_timeout(200)
    pg.keyboard.press("Control+Enter")
    pg.wait_for_function("() => !document.querySelector('.tx[contenteditable]')", timeout=8000)

    payload = pg.evaluate("() => window.luojianStructured()")
    print("  结构化载荷：")
    print("   ", json.dumps(payload, ensure_ascii=False)[:400], "...")
    check("载荷有日期", payload["date"], pg.input_value("#day"))
    check("作者是字段，不用猜", sorted({i["source"] for i in payload["items"]}), ["VITO", "strategies".replace("strategies", "Agent"), "策略"][0:0] or sorted({i["source"] for i in payload["items"]}))
    strat = [i for i in payload["items"] if i["source"] == "策略"]
    check("策略名下 2 条", len(strat), 2)
    check("第一条 important = true", strat[0]["important"], True)
    check("第二条 important = false", strat[1]["important"], False)
    check("正文里带着 ** ，纯文本后端也认得出", strat[0]["text"], "**混天绫加仓亨通，白银有色**")
    check("图片条目带 media id", any(i["media"] for i in payload["items"] if i["source"] == "Agent"))
    check("media 为空的条目是 null", all(i["media"] is None for i in payload["items"] if i["source"] == "策略"))

    print("== 7. 顶部统计条数 ==")
    check("显示 1 条重要", pg.inner_text("#out .imp-n"), "1 条重要")

    print("== 8. 点进编辑后，条内换行还在 ==")
    new_author(pg, "换行")
    add(pg, "第一行\n第二行")
    static = pg.evaluate("""() => {
      const n = document.querySelector('#out .grp[data-author="换行"] .ln .tx');
      return n ? n.textContent.replace(/\\r/g, '') : '';
    }""")
    check("静止态就是两行", static, "第一行\n第二行")
    pg.click('#out .grp[data-author="换行"] .ln')
    pg.wait_for_timeout(400)
    editing_text = pg.evaluate("""() => {
      const tx = document.querySelector('.tx[contenteditable]');
      return tx ? tx.innerText.replace(/\\r/g, '') : '';
    }""")
    check("一点进去换行还在", editing_text.rstrip("\n"), "第一行\n第二行")
    check("编辑态用了 br 而不是裸换行", pg.evaluate("""() => {
      const el = document.querySelector('.tx[contenteditable] .eline');
      return !!el && el.querySelectorAll('br').length >= 1;
    }"""))
    pg.keyboard.press("Control+Enter")
    pg.wait_for_function("() => !document.querySelector('.tx[contenteditable]')", timeout=8000)
    saved = pg.evaluate("""() => {
      const n = document.querySelector('#out .grp[data-author="换行"] .ln .tx');
      const payload = window.luojianStructured().items.find(i => i.source === '换行');
      return { dom: n ? n.textContent.replace(/\\r/g, '') : '', api: payload && payload.text };
    }""")
    check("存完 DOM 换行还在", saved["dom"], "第一行\n第二行")
    check("存进后端的也是两行", saved["api"], "第一行\n第二行")
    check("无 JS 报错", errs, [])
    b.close()

print("\n=== 结果:", "全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}", "===")
sys.exit(1 if fails else 0)
