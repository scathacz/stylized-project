# -*- coding: utf-8 -*-
"""回车换行，Ctrl+Enter 收进右边。框选后先放进检索窗，写下问题再去查。"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\codex测试\desk-notes")
sys.path.insert(0, r"D:\codex测试\desk-notes\prototype")

from playwright.sync_api import sync_playwright

from _http import start_note_server

HTML, _DATA = start_note_server()
fails = []
hits = {"n": 0}


def check(name, got, want=True):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + name)
    if not ok:
        print(f"         got={got!r}\n        want={want!r}")
        fails.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(HTML)

    def fulfill(route):
        hits["n"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "text": "利差大约两个点",
                "queries": ["美日利差"],
                "urls": [{"title": "财政部", "url": "https://example.com/spread"}],
                "model": "deepseek-v4.1-flash",
                "provider": "geili",
                "note": "",
                "question": "现在利差多少",
                "context": "美日十年利差现在多少",
                "id": hits["n"],
                "created_at": "2026-09-22T10:00:00+08:00",
            }, ensure_ascii=False),
        )

    page.route("**/api/notes/lookup", fulfill)
    page.fill("#input", "美日十年利差现在多少")
    page.press("#input", "Enter")
    page.wait_for_timeout(250)
    check("回车只换行，不收进右边", page.locator("#out .ln").count(), 0)
    check("回车留在输入框里", "\n" in page.locator("#input").input_value())
    page.evaluate("""() => {
      const el = document.querySelector('#input');
      el.focus();
      el.setSelectionRange(0, '美日十年利差现在多少'.length);
    }""")
    page.press("#input", "Alt+Enter")
    page.wait_for_selector("#dock-q")
    page.wait_for_timeout(250)
    box = page.locator("#dock").bounding_box()
    viewport = page.viewport_size
    check("检索窗从中线铺到右边", abs(box["x"] - viewport["width"] / 2) <= 8)
    check("检索窗占半屏宽", abs(box["width"] - viewport["width"] / 2) <= 8)
    check("检索窗拉到屏幕下沿", box["y"] + box["height"] >= viewport["height"] - 2)
    page.set_viewport_size({"width": 800, "height": 700})
    page.wait_for_timeout(100)
    narrow = page.locator("#dock").bounding_box()
    check("窄窗口铺满宽度", narrow["width"] >= 790)
    check("窄窗口不是矮条", narrow["height"] >= 600)
    page.set_viewport_size({"width": 1440, "height": 900})
    check("框选后不立刻检索", hits["n"], 0)
    check("材料进了窗", page.locator(".dock-piece").inner_text(), "美日十年利差现在多少")
    check("等着输入问题", page.locator("#dock-q").count(), 1)
    check("左边草稿还在", page.locator("#input").input_value().startswith("美日十年利差现在多少"))
    page.fill("#dock-q", "现在利差多少")
    page.press("#dock-q", "Enter")
    page.wait_for_selector(".dock-ans")
    check("写下问题才去搜", hits["n"], 1)
    check("发出去后输入框清空", page.locator("#dock-q").input_value(), "")
    check("答案在窗里", page.locator(".dock-ans").inner_text(), "利差大约两个点")
    check("来源链接在", page.locator(".dock-src a").inner_text(), "财政部")
    check("检索没有写进右边", page.locator("#out .ln").count(), 0)
    page.locator("#input").click()
    page.press("#input", "Control+Enter")
    page.wait_for_function(
        """() => [...document.querySelectorAll('#out .ln .tx')].some(e => (e.textContent || '').includes('美日十年利差'))"""
    )
    check("Ctrl+Enter 才收进右边", page.locator("#out .ln").count() > 0)
    page.locator("[data-act=fold]").click()
    check("折叠后窗收起", page.locator("#dock").is_hidden())
    check("边上留着标签", page.locator("#dock-tab").is_visible())
    check("折叠标签是问题概括", page.locator("#dock-tab").inner_text(), "现在利差多少")
    page.locator("#dock-tab").click()
    page.wait_for_selector("#dock-q")
    check("点标签展开", page.locator(".dock-ans").inner_text(), "利差大约两个点")
    page.locator("[data-act=close]").click()
    check("关掉后窗不见了", page.locator("#dock").is_hidden())
    check("关掉后标签也不见", page.locator("#dock-tab").is_hidden())
    page.locator("#btn-talks").click()
    page.wait_for_selector(".talk")
    check("概览是发送材料的缩略", "美日十年利差" in page.locator(".talk-piece").first.inner_text())
    check("右上角能看见这次对话", "现在利差多少" in page.locator(".talk").first.inner_text())
    page.locator(".talk").first.click()
    page.wait_for_selector(".dock-ask")
    check("点开能看问题和回答", page.locator(".dock-ask").inner_text(), "现在利差多少")
    check("历史里留着材料", "美日十年利差现在多少" in page.locator(".dock-piece").inner_text())
    page.locator("[data-act=close]").click()

    def pick(text):
        page.fill("#input", text)
        page.evaluate(
            """(t) => { const el = document.querySelector('#input'); el.focus(); el.setSelectionRange(0, t.length); }""",
            text,
        )
        page.press("#input", "Alt+Enter")
        page.wait_for_selector("#dock-q")

    pick("美日十年利差现在多少")
    page.locator("[data-act=fold]").click()
    pick("黄金今晚怎么看")
    page.locator("[data-act=fold]").click()
    labels = [t.strip() for t in page.locator(".dock-tab").all_inner_texts()]
    check("两条折叠窗都能分开", labels, ["美日十年利差现在多少", "黄金今晚怎么看"])
    browser.close()

print("全部通过" if not fails else f"失败 {len(fails)} 项")
sys.exit(1 if fails else 0)
