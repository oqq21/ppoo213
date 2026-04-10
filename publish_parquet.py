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


def _git_hash_object(path: Path, cwd: Path) -> str | None:
    res = _run_git(["git", "hash-object", str(path)], cwd)
    if not _git_ok(res):
        return None
    return res.stdout.strip()


def _git_ref_hash(repo: Path, refspec: str) -> str | None:
    res = _run_git(["git", "rev-parse", refspec], repo)
    if not _git_ok(res):
        return None
    return res.stdout.strip()


def _warn_other_same_name_files(target: Path) -> None:
    # Help catch common mistake: replacing a same-name parquet in a nearby folder.
    check_dirs = [target.parent.parent, target.parent.parent.parent]
    try:
        target_hash = _git_hash_object(target, target.parent)
    except Exception:
        target_hash = None
    for d in check_dirs:
        if d is None:
            continue
        alt = d / target.name
        if not alt.exists():
            continue
        if alt.resolve() == target.resolve():
            continue
        alt_hash = _git_hash_object(alt, target.parent)
        if target_hash and alt_hash and target_hash == alt_hash:
            continue
        print("WARNING: same-name parquet found at different path.")
        print(f"  current: {target}")
        print(f"  other:   {alt}")
        print("  If this is the new file, run with --src to publish that file.")
        print(f"  example: python publish_parquet.py --src \"{alt}\"")


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
    parser.add_argument("--target", type=str, default="", help="Target parquet path (default: 요약본.parquet)")
    parser.add_argument("--message", type=str, default="Update parquet", help="Git commit message")
    parser.add_argument(
        "--no-force-redeploy",
        action="store_true",
        help="Do not create empty commit when parquet is unchanged",
    )
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve() if args.target else (base_dir / "요약본.parquet")

    before = target.exists()
    before_size = target.stat().st_size if before else 0
    before_mtime = _fmt_mtime(target) if before else "none"

    copied = False
    if args.src:
        src = Path(args.src).expanduser().resolve()
        if not src.exists():
            print(f"Source not found: {src}", file=sys.stderr)
            return 2
        if src.suffix.lower() != ".parquet":
            print(f"Source is not a parquet file: {src}", file=sys.stderr)
            return 2
        if src != target:
            _copy_atomic(src, target)
            copied = True
    else:
        if target.exists():
            src = target
        else:
            src = _find_default_source(base_dir)
            if src is None:
                print("No source parquet found. Place 요약본.parquet or use --src.", file=sys.stderr)
                return 2
            if src.suffix.lower() != ".parquet":
                print(f"Source is not a parquet file: {src}", file=sys.stderr)
                return 2
            if src != target:
                _copy_atomic(src, target)
                copied = True

    if not target.exists():
        print(f"Target not found: {target}", file=sys.stderr)
        return 2
    if target.suffix.lower() != ".parquet":
        print(f"Target is not a parquet file: {target}", file=sys.stderr)
        return 2

    after_size = target.stat().st_size
    after_mtime = _fmt_mtime(target)

    if copied:
        print(f"Updated: {target}")
        print(f"  from: {src}")
        print(f"  size: {before_size} -> {after_size}")
        print(f"  mtime: {before_mtime} -> {after_mtime}")
    else:
        print(f"Using: {target}")
        print(f"  size: {after_size}")
        print(f"  mtime: {after_mtime}")

    _warn_other_same_name_files(target)

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

    worktree_dir = Path(tempfile.mkdtemp(prefix="publish_worktree_", dir=repo))
    add_wt = _run_git(["git", "worktree", "add", "--detach", str(worktree_dir), remote_ref], repo)
    if not _git_ok(add_wt):
        print("git worktree add failed:", add_wt.stderr.strip(), file=sys.stderr)
        _cleanup_worktree(repo, worktree_dir)
        return 3

    worktree_target = worktree_dir / target_rel
    _copy_atomic(target, worktree_target)

    local_hash = _git_hash_object(target, worktree_dir)
    remote_hash = _git_ref_hash(worktree_dir, f"{remote_ref}:{target_rel.as_posix()}")
    if local_hash:
        print(f"  local-hash:  {local_hash}")
    if remote_hash:
        print(f"  remote-hash: {remote_hash}")

    add_res = _run_git(["git", "add", str(target_rel)], worktree_dir)
    if add_res.returncode != 0:
        print("git add failed:", add_res.stderr.strip(), file=sys.stderr)
        _cleanup_worktree(repo, worktree_dir)
        return 3

    diff_res = _run_git(["git", "diff", "--cached", "--quiet"], worktree_dir)
    if diff_res.returncode == 0:
        if args.no_force_redeploy:
            print("No changes to commit.")
            _cleanup_worktree(repo, worktree_dir)
            return 0
        print("No parquet changes. Creating empty commit for redeploy.")
        commit_res = _run_git(["git", "commit", "--allow-empty", "-m", f"{args.message} (redeploy)"], worktree_dir)
        if commit_res.returncode != 0:
            print("git commit failed:", commit_res.stderr.strip(), file=sys.stderr)
            _cleanup_worktree(repo, worktree_dir)
            return 3
        print(commit_res.stdout.strip())
        push_res = _run_git(["git", "push", "origin", f"HEAD:refs/heads/{branch_name}"], worktree_dir)
        if push_res.returncode != 0:
            print("git push failed:", push_res.stderr.strip(), file=sys.stderr)
            _cleanup_worktree(repo, worktree_dir)
            return 3
        print(push_res.stdout.strip())
        _cleanup_worktree(repo, worktree_dir)
        return 0

    commit_res = _run_git(["git", "commit", "-m", args.message], worktree_dir)
    if commit_res.returncode != 0:
        print("git commit failed:", commit_res.stderr.strip(), file=sys.stderr)
        _cleanup_worktree(repo, worktree_dir)
        return 3
    print(commit_res.stdout.strip())

    push_res = _run_git(["git", "push", "origin", f"HEAD:refs/heads/{branch_name}"], worktree_dir)
    if push_res.returncode != 0:
        print("git push failed:", push_res.stderr.strip(), file=sys.stderr)
        _cleanup_worktree(repo, worktree_dir)
        return 3
    print(push_res.stdout.strip())
    _cleanup_worktree(repo, worktree_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
