"""踩点:文章发布弹窗「内容声明」子菜单的真实 DOM/文案。

复用 impl helper 打开发布弹窗 → hover 公开项 → dump extend-box 全部文本与 HTML。
不点发布,不触发风控。

用法: backend/.venv/Scripts/python.exe scratch/recon_weibo_article_stmt.py
"""
import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from impl._browser import create_browser, close_browser, create_context  # noqa: E402
from impl.weibo.platform import WeiboPlatform  # noqa: E402

COOKIE = str(BASE_DIR / "data" / "cookiesFile" / "73c2f7af-db41-11f1-b4c1-bc24114cad59.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.4324.150 Safari/537.36")


async def main() -> None:
    browser = await create_browser(headless=False)
    try:
        context = await create_context(browser, storage_state=COOKIE, user_agent=UA)
        page = await context.new_page()

        # 1. 打开文章编辑器草稿列表
        await page.goto(
            "https://card.weibo.com/article/v5/editor#/draft",
            wait_until="domcontentloaded", timeout=60000,
        )
        await asyncio.sleep(6)
        print("url:", page.url)

        # 2. 打开第一篇草稿进编辑器(找 编辑/继续编辑 按钮)
        for label in ("编辑", "继续编辑"):
            btn = page.locator(f"text={label}").first
            if await btn.count():
                await btn.click()
                print("clicked:", label)
                break
        await asyncio.sleep(6)
        print("editor url:", page.url)

        # 3. 点编辑器右上「发布」/「发表」打开弹窗
        for label in ("发布", "发表", "下一步"):
            btn = page.get_by_text(label, exact=True).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                print("clicked publish:", label)
                break
        await asyncio.sleep(4)

        # 4. hover 范围触发器展开下拉
        trig = await WeiboPlatform._article_modal_wb_trigger(page)
        print("trigger:", trig)
        if not trig:
            await page.screenshot(path=str(BASE_DIR / "scratch" / "wb_article_no_modal.png"), full_page=True)
            print("saved scratch/wb_article_no_modal.png")
            return
        await page.mouse.move(trig["x"], trig["y"])
        await asyncio.sleep(1.5)
        visible = await WeiboPlatform._article_dropdown_visible(page)
        print("dropdown visible:", visible)

        # 5. dump 下拉项 + hover 公开项展开子菜单
        items = await page.evaluate("""() => {
          const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          return [...document.querySelectorAll('.publish-dropdown__item')]
            .filter(vis).map(e => (e.innerText || '').trim().slice(0, 30));
        }""")
        print("dropdown items:", json.dumps(items, ensure_ascii=False))

        entry = await WeiboPlatform._article_dropdown_extend_rect(page, "公开")
        print("extend entry:", entry)
        if entry:
            await page.mouse.move(entry["x"], entry["y"])
            await asyncio.sleep(1.5)
            dump = await page.evaluate("""() => {
              const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const boxes = [...document.querySelectorAll('[class*="extend"], [class*="submenu"], [class*="sub-menu"], [class*="level"]')]
                .filter(vis);
              const texts = [];
              boxes.forEach(b => {
                [...b.querySelectorAll('li, div, span')].forEach(e => {
                  if (e.children.length === 0) {
                    const t = (e.textContent || '').trim();
                    if (t && t.length <= 30 && !texts.includes(t)) texts.push(t);
                  }
                });
              });
              const first = boxes[0];
              return {texts, cls: first ? (first.className || '').toString() : '', html: first ? first.outerHTML.replace(/\\s+/g, ' ').slice(0, 1500) : ''};
            }""")
            print("submenu texts:", json.dumps(dump["texts"], ensure_ascii=False))
            print("submenu cls:", dump["cls"])
            print("submenu html:", dump["html"][:1200])

        await page.screenshot(path=str(BASE_DIR / "scratch" / "wb_article_stmt_recon.png"), full_page=True)
        print("saved scratch/wb_article_stmt_recon.png")
        await context.close()
    finally:
        await close_browser(browser, is_close_by_code=True)


if __name__ == "__main__":
    asyncio.run(main())
