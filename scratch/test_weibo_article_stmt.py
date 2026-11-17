"""端到端验证文章内容声明设置(选 AI 声明,不点发表)。

用法: backend/.venv/Scripts/python.exe scratch/test_weibo_article_stmt.py
"""
import asyncio
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

        # 复用发布流程同款:调用 _article_set_wb_setting 设置声明(范围=公开)
        await WeiboPlatform()._article_set_wb_setting(page, "内容由AI生成", 0)
        print("SET OK")

        # 校验选中态: extend-box 里 active 项文本
        state = await page.evaluate("""() => {
          const act = document.querySelector('.extend-box__content__item.active');
          return act ? (act.textContent || '').trim() : '';
        }""")
        print("active statement:", state)
        await page.screenshot(path=str(BASE_DIR / "scratch" / "wb_article_stmt_set.png"), full_page=True)
        await context.close()
    finally:
        await close_browser(browser, is_close_by_code=True)


if __name__ == "__main__":
    asyncio.run(main())
