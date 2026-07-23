from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DATA_NAMES = (
    "요약본.parquet",
    "packet_active.parquet",
    "packet_completed.parquet",
)
DEFAULT_BRANCH = "data-latest"
DEFAULT_REMOTE = "origin"


def _run(
    args: list[str],
    cwd: Path,
    *,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=capture,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _default_sources(base_dir: Path) -> dict[str, Path | None]:
    parent = base_dir.parent
    work = parent / "ppoo213_work"
    return {
        "요약본.parquet": _first_existing([
            parent / "요약본.parquet",
            work / "요약본.parquet",
            base_dir / "요약본.parquet",
        ]),
        "packet_active.parquet": _first_existing([
            work / "packet_active.parquet",
            base_dir / "packet_active.parquet",
        ]),
        "packet_completed.parquet": _first_existing([
            work / "packet_completed.parquet",
            base_dir / "packet_completed.parquet",
        ]),
    }


def _manifest(paths: dict[str, Path]) -> dict:
    files = {
        name: {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for name, path in paths.items()
    }
    version_source = "\n".join(
        f"{name}:{files[name]['sha256']}" for name in DATA_NAMES
    )
    version = hashlib.sha256(version_source.encode("utf-8")).hexdigest()[:20]
    return {
        "version": version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def _remote_url(repo: Path, remote: str) -> str:
    result = _run(["git", "remote", "get-url", remote], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Git remote 없음: {remote}")
    return result.stdout.strip()


def _remote_manifest_matches(repository: str, branch: str, manifest: dict) -> bool:
    if not repository.startswith("https://github.com/"):
        return False
    slug = repository.removeprefix("https://github.com/").removesuffix(".git")
    try:
        import requests

        response = requests.get(
            f"https://github.com/{slug}/raw/refs/heads/{branch}/manifest.json",
            params={"v": manifest["version"]},
            timeout=(10, 20),
            headers={"Cache-Control": "no-cache"},
        )
        if response.status_code != 200:
            return False
        current = response.json()
        return current.get("version") == manifest["version"]
    except Exception:
        return False


def _resolve_paths(args: argparse.Namespace, base_dir: Path) -> dict[str, Path]:
    defaults = _default_sources(base_dir)
    explicit = {
        "요약본.parquet": args.summary or args.src,
        "packet_active.parquet": args.active,
        "packet_completed.parquet": args.completed,
    }
    result: dict[str, Path] = {}
    for name in DATA_NAMES:
        value = explicit[name]
        path = Path(value).expanduser().resolve() if value else defaults[name]
        if path is None or not path.exists():
            raise FileNotFoundError(f"{name} 원본을 찾지 못했습니다: {path}")
        if path.suffix.lower() != ".parquet":
            raise ValueError(f"Parquet 파일이 아닙니다: {path}")
        result[name] = path
    return result


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="최신 Parquet 3개를 단일 data-latest 스냅샷으로 게시합니다."
    )
    parser.add_argument("--summary", default="", help="요약본.parquet 경로")
    parser.add_argument("--active", default="", help="packet_active.parquet 경로")
    parser.add_argument("--completed", default="", help="packet_completed.parquet 경로")
    parser.add_argument("--src", default="", help="기존 호환: 요약본.parquet 경로")
    parser.add_argument("--target", default="", help=argparse.SUPPRESS)
    parser.add_argument("--message", default="", help=argparse.SUPPRESS)
    parser.add_argument("--no-force-redeploy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--force", action="store_true", help="내용이 같아도 다시 게시")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        paths = _resolve_paths(args, base_dir)
        manifest = _manifest(paths)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    for name, path in paths.items():
        info = manifest["files"][name]
        print(f"[DATA] {name}: {path} ({info['size'] / 1024 / 1024:.2f} MiB)")
    print(f"[VERSION] {manifest['version']}")
    if args.dry_run:
        print("[DRY-RUN] GitHub에 게시하지 않았습니다.")
        return 0

    git_root = _run(["git", "rev-parse", "--show-toplevel"], base_dir)
    if git_root.returncode != 0:
        print("[ERROR] Git 저장소가 아닙니다.", file=sys.stderr)
        return 3
    repo = Path(git_root.stdout.strip())
    try:
        repository = _remote_url(repo, args.remote)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 3

    if not args.force and _remote_manifest_matches(repository, args.branch, manifest):
        print("[SKIP] 원격 데이터가 이미 최신입니다.")
        return 0

    with tempfile.TemporaryDirectory(prefix="ppoo213_data_publish_") as temp:
        snapshot = Path(temp)
        for name, path in paths.items():
            shutil.copy2(path, snapshot / name)
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (snapshot / ".gitattributes").write_text(
            "*.parquet -diff\n",
            encoding="utf-8",
        )

        commands = [
            ["git", "init", "--initial-branch", args.branch],
            ["git", "add", "--", ".gitattributes", "manifest.json", *DATA_NAMES],
            [
                "git",
                "-c", "user.name=ppoo213-data-publisher",
                "-c", "user.email=ppoo213-data@users.noreply.github.com",
                "commit",
                "-m", f"Data snapshot {manifest['version']}",
            ],
            ["git", "remote", "add", args.remote, repository],
            [
                "git",
                "push",
                "--force",
                args.remote,
                f"HEAD:refs/heads/{args.branch}",
            ],
        ]
        for command in commands:
            result = _run(command, snapshot)
            if result.returncode != 0:
                print(result.stdout.strip())
                print(result.stderr.strip(), file=sys.stderr)
                return 3

    print(f"[OK] {args.branch} 브랜치를 최신 스냅샷으로 교체했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
