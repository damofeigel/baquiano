"""Writes each run's FinderResult to its own file so runs never overwrite each other."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from src.core import config

from .models import FinderResult


def fund_slug(url: str) -> str:
    """Filesystem-safe name for a fund, from its URL's domain."""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    host = host.split("@")[-1].split(":")[0].removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.") or "fund"


def write_result(result: FinderResult, fund_url: str, out_dir: Path | None = None) -> Path:
    """Write result to <out_dir>/<fund-domain>_<YYYYmmdd-HHMMSS>.json and return the path.
    """
    out_dir = out_dir or config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{fund_slug(fund_url)}_{datetime.now():%Y%m%d-%H%M%S}"
    for n in range(1, 1000):
        path = out_dir / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=2))
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not find a free output filename for {stem}")
