from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


DATA_NAMES = (
    "요약본.parquet",
    "packet_active.parquet",
    "packet_completed.parquet",
    "gem_prices.json",
)
ASSET_PREFIXES = {
    "요약본.parquet": "summary",
    "packet_active.parquet": "packet-active",
    "packet_completed.parquet": "packet-completed",
    "gem_prices.json": "gem-prices",
}
DEFAULT_REMOTE = "origin"
DEFAULT_TAG = "web-data-latest"
DEFAULT_TARGET = "main"
API_ROOT = "https://api.github.com"
STABLE_MANIFEST_NAME = "manifest.json"


def _run(args: list[str], cwd: Path, *, input_text: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        input=input_text or None,
        text=True,
        capture_output=True,
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
    github_dir = base_dir.parent
    workspace = github_dir.parent
    return {
        "요약본.parquet": _first_existing([
            workspace / "자료" / "요약본.parquet",
            base_dir / "요약본.parquet",
        ]),
        "packet_active.parquet": _first_existing([
            base_dir / "packet_active.parquet",
        ]),
        "packet_completed.parquet": _first_existing([
            base_dir / "packet_completed.parquet",
        ]),
        "gem_prices.json": _first_existing([
            base_dir / "gem_prices.json",
        ]),
    }


def _manifest(paths: dict[str, Path]) -> dict:
    files = {}
    for name, path in paths.items():
        sha = _sha256(path)
        files[name] = {
            "size": path.stat().st_size,
            "sha256": sha,
            "asset": f"{ASSET_PREFIXES[name]}-{sha[:16]}{path.suffix.lower()}",
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


def _repository_slug(remote_url: str) -> str:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        slug = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            raise ValueError(f"GitHub remote가 아닙니다: {remote_url}")
        slug = parsed.path.lstrip("/")
    slug = slug.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        raise ValueError(f"GitHub 저장소 주소를 해석하지 못했습니다: {remote_url}")
    return slug


def _github_token(repo: Path) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    result = _run(
        ["git", "credential", "fill"],
        repo,
        input_text="protocol=https\nhost=github.com\n\n",
    )
    if result.returncode == 0:
        values = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        if values.get("password"):
            return values["password"]
    raise RuntimeError(
        "GitHub 인증정보가 없습니다. GITHUB_TOKEN 환경변수 또는 Git credential이 필요합니다."
    )


def _session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ppoo213-data-publisher",
    })
    return session


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    attempts = 1 if method.upper() == "POST" else 4
    for attempt in range(attempts):
        try:
            response = session.request(method, url, timeout=(15, 300), **kwargs)
        except requests.RequestException:
            if attempt + 1 >= attempts:
                raise
            time.sleep((2, 5, 15)[attempt])
            continue
        retryable = response.status_code in {429, 500, 502, 503, 504}
        if retryable and attempt + 1 < attempts:
            wait = int(response.headers.get("Retry-After") or (2, 5, 15)[attempt])
            time.sleep(min(60, max(1, wait)))
            continue
        if response.status_code >= 400:
            message = response.text[:500].replace("\n", " ")
            raise RuntimeError(f"GitHub API {method} {response.status_code}: {message}")
        return response
    raise RuntimeError(f"GitHub API {method} 재시도 실패")


def _ensure_release(
    session: requests.Session,
    slug: str,
    tag: str,
    target: str,
) -> dict:
    url = f"{API_ROOT}/repos/{slug}/releases/tags/{tag}"
    response = session.get(url, timeout=(15, 30))
    if response.status_code == 200:
        return response.json()
    if response.status_code != 404:
        raise RuntimeError(f"GitHub Release 조회 실패 {response.status_code}: {response.text[:500]}")
    return _request(
        session,
        "POST",
        f"{API_ROOT}/repos/{slug}/releases",
        json={
            "tag_name": tag,
            "target_commitish": target,
            "name": "Latest web data",
            "body": "Streamlit 최신 데이터 전용 Release",
            "draft": False,
            "prerelease": False,
        },
    ).json()


def _upload_asset(
    session: requests.Session,
    release: dict,
    name: str,
    path: Path,
    content_type: str,
) -> dict:
    upload_url = str(release["upload_url"]).split("{", 1)[0]
    with path.open("rb") as source:
        response = _request(
            session,
            "POST",
            upload_url,
            params={"name": name},
            headers={"Content-Type": content_type},
            data=source,
        )
    return response.json()


def _delete_asset(session: requests.Session, slug: str, asset_id: int) -> None:
    _request(
        session,
        "DELETE",
        f"{API_ROOT}/repos/{slug}/releases/assets/{asset_id}",
    )


def _resolve_paths(args: argparse.Namespace, base_dir: Path) -> dict[str, Path]:
    defaults = _default_sources(base_dir)
    explicit = {
        "요약본.parquet": args.summary or args.src,
        "packet_active.parquet": args.active,
        "packet_completed.parquet": args.completed,
        "gem_prices.json": args.gem_prices,
    }
    result: dict[str, Path] = {}
    for name in DATA_NAMES:
        value = explicit[name]
        path = Path(value).expanduser().resolve() if value else defaults[name]
        if path is None or not path.exists():
            raise FileNotFoundError(f"{name} 원본을 찾지 못했습니다: {path}")
        expected_suffix = ".json" if name == "gem_prices.json" else ".parquet"
        if path.suffix.lower() != expected_suffix:
            raise ValueError(f"파일 형식이 올바르지 않습니다: {path}")
        result[name] = path
    return result


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="최신 Parquet 3개를 GitHub Release 자산으로 교체합니다."
    )
    parser.add_argument("--summary", default="", help="요약본.parquet 경로")
    parser.add_argument("--active", default="", help="packet_active.parquet 경로")
    parser.add_argument("--completed", default="", help="packet_completed.parquet 경로")
    parser.add_argument("--gem-prices", default="", help="gem_prices.json 경로")
    parser.add_argument("--src", default="", help="기존 호환: 요약본.parquet 경로")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="Release 태그 기준 브랜치")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--force", action="store_true", help="내용이 같아도 manifest 다시 게시")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--message", default="", help=argparse.SUPPRESS)
    parser.add_argument("--no-force-redeploy", action="store_true", help=argparse.SUPPRESS)
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
        slug = _repository_slug(_remote_url(repo, args.remote))
        session = _session(_github_token(repo))
        release = _ensure_release(session, slug, args.tag, args.target)
        assets = {
            str(asset["name"]): asset
            for asset in release.get("assets", [])
            if isinstance(asset, dict) and asset.get("name")
        }

        for logical_name in DATA_NAMES:
            info = manifest["files"][logical_name]
            asset_name = str(info["asset"])
            existing = assets.get(asset_name)
            if existing and int(existing.get("size") or -1) == int(info["size"]):
                print(f"[REUSE] {asset_name}")
                continue
            if existing:
                _delete_asset(session, slug, int(existing["id"]))
            assets[asset_name] = _upload_asset(
                session,
                release,
                asset_name,
                paths[logical_name],
                "application/json" if logical_name.endswith(".json") else "application/octet-stream",
            )
            print(f"[UPLOAD] {asset_name}")

        manifest_name = f"manifest-{manifest['version']}.json"
        current_pointer = f"current_manifest={manifest_name}"
        if (
            not args.force
            and current_pointer in str(release.get("body") or "")
            and manifest_name in assets
            and STABLE_MANIFEST_NAME in assets
        ):
            keep_names = {
                manifest_name,
                STABLE_MANIFEST_NAME,
                *(str(info["asset"]) for info in manifest["files"].values()),
            }
            for name, asset in list(assets.items()):
                if name not in keep_names:
                    _delete_asset(session, slug, int(asset["id"]))
                    print(f"[CLEAN] {name}")
            print("[SKIP] GitHub Release 데이터가 이미 최신입니다.")
            return 0

        with tempfile.TemporaryDirectory(prefix="ppoo213_release_manifest_") as temp:
            manifest_path = Path(temp) / manifest_name
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            existing_manifest = assets.get(manifest_name)
            if existing_manifest:
                _delete_asset(session, slug, int(existing_manifest["id"]))
            assets[manifest_name] = _upload_asset(
                session,
                release,
                manifest_name,
                manifest_path,
                "application/json",
            )
            print(f"[UPLOAD] {manifest_name}")
            existing_stable_manifest = assets.get(STABLE_MANIFEST_NAME)
            if existing_stable_manifest:
                _delete_asset(
                    session,
                    slug,
                    int(existing_stable_manifest["id"]),
                )
            assets[STABLE_MANIFEST_NAME] = _upload_asset(
                session,
                release,
                STABLE_MANIFEST_NAME,
                manifest_path,
                "application/json",
            )
            print(f"[UPLOAD] {STABLE_MANIFEST_NAME}")

        _request(
            session,
            "PATCH",
            f"{API_ROOT}/repos/{slug}/releases/{release['id']}",
            json={"body": f"Streamlit 최신 데이터 전용 Release\n\n{current_pointer}"},
        )

        keep_names = {
            manifest_name,
            STABLE_MANIFEST_NAME,
            *(str(info["asset"]) for info in manifest["files"].values()),
        }
        for name, asset in list(assets.items()):
            if name not in keep_names:
                _delete_asset(session, slug, int(asset["id"]))
                print(f"[CLEAN] {name}")

        print(f"[OK] Release {args.tag}를 최신 스냅샷으로 교체했습니다.")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
