#!/usr/bin/env python3
"""
snapshot_pages.py — Save each fund's portfolio page, so every test reads the same thing.

Usage:
    python evaluation/snapshot_pages.py                     # every fund in funds.json with no copy yet
    python evaluation/snapshot_pages.py --fund sosv --force # save this one again

Saves the page as the browser sees it after a little scrolling, with its scripts
removed, plus the site's homepage for a model that starts there. A long list is
kept short on purpose: the answer key covers the saved copy, and a person has to
check it. The copies don't update with the sites: that is the point.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from common import DETECTION_DIR, FUNDS_FILE

MAX_SCROLLS, MAX_CLICKS = 2, 1


def strip_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script\s*>", "", html, flags=re.IGNORECASE | re.DOTALL)


def page_key(url: str) -> str:
    """A page's identity, whatever way a link writes it: no scheme, www., fragment or final slash."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    return host + parts.path.rstrip("/") + (f"?{parts.query}" if parts.query else "")


def save_fund(browser, fund: dict, fund_dir: Path) -> dict:
    (fund_dir / "pages").mkdir(parents=True, exist_ok=True)
    pages: dict[str, str] = {}

    def save(url: str, name: str) -> None:
        browser.go_to(url)
        browser.expand_listing(max_scrolls=MAX_SCROLLS, max_clicks=MAX_CLICKS)
        (fund_dir / "pages" / name).write_text(strip_scripts(browser.get_page_content()), encoding="utf-8")
        pages[page_key(url)] = name
        pages[page_key(browser.page.url)] = name

    save(fund["url"], "portfolio.html")
    parts = urlsplit(browser.page.url)
    home = f"{parts.scheme}://{parts.netloc}/"
    if page_key(home) not in pages:
        try:
            save(home, "home.html")
        except Exception as exc:
            print(f"  {fund['id']}: no homepage saved ({type(exc).__name__})")
    meta = {"id": fund["id"], "url": fund["url"], "why": fund["why"], "pages": pages, "reviewed": False,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (fund_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Save funds' portfolio pages for the evaluation.")
    parser.add_argument("--fund", nargs="+", help="Only these funds (ids from funds.json)")
    parser.add_argument("--force", action="store_true", help="Save again, even if a copy exists")
    args = parser.parse_args()

    from src.core.browser import BrowserManager

    funds = json.loads(FUNDS_FILE.read_text(encoding="utf-8"))
    with BrowserManager() as browser:
        for fund in funds:
            fund_dir = DETECTION_DIR / fund["id"]
            if args.fund and fund["id"] not in args.fund:
                continue
            if (fund_dir / "meta.json").exists() and not args.force:
                continue
            try:
                meta = save_fund(browser, fund, fund_dir)
                print(f"{fund['id']}: saved {sorted(set(meta['pages'].values()))}")
            except Exception as exc:
                print(f"{fund['id']}: FAILED {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")


if __name__ == "__main__":
    main()
