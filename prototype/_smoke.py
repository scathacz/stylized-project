# -*- coding: utf-8 -*-
"""速记原型冒烟：布局 / 作者点选与大小写归一 / 点内容整组改 / Ctrl+B 加粗即重要 / 图片 / 迁移 / 回灌落笺。"""
import io, sys, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\codex测试\desk-notes")
sys.path.insert(0, r"D:\codex测试\desk-notes\prototype")
from datetime import date
from playwright.sync_api import sync_playwright
from desk.parser import parse_notes
from _http import start_note_server

HTML, DATA = start_note_server()
SHOT = pathlib.Path(r"D:\codex测试\desk-notes\prototype")
fails = []


def check(name, got, want=True):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + name)
    if not ok:
        print(f"         got={got!r}\n        want={want!r}")
        fails.append(name)


def cn(iso):
    y, m, d = iso.split("-")
    return f"{y}年{int(m)}月{int(d)}日"


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
    pg.wait_for_timeout(200)


def chip_names(pg):
    return pg.eval_on_selector_all("#chips .chip[data-a]", "els => els.map(e => e.dataset.a)")


def selected(pg):
    return pg.eval_on_selector_all("#chips .chip.on", "els => els.map(e => e.dataset.a)")


def heads(pg):
    return pg.eval_on_selector_all("#out .who", "els => els.map(e => e.childNodes[0].nodeValue)")


def lines(pg, author=None):
    return pg.evaluate("""(a) => {
      const gs = [...document.querySelectorAll('#out .grp')];
      const scope = a ? gs.filter(x => x.dataset.author === a) : gs;
      return scope.flatMap(x => [...x.querySelectorAll('.ln .tx')].map(t => t.textContent));
    }""", author)


def group_text(pg, author):
    return pg.evaluate("""(a) => {
      const tx = [...document.querySelectorAll('.tx[contenteditable]')].find(e => e.dataset.author === a);
      return tx ? tx.innerText : null;
    }""", author)


def enter_edit(pg, author, nth=0):
    """悬浮到内容上点一下，整组进入编辑。"""
    pg.locator(f'#out .grp[data-author="{author}"] .ln').nth(nth).click()
    pg.wait_for_timeout(220)


def select_line(pg, which="all"):
    """选中编辑区里的第 which 条（每条是一个 .eline 块）。"""
    ok = pg.evaluate("""(w) => {
      const tx = document.querySelector('.tx[contenteditable]');
      if (w === 'all'){
        const r = document.createRange(); r.selectNodeContents(tx);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r); return true;
      }
      const el = tx.querySelectorAll(':scope > .eline')[w];
      if (!el) return false;
      const r = document.createRange(); r.selectNodeContents(el);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      return true;
    }""", which)
    assert ok, f"选不中第 {which} 条"
    pg.wait_for_timeout(150)


def save(pg):
    pg.keyboard.press("Control+Enter")
    pg.wait_for_function("() => !document.querySelector('.tx[contenteditable]')", timeout=8000)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 940})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(HTML); pg.wait_for_timeout(600)
    today = pg.input_value("#day")

    print("== 1. 布局与图标 ==")
    pos = pg.evaluate("""() => { const c = document.querySelector('#chips').getBoundingClientRect(),
      i = document.querySelector('#input').getBoundingClientRect();
      return {chip: Math.round(c.top), input: Math.round(i.top)}; }""")
    check("chip 行在输入框上方", pos["chip"] < pos["input"])
    check("未署名是空位", pg.inner_text("#chips .chip.blank"), "")
    check("新作者是文字不是加号", pg.inner_text("#chip-add"), "新作者")
    check("页面上没有任何按钮图标",
          pg.evaluate("""() => [...document.querySelectorAll('#out button, #chips button')]
            .every(b => /^[\\u4e00-\\u9fa5A-Za-z0-9]+$/.test(b.textContent.trim()) || b.textContent.trim() === '')"""))

    print("== 2. 写一句，回车进右边 ==")
    add(pg, "期权和树都是空 - 要小心点 - 全员卖C")
    check("作者标题是裸名字", heads(pg), ["未署名"])
    check("日期在最上面", pg.inner_text("#out").splitlines()[0], cn(today))
    check("内容垒在作者下面", lines(pg, "未署名"), ["期权和树都是空 - 要小心点 - 全员卖C"])

    print("== 3. 新作者 + 点内容整组进入编辑 ==")
    new_author(pg, "VITO")
    add(pg, "债券市场比FED还鹰派")
    add(pg, "市场在杀融资 - 康宁 CoreWeave")
    check("VITO 名下 2 条", len(lines(pg, "VITO")), 2)
    enter_edit(pg, "VITO")
    check("整组变成一块可编辑区", pg.locator('.tx[contenteditable][data-author="VITO"]').count(), 1)
    check("这块里就是这个作者今天的全部内容", group_text(pg, "VITO"),
          "债券市场比FED还鹰派\n市场在杀融资 - 康宁 CoreWeave")
    check("编辑时给出快捷键提示", pg.inner_text("#edithint"), "Ctrl+Z 撤销 · Ctrl+Shift+Z 重做 · Ctrl+B 加粗＝标为重要 · Ctrl+Enter 存 · Esc 撤")
    check("其它组不受影响", pg.locator(".tx[contenteditable]").count(), 1)

    print("== 4. 只把第一行标为重要 ==")
    select_line(pg, 0)
    pg.keyboard.press("Control+b"); pg.wait_for_timeout(200)
    check("加粗只落在第一行", pg.locator(".tx[contenteditable] b").count(), 1)
    save(pg)
    check("存完不再是编辑态", pg.locator(".tx[contenteditable]").count(), 0)
    check("第一行是重要", pg.locator("#out .ln.imp").count(), 1)
    check("第一行文字没变", lines(pg, "VITO")[0], "债券市场比FED还鹰派")
    check("第二行没被带上", pg.locator("#out .ln.imp .tx").inner_text(), "债券市场比FED还鹰派")

    print("== 5. 再点进去 Ctrl+B 取消加粗 ==")
    enter_edit(pg, "VITO")
    select_line(pg, 0)
    pg.keyboard.press("Control+b"); pg.wait_for_timeout(200)
    check("先取消掉", pg.locator(".tx[contenteditable] b").count(), 0)
    save(pg)
    check("不再有重要标记", pg.locator("#out .ln.imp").count(), 0)

    print("== 6. 在整组里直接改文字、删一行 ==")
    enter_edit(pg, "VITO")
    select_line(pg, "all")
    for i, ln in enumerate(["债券市场比FED还鹰派", "市场在杀融资 - 康宁 CoreWeave", "新加的一句"]):
        if i: pg.keyboard.press("Enter")
        pg.keyboard.type(ln)
    save(pg)
    check("改完是 3 行", lines(pg, "VITO"),
          ["债券市场比FED还鹰派", "市场在杀融资 - 康宁 CoreWeave", "新加的一句"])
    enter_edit(pg, "VITO")
    select_line(pg, 2)
    pg.keyboard.press("Delete"); pg.wait_for_timeout(150)
    save(pg)
    check("删掉一行后剩 2 行", lines(pg, "VITO"), ["债券市场比FED还鹰派", "市场在杀融资 - 康宁 CoreWeave"])

    print("== 7. Esc 撤销 ==")
    enter_edit(pg, "VITO")
    select_line(pg, "all")
    pg.keyboard.type("乱写的")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("内容回到改之前", lines(pg, "VITO"), ["债券市场比FED还鹰派", "市场在杀融资 - 康宁 CoreWeave"])
    check("不再处于编辑态", pg.locator(".tx[contenteditable]").count(), 0)

    print("== 8. 改归属：剪切 / 粘贴跨组搬（没有图标了）==")
    enter_edit(pg, "未署名")
    select_line(pg, "all")
    pg.keyboard.press("Control+x"); pg.wait_for_timeout(250)
    save(pg)
    check("未署名组消失了", "未署名" not in heads(pg))
    enter_edit(pg, "VITO")
    pg.evaluate("""() => { const tx = document.querySelector('.tx[contenteditable]');
      const r = document.createRange(); r.selectNodeContents(tx); r.collapse(false);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    pg.keyboard.press("Control+v"); pg.wait_for_timeout(300)
    save(pg)
    check("那条搬到了 VITO 名下", lines(pg, "VITO"),
          ["债券市场比FED还鹰派", "市场在杀融资 - 康宁 CoreWeave", "期权和树都是空 - 要小心点 - 全员卖C"])
    check("只剩一个作者分组", heads(pg), ["VITO"])

    print("== 9. 查：搜索过滤 + 命中高亮 ==")
    pg.fill("#q", "康宁"); pg.wait_for_timeout(250)
    check("只剩命中的分组", heads(pg), ["VITO"])
    check("只剩命中的行", len(lines(pg)), 1)
    check("命中处高亮", pg.locator("#out mark.hit").count(), 1)
    pg.fill("#q", "不存在的东西"); pg.wait_for_timeout(250)
    check("无结果提示", pg.inner_text("#out").startswith("今天没有含"), True)
    pg.fill("#q", ""); pg.wait_for_timeout(250)
    check("清空搜索后恢复", len(lines(pg)), 3)

    print("== 10. 作者名忽略大小写 ==")
    before = chip_names(pg)
    new_author(pg, "vito")
    check("没有多出一个 chip", chip_names(pg), before)
    check("选中的还是原来那个 VITO", selected(pg), ["VITO"])
    add(pg, "日本鸽派加息 - 日元净多头寸可能会对美国流动性有负面影响")
    check("内容进的是 VITO 组", len(lines(pg, "VITO")), 4)

    print("== 11. 不认识的名字直接记，不提示 ==")
    new_author(pg, "老王")
    add(pg, "看多光模块 但别追高")
    check("老王有自己的分组", "老王" in heads(pg))
    check("没有任何警告文字", "落笺还不认识" not in pg.inner_text("#out"))

    print("== 12. 图片：粘贴 + 在整组里用 [图] 行管理 ==")
    new_author(pg, "Agent")
    pg.evaluate("""async () => {
      const c = document.createElement('canvas'); c.width = 120; c.height = 60;
      const x = c.getContext('2d'); x.fillStyle = '#284b3d'; x.fillRect(0,0,120,60);
      const blob = await new Promise(r => c.toBlob(r, 'image/png'));
      const dt = new DataTransfer();
      dt.items.add(new File([blob], 'shot.png', {type:'image/png'}));
      document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles:true, cancelable:true}));
    }""")
    pg.wait_for_timeout(900)
    check("缩略图出现", pg.locator("#out img.thumb").count(), 1)
    check("缩略图真的解码出来了",
          pg.evaluate("() => { const i = document.querySelector('#out img.thumb'); return !!i && i.naturalWidth > 0; }"))
    enter_edit(pg, "Agent")
    check("编辑态里图片就是一行 [图]", group_text(pg, "Agent"), "[图]")
    select_line(pg, "all")
    pg.keyboard.type("[图]"); pg.keyboard.press("Enter"); pg.keyboard.type("图片下面配一句话")
    save(pg)
    check("图片还在", pg.locator("#out img.thumb").count(), 1)
    check("配文也记上了", lines(pg, "Agent"), ["图片下面配一句话"])

    print("== 13. 刷新：文字 / 重要标记 / 图片都要在 ==")
    pg.reload(); pg.wait_for_selector("#out .grp", timeout=8000)
    pg.wait_for_timeout(400)
    check("作者分组保持", heads(pg), ["VITO", "老王", "Agent"])
    check("图片还在且解码正常",
          pg.evaluate("() => { const i = document.querySelector('#out img.thumb'); return !!i && i.naturalWidth > 0; }"))
    check("VITO 4 条", len(lines(pg, "VITO")), 4)

    print("== 14. 点复制，读真实剪贴板 ==")
    pg.click("#btn-copy"); pg.wait_for_timeout(400)
    copied = pg.evaluate("() => navigator.clipboard.readText()")
    (SHOT / "_last-copy.txt").write_text(copied, encoding="utf-8")
    print("-------- 剪贴板（repr）--------")
    print(repr(copied))
    print("------------------------------")
    check("日期在第一行", copied.splitlines()[0], cn(today))
    check("作者是裸名字", "作者：" not in copied)
    check("VITO 只出现一次", copied.count("VITO"), 1)
    check("没有图标残留", not any(c in copied for c in "→✎×＋"))

    print("== 15. 剪贴板回灌落笺解析器 ==")
    days = parse_notes(copied, fallback_day=date.fromisoformat(today))
    got = [(s.source, s.text) for d in days for s in d.snippets]
    print("  落笺解析出：")
    for src, body in got:
        print(f"      {src} ← {body!r}")
    check("日期行被正确识别", [d.day for d in days], [date.fromisoformat(today)])
    check("VITO 内容完整", got[0][1],
          "债券市场比FED还鹰派\n市场在杀融资 - 康宁 CoreWeave\n期权和树都是空 - 要小心点 - 全员卖C\n"
          "日本鸽派加息 - 日元净多头寸可能会对美国流动性有负面影响")
    check("Agent 内容含图片占位", got[2], ("Agent", "[图]\n图片下面配一句话"))

    print("== 16. 另一窗口从后端读到同一份 ==")
    ctx2 = b.new_context(viewport={"width": 1200, "height": 900})
    pg2 = ctx2.new_page()
    errs2 = []
    pg2.on("pageerror", lambda e: errs2.append(str(e)))
    pg2.goto(HTML)
    pg2.wait_for_selector("#out .grp", timeout=8000)
    pg2.wait_for_timeout(400)
    check("两个窗口作者分组一致", heads(pg2), heads(pg))
    check("两个窗口 VITO 条数一致", len(lines(pg2, "VITO")), 4)
    check("另一窗口图片也在",
          pg2.evaluate("() => { const i = document.querySelector('#out img.thumb'); return !!i && i.naturalWidth > 0; }"))
    check("无 JS 报错", errs + errs2, [])

    print("== 17. 右边删改可以 Ctrl+Z ==")
    new_author(pg, "撤销")
    add(pg, "留下")
    add(pg, "删掉")
    enter_edit(pg, "撤销")
    select_line(pg, 1)
    pg.keyboard.press("Delete")
    pg.wait_for_timeout(150)
    save(pg)
    check("删掉之后只剩一句", lines(pg, "撤销"), ["留下"])
    pg.keyboard.press("Control+z")
    pg.wait_for_function(
        """() => [...document.querySelectorAll('#out .grp')].some(g => g.dataset.author==='撤销' && g.querySelectorAll('.ln .tx').length===2)""",
        timeout=8000,
    )
    check("Ctrl+Z 把删掉的一句找回来", lines(pg, "撤销"), ["留下", "删掉"])
    pg.keyboard.press("Control+Shift+Z")
    pg.wait_for_function(
        """() => [...document.querySelectorAll('#out .grp')].some(g => g.dataset.author==='撤销' && g.querySelectorAll('.ln .tx').length===1)""",
        timeout=8000,
    )
    check("Ctrl+Shift+Z 重做删除", lines(pg, "撤销"), ["留下"])
    enter_edit(pg, "撤销")
    pg.keyboard.type("多")
    pg.wait_for_timeout(100)
    pg.keyboard.press("Control+z")
    pg.wait_for_timeout(150)
    check("编辑中 Ctrl+Z 撤掉刚打的字", group_text(pg, "撤销"), "留下")
    pg.keyboard.press("Escape")

    print("== 18. 网页粘贴去掉颜色和列表 ==")
    new_author(pg, "粘贴")
    add(pg, "原文")
    enter_edit(pg, "粘贴")
    pg.evaluate("""() => {
      const tx = document.querySelector('.tx[contenteditable]');
      const el = tx.querySelector('.eline');
      const n = el.firstChild;
      const r = document.createRange();
      r.setStart(n, n.length); r.collapse(true);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      const dt = new DataTransfer();
      dt.setData('text/html', '<ul><li style="color:red;font-size:30px"><span style="background:yellow">甲</span></li><li><b>乙</b></li></ul>');
      dt.setData('text/plain', '• 甲\\n2. 乙');
      tx.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    }""")
    pg.wait_for_timeout(200)
    check("列表变成纯文字两条", group_text(pg, "粘贴"), "原文\n甲\n乙")
    check("没有颜色、列表、加粗标签", pg.evaluate("""() => {
      const tx = document.querySelector('.tx[contenteditable]');
      return !tx.querySelector('span, ul, li, font, b, strong') && !/color|font-size|background/i.test(tx.innerHTML);
    }"""))
    pg.keyboard.press("Escape")
    check("无 JS 报错", errs + errs2, [])
    b.close()

print("\n=== 结果:", "全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}", "===")
sys.exit(1 if fails else 0)
