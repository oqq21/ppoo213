from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
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
        cwd / "sales_10000.parquet",
        cwd / "data.parquet",
        base_dir / "요약본.parquet",
        base_dir / "sales_10000.parquet",
        base_dir / "data.parquet",
        base_dir.parent / "요약본.parquet",
        base_dir.parent / "sales_10000.parquet",
        base_dir.parent / "data.parquet",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True)


def _git_ok(res: subprocess.CompletedProcess) -> bool:
    return res.returncode == 0


def _pick_remote_branch(repo: Path, preferred: str) -> tuple[str, str] | None:
    fetch_res = _run_git(["git", "fetch", "origin"], repo)
    if not _git_ok(fetch_res):
        print("git fetch failed:", fetch_res.stderr.strip(), file=sys.stderr)
        return None

    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(["main", "master"])

    for name in candidates:
        ref = f"origin/{name}"
        verify = _run_git(["git", "rev-parse", "--verify", ref], repo)
        if _git_ok(verify):
            return name, ref
    return None


def _cleanup_worktree(repo: Path, worktree: Path) -> None:
    _run_git(["git", "worktree", "remove", "--force", str(worktree)], repo)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Update parquet in repo and push to Streamlit Cloud.")
    parser.add_argument("--src", type=str, default="", help="Source parquet path (relative ok)")
    parser.add_argument("--target", type=str, default="", help="Target parquet path (default: web_app/요약본.parquet)")
    parser.add_argument("--message", type=str, default="Update parquet", help="Git commit message")
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

    # git add/commit/push
    git_root = _run_git(["git", "rev-parse", "--show-toplevel"], base_dir)
    if git_root.returncode != 0:
        print("Git repo not found. Commit/push skipped.", file=sys.stderr)
        print(git_root.stderr.strip(), file=sys.stderr)
        return 3
    repo = Path(git_root.stdout.strip())

    try:
        target_rel = target.relative_to(repo)
    except ValueError:
        print("Target must be inside the git repo to commit.", file=sys.stderr)
        return 3

    branch_res = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    preferred_branch = branch_res.stdout.strip() if _git_ok(branch_res) else ""
    if preferred_branch in ("", "HEAD"):
        preferred_branch = "main"

    branch_info = _pick_remote_branch(repo, preferred_branch)
    if branch_info is None:
        print("Remote branch not found (origin/main or origin/master).", file=sys.stderr)
        return 3
    branch_name, remote_ref = branch_info

    worktree_dir = Path(tempfile.mkdtemp(prefix="publish_worktree_"))
    try:
        add_wt = _run_git(["git", "worktree", "add", "--detach", str(worktree_dir), remote_ref], repo)
        if not _git_ok(add_wt):
            print("git worktree add failed:", add_wt.stderr.strip(), file=sys.stderr)
            return 3

        checkout_res = _run_git(["git", "checkout", "-B", branch_name, remote_ref], worktree_dir)
        if not _git_ok(checkout_res):
            print("git checkout failed:", checkout_res.stderr.strip(), file=sys.stderr)
            return 3

        worktree_target = worktree_dir / target_rel
        _copy_atomic(target, worktree_target)

        add_res = _run_git(["git", "add", str(target_rel)], worktree_dir)
        if add_res.returncode != 0:
            print("git add failed:", add_res.stderr.strip(), file=sys.stderr)
            return 3

        diff_res = _run_git(["git", "diff", "--cached", "--quiet"], worktree_dir)
        if diff_res.returncode == 0:
            print("No changes to commit.")
            return 0

        commit_res = _run_git(["git", "commit", "-m", args.message], worktree_dir)
        if commit_res.returncode != 0:
            print("git commit failed:", commit_res.stderr.strip(), file=sys.stderr)
            return 3
        print(commit_res.stdout.strip())

        push_res = _run_git(["git", "push", "origin", branch_name], worktree_dir)
        if push_res.returncode != 0:
            print("git push failed:", push_res.stderr.strip(), file=sys.stderr)
            return 3
        print(push_res.stdout.strip())
        return 0
    finally:
        _cleanup_worktree(repo, worktree_dir)


if __name__ == "__main__":
    raise SystemExit(main())
