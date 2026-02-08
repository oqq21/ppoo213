from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _fmt_mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)


def _find_default_source(base_dir: Path) -> Path | None:
    cwd = Path.cwd()
    candidates = [
        cwd / "요약본.parquet",
        base_dir / "요약본.parquet",
        base_dir.parent / "요약본.parquet",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Update parquet file used by the web app.")
    parser.add_argument("--src", type=str, default="", help="Source parquet path")
    parser.add_argument("--target", type=str, default="", help="Target parquet path (default: web_app/요약본.parquet)")
    args = parser.parse_args()

    src = Path(args.src).expanduser().resolve() if args.src else _find_default_source(base_dir)
    if src is None:
        print("No source parquet found. Use --src to specify a file.", file=sys.stderr)
        return 2
    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        return 2
    if src.suffix.lower() != ".parquet":
        print(f"Source is not a parquet file: {src}", file=sys.stderr)
        return 2

    target = Path(args.target).expanduser().resolve() if args.target else (base_dir / "요약본.parquet")

    before = target.exists()
    before_size = target.stat().st_size if before else 0
    before_mtime = _fmt_mtime(target) if before else "none"

    _copy_atomic(src, target)

    after_size = target.stat().st_size
    after_mtime = _fmt_mtime(target)

    print(f"Updated: {target}")
    print(f"  from: {src}")
    print(f"  size: {before_size} -> {after_size}")
    print(f"  mtime: {before_mtime} -> {after_mtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
