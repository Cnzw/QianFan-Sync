#!/usr/bin/env python3
"""抖音视频下载 + 文案提取（CloakBrowser 方案）。

为什么不用 yt-dlp：
    yt-dlp 的 Douyin extractor 直接请求 ``aweme/v1/web/aweme/detail`` 接口，
    但该接口需要 ``a_bogus`` 签名，yt-dlp 未实现（源码里留有
    ``TODO: Run verification challenge code``），实测只会报
    ``ERROR: [Douyin] xxx: Fresh cookies (not necessarily logged in) are needed``。

本脚本的做法：
    用项目自带的 CloakBrowser（隐匿 Chromium）打开视频页，拦截该 detail
    接口的响应，一次拿到「描述 / 话题标签 / 无水印播放地址」，再用
    requests 直接下载 mp4。

用法：
    python scripts/douyin_fetch.py <链接或视频ID> [更多链接/ID ...]
    python scripts/douyin_fetch.py 7705274288967265423 7706830497887866146

    常用参数：
      -o DIR              输出目录（默认 <项目根>/data/douyin_fetch）
      --account-id N      使用 user_info 表中某个抖音账号的登录态
      --cookie-file PATH  直接指定 storage_state JSON（优先于 --account-id）
      --json-only         只抓文案元数据，不下载视频

输出（每个视频一组）：
    <id>.mp4       无水印视频
    <id>.json      原始元数据（含完整接口字段中的常用项）
    <id>.txt       文案（原始描述 / 纯文案 / 标签），可直接复制粘贴
    _summary.md    全部汇总

退出码：0 全部成功，1 有失败项。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_VIDEO_ID_RE = re.compile(r"(\d{19})")


def _prepare_env() -> None:
    """让后端包可导入，并保证 TMP 可写（Playwright 要建临时目录）。"""
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("SAU_DATA_DIR", str(REPO_ROOT / "data"))
    os.environ.setdefault(
        "CLOAKBROWSER_BINARY_PATH",
        str(REPO_ROOT / "dependency" / "cloakbrowser" / "chrome.exe"),
    )
    # 部分环境（沙箱 / 安全软件）下系统 TEMP 不可写，浏览器会报
    # EPERM: operation not permitted, mkdtemp ...\playwright-artifacts-XXXXXX
    tmp_dir = REPO_ROOT / "data" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(tmp_dir)
    os.environ["TMP"] = str(tmp_dir)
    # 后端 logger 打开了 DEBUG，requests/urllib3 的连接日志会刷屏
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def parse_ids(items: list[str]) -> list[str]:
    """把链接或纯 ID 统一成视频 ID，并按出现顺序去重。"""
    ids: list[str] = []
    for raw in items:
        m = _VIDEO_ID_RE.search(raw)
        if not m:
            print(f"[warn] 无法识别为抖音视频: {raw}", file=sys.stderr)
            continue
        vid = m.group(1)
        if vid not in ids:
            ids.append(vid)
    return ids


def resolve_cookie(cookie_file: str | None, account_id: int | None) -> str:
    """返回 storage_state JSON 路径。"""
    if cookie_file:
        path = Path(cookie_file)
        if not path.is_file():
            raise SystemExit(f"cookie 文件不存在: {path}")
        return str(path)

    db_path = Path(os.environ.get("SAU_DATA_DIR", REPO_ROOT / "data")) / "db" / "database.db"
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在: {db_path}（可用 --cookie-file 指定登录态）")

    cookies_dir = Path(os.environ.get("SAU_DATA_DIR", REPO_ROOT / "data")) / "cookiesFile"
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        if account_id:
            rows = conn.execute(
                "SELECT id, filePath FROM user_info WHERE type=3 AND id=?", (account_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, filePath FROM user_info WHERE type=3 AND status=1 ORDER BY id"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise SystemExit("没有可用的抖音账号（user_info.type=3 无记录），请先登录或用 --cookie-file")

    path = cookies_dir / rows[0][1]
    if not path.is_file():
        raise SystemExit(f"账号 cookie 文件缺失: {path}")
    print(f"[info] 使用抖音账号 id={rows[0][0]} 登录态: {path.name}", file=sys.stderr)
    return str(path)


def extract_tags(detail: dict) -> list[str]:
    """优先取接口的 text_extra，兜底从描述里正则抓 #话题。"""
    tags = [
        t.get("hashtag_name")
        for t in (detail.get("text_extra") or [])
        if isinstance(t, dict) and t.get("hashtag_name")
    ]
    if tags:
        return tags
    desc = detail.get("desc") or ""
    return [m.group(1) for m in re.finditer(r"#([^\s#]+)", desc)]


def plain_text(desc: str) -> str:
    """去掉 #话题 后的纯文案。"""
    text = re.sub(r"#\S+", "", desc or "")
    return re.sub(r"\s+", " ", text).strip()


async def grab_details(cookie_path: str, ids: list[str], want_video: bool) -> list[dict]:
    from impl._browser import close_browser, create_browser  # noqa: E402

    results: list[dict] = []
    browser = await create_browser(headless=True)
    ctx = await browser.new_context(storage_state=cookie_path)
    try:
        for idx, vid in enumerate(ids, 1):
            print(f"[{idx}/{len(ids)}] {vid}", file=sys.stderr, flush=True)
            page = await ctx.new_page()
            hit: dict = {}

            async def _on_resp(resp, hit=hit):
                if "aweme/v1/web/aweme/detail" not in resp.url:
                    return
                try:
                    data = await resp.json()
                except Exception:
                    return
                detail = data.get("aweme_detail")
                if detail:
                    hit["detail"] = detail

            def _handler(resp):
                asyncio.create_task(_on_resp(resp))

            page.on("response", _handler)
            detail: dict | None = None
            try:
                for attempt in (1, 2):
                    await page.goto(
                        f"https://www.douyin.com/video/{vid}",
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    for _ in range(25):
                        if hit:
                            break
                        await asyncio.sleep(1)
                    if hit:
                        detail = hit["detail"]
                        break
                    if attempt == 1:
                        print(f"  第 1 次未拿到详情，重试…", file=sys.stderr, flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  抓取异常: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr, flush=True)
            finally:
                page.remove_listener("response", _handler)
                await page.close()

            if not detail:
                results.append({"id": vid, "ok": False, "reason": "detail not captured"})
                continue

            video = detail.get("video") or {}
            meta = {
                "id": vid,
                "desc": (detail.get("desc") or "").strip(),
                "tags": extract_tags(detail),
                "author": (detail.get("author") or {}).get("nickname") or "",
                "duration_ms": video.get("duration"),
                "web_url": f"https://www.douyin.com/video/{vid}",
                "statistics": detail.get("statistics") or {},
                "play_urls": (video.get("play_addr") or {}).get("url_list") or [],
            }
            results.append({"ok": True, "meta": meta})
    finally:
        await ctx.close()
        await close_browser(browser)
    return results


def download(urls: list[str], dest: Path) -> int:
    """依次尝试候选地址，返回落盘字节数（失败为 0）。"""
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
    for url in urls[:3]:
        tmp = dest.with_suffix(".part")
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as fp:
                    for chunk in resp.iter_content(256 * 1024):
                        if chunk:
                            fp.write(chunk)
            size = tmp.stat().st_size
            if size < 1024:
                tmp.unlink(missing_ok=True)
                continue
            tmp.replace(dest)
            return size
        except Exception as exc:  # noqa: BLE001
            print(f"  下载失败: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
    return 0


def write_text_files(out_dir: Path, meta: dict, size: int) -> list[str]:
    """写出 <id>.txt，并返回同样的文案行（供汇总复用）。"""
    desc = meta["desc"]
    tags = meta["tags"]
    plain = plain_text(desc)
    tag_line = " ".join("#" + t for t in tags) if tags else "（无）"
    minutes, seconds = divmod(round((meta.get("duration_ms") or 0) / 1000), 60)
    lines = [
        f"文件：{meta['id']}.mp4（{size / 1048576:.1f} MB）",
        f"作者：{meta['author']}",
        f"时长：{minutes}分{seconds}秒",
        f"链接：{meta['web_url']}",
        f"原始描述：{desc}",
        f"纯文案：{plain}",
        f"标签：{tag_line}",
    ]
    (out_dir / f"{meta['id']}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="抖音视频下载 + 文案提取（CloakBrowser）")
    ap.add_argument("targets", nargs="+", help="抖音视频链接或 19 位视频 ID")
    ap.add_argument("-o", "--out", default=str(REPO_ROOT / "data" / "douyin_fetch"))
    ap.add_argument("--account-id", type=int, default=None)
    ap.add_argument("--cookie-file", default=None)
    ap.add_argument("--json-only", action="store_true", help="只抓文案，不下载视频")
    args = ap.parse_args()

    _prepare_env()
    ids = parse_ids(args.targets)
    if not ids:
        print("没有可用的视频 ID", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = resolve_cookie(args.cookie_file, args.account_id)

    started = time.time()
    results = asyncio.run(grab_details(cookie_path, ids, not args.json_only))

    summary_lines = ["# 抖音素材清单", ""]
    failed: list[str] = []
    for idx, item in enumerate(results, 1):
        if not item.get("ok"):
            failed.append(item["id"])
            summary_lines.append(f"## {idx}. {item['id']}  ❌ {item.get('reason', '失败')}")
            summary_lines.append("")
            continue

        meta = item["meta"]
        (out_dir / f"{meta['id']}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        size = 0
        if not args.json_only and meta["play_urls"]:
            size = download(meta["play_urls"], out_dir / f"{meta['id']}.mp4")
            if not size:
                failed.append(meta["id"])
        txt_lines = write_text_files(out_dir, meta, size)
        summary_lines.append(f"## {idx}. {meta['id']}")
        summary_lines.extend(f"- {line}" for line in txt_lines)
        summary_lines.append("")
        print(
            f"  ok  desc={meta['desc'][:40]!r} tags={len(meta['tags'])} "
            f"size={size / 1048576:.1f}MB",
            file=sys.stderr,
            flush=True,
        )

    summary_lines.insert(1, f"共 {len(ids)} 条，失败 {len(failed)} 条，用时 {time.time() - started:.0f}s\n")
    (out_dir / "_summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"\n输出目录: {out_dir}")
    if failed:
        print(f"失败: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
