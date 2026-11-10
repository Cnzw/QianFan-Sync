"""精简踩点:只 dump extend-box 内 item 的真实 class 名。"""
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
        await page.goto(
            "https://card.weibo.com/article/v5/editor#/draft/3993375",
            wait_until="domcontentloaded", timeout=60000,
        )
        await asyncio.sleep(8)
        for label in ("发布", "发表", "下一步"):
            btn = page.get_by_text(label, exact=True).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                break
        await asyncio.sleep(4)
        trig = await WeiboPlatform._article_modal_wb_trigger(page)
        await page.mouse.move(trig["x"], trig["y"])
        await asyncio.sleep(1.5)
        entry = await WeiboPlatform._article_dropdown_extend_rect(page, "公开")
        await page.mouse.move(entry["x"], entry["y"])
        await asyncio.sleep(1.5)

        info = await page.evaluate("""() => {
          const items = [...document.querySelectorAll('.extend-box__content__item')];
          const anyLeaf = [...document.querySelectorAll('.extend-box *')]
            .filter(e => e.children.length === 0 && (e.textContent || '').trim());
          return {
            contentItemCount: items.length,
            itemClasses: items.map(e => (e.className || '').toString()),
            leafClasses: anyLeaf.map(e => ({
              cls: (e.className || '').toString(),
              text: (e.textContent || '').trim().slice(0, 20),
            })),
          };
        }""")
        print(json.dumps(info, ensure_ascii=False, indent=1)[:1800])
        await context.close()
    finally:
        await close_browser(browser, is_close_by_code=True)


if __name__ == "__main__":
    asyncio.run(main())
