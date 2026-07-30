from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests


DATA_FILES = (
    "요약본.parquet",
    "packet_active.parquet",
    "packet_completed.parquet",
    "gem_prices.json",
)
DEFAULT_RELEASE_API = (
    "https://api.github.com/repos/oqq21/ppoo213/releases/tags/web-data-latest"
)
DEFAULT_MANIFEST_URL = (
    "https://github.com/oqq21/ppoo213/releases/download/"
    "web-data-latest/manifest.json"
)
DEFAULT_FALLBACK_MANIFEST_URL = (
    "https://github.com/oqq21/ppoo213/releases/download/"
    "web-data-latest/manifest-fallback.json"
)
DEFAULT_ASSET_BASE_URL = (
    "https://github.com/oqq21/ppoo213/releases/download/web-data-latest"
)
_LOCK = threading.Lock()
_STATE: dict[str, object] = {
    "checked_at": 0.0,
    "snapshot": None,
}


@dataclass(frozen=True)
class DataSnapshot:
    version: str
    updated_at: str
    directory: Path

    def path(self, name: str) -> Path:
        return self.directory / name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_version(value: object) -> str:
    version = re.sub(r"[^A-Za-z0-9._-]", "", str(value or ""))
    if not version:
        raise ValueError("데이터 manifest에 version이 없습니다.")
    return version[:80]


def _validate_manifest(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("데이터 manifest 형식이 올바르지 않습니다.")
    version = _safe_version(value.get("version"))
    files = value.get("files")
    if not isinstance(files, dict):
        raise ValueError("데이터 manifest에 files가 없습니다.")
    for name in DATA_FILES:
        info = files.get(name)
        if not isinstance(info, dict):
            raise ValueError(f"데이터 manifest에 {name} 정보가 없습니다.")
        sha = str(info.get("sha256") or "")
        size = info.get("size")
        asset = str(info.get("asset") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"{name} sha256이 올바르지 않습니다.")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"{name} size가 올바르지 않습니다.")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", asset):
            raise ValueError(f"{name} Release 파일명이 올바르지 않습니다.")
    result = dict(value)
    result["version"] = version
    return result


def _snapshot_from_manifest(manifest_path: Path) -> DataSnapshot | None:
    try:
        manifest = _validate_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        directory = manifest_path.parent
        if all((directory / name).exists() for name in DATA_FILES):
            return DataSnapshot(
                version=manifest["version"],
                updated_at=str(manifest.get("updated_at") or ""),
                directory=directory,
            )
    except Exception:
        return None
    return None


def _cached_snapshot(cache_root: Path) -> DataSnapshot | None:
    manifests = sorted(
        (
            path
            for path in cache_root.glob("*/manifest.json")
            if path.parent.name != "blobs"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifests:
        snapshot = _snapshot_from_manifest(manifest_path)
        if snapshot is not None:
            return snapshot
    return None


def _download_file(url: str, target: Path, expected_sha: str, expected_size: int) -> None:
    temporary = target.with_suffix(target.suffix + ".tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(15, 300),
            headers={"Cache-Control": "no-cache"},
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size != expected_size:
            raise ValueError(f"{target.name} 크기 불일치: {size} != {expected_size}")
        if digest.hexdigest() != expected_sha:
            raise ValueError(f"{target.name} sha256 불일치")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _release_assets(release: object) -> dict[str, dict]:
    if not isinstance(release, dict):
        raise ValueError("GitHub Release 응답이 올바르지 않습니다.")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub Release에 assets가 없습니다.")
    result = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") and asset.get("browser_download_url"):
            result[str(asset["name"])] = asset
    return result


def _current_manifest_asset(release: dict, assets: dict[str, dict]) -> dict:
    body = str(release.get("body") or "")
    match = re.search(r"(?m)^current_manifest=([A-Za-z0-9._-]+)$", body)
    if match and match.group(1) in assets:
        return assets[match.group(1)]
    manifests = [
        asset
        for name, asset in assets.items()
        if re.fullmatch(r"manifest-[A-Za-z0-9._-]+\.json", name)
    ]
    if not manifests:
        raise ValueError("GitHub Release에 최신 manifest가 없습니다.")
    return max(
        manifests,
        key=lambda asset: (
            str(asset.get("created_at") or ""),
            int(asset.get("id") or 0),
        ),
    )


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _prune_cache(cache_root: Path, current_version: str, keep_versions: int = 2) -> None:
    candidates = []
    for manifest_path in cache_root.glob("*/manifest.json"):
        if manifest_path.parent.name == "blobs":
            continue
        snapshot = _snapshot_from_manifest(manifest_path)
        if snapshot is not None:
            candidates.append((manifest_path.stat().st_mtime, snapshot))
    candidates.sort(key=lambda item: item[0], reverse=True)

    keep = {current_version}
    for _, snapshot in candidates:
        if len(keep) >= max(1, keep_versions):
            break
        keep.add(snapshot.version)

    referenced_hashes: set[str] = set()
    for _, snapshot in candidates:
        if snapshot.version not in keep:
            shutil.rmtree(snapshot.directory, ignore_errors=True)
            continue
        try:
            manifest = _validate_manifest(
                json.loads((snapshot.directory / "manifest.json").read_text(encoding="utf-8"))
            )
            referenced_hashes.update(
                str(info["sha256"]) for info in manifest["files"].values()
            )
        except Exception:
            continue

    blob_dir = cache_root / "blobs"
    if blob_dir.exists():
        for blob in blob_dir.iterdir():
            if blob.is_file() and blob.name not in referenced_hashes:
                blob.unlink(missing_ok=True)


def ensure_data_snapshot(
    cache_root: str | Path | None = None,
    check_interval: float = 60 * 60,
    release_api: str | None = None,
    manifest_url: str | None = None,
) -> DataSnapshot:
    root = Path(
        cache_root
        or os.environ.get("PP213_DATA_CACHE_DIR")
        or (Path(tempfile.gettempdir()) / "ppoo213-data")
    )
    api_url = (
        release_api
        or os.environ.get("PP213_DATA_RELEASE_API")
        or DEFAULT_RELEASE_API
    )
    direct_manifest_url = (
        manifest_url
        or os.environ.get("PP213_DATA_MANIFEST_URL")
        or DEFAULT_MANIFEST_URL
    )

    with _LOCK:
        now = time.monotonic()
        current = _STATE.get("snapshot")
        checked_at = float(_STATE.get("checked_at") or 0.0)
        if (
            isinstance(current, DataSnapshot)
            and now - checked_at < max(0.0, check_interval)
            and all(current.path(name).exists() for name in DATA_FILES)
        ):
            return current

        root.mkdir(parents=True, exist_ok=True)
        cache_buster = int(time.time() // max(1.0, check_interval))
        try:
            try:
                # 공개 Release의 고정 manifest 자산은 REST API 제한을 쓰지 않는다.
                # 데이터 파일명은 manifest에 있으므로 API 없이 다운로드 URL을
                # 안전하게 구성할 수 있다.
                manifest_error: Exception | None = None
                manifest = None
                manifest_urls = [direct_manifest_url]
                if direct_manifest_url == DEFAULT_MANIFEST_URL:
                    manifest_urls.append(DEFAULT_FALLBACK_MANIFEST_URL)
                for candidate_url in manifest_urls:
                    try:
                        manifest_response = requests.get(
                            candidate_url,
                            params={"v": cache_buster},
                            timeout=(15, 30),
                            headers={"Cache-Control": "no-cache"},
                        )
                        manifest_response.raise_for_status()
                        manifest = _validate_manifest(
                            manifest_response.json()
                        )
                        break
                    except Exception as exc:
                        manifest_error = exc
                if manifest is None:
                    raise RuntimeError(
                        f"stable/fallback manifest 조회 실패: {manifest_error}"
                    )
                asset_base_url = str(
                    os.environ.get("PP213_DATA_ASSET_BASE_URL")
                    or DEFAULT_ASSET_BASE_URL
                ).rstrip("/")
                assets = {
                    str(info["asset"]): {
                        "name": str(info["asset"]),
                        "browser_download_url": (
                            f"{asset_base_url}/{info['asset']}"
                        ),
                    }
                    for info in manifest["files"].values()
                }
            except Exception:
                # REST API는 시간당 제한 때문에 기본 경로로 사용하지 않는다.
                # 새 배포는 항상 고정 manifest를 게시한다. 정말 예전 Release를
                # 복구할 때만 명시적인 환경변수로 API fallback을 허용한다.
                allow_api_fallback = (
                    os.environ.get("PP213_ALLOW_API_FALLBACK") == "1"
                    or (release_api is not None and manifest_url is None)
                )
                if not allow_api_fallback:
                    raise
                release_response = requests.get(
                    api_url,
                    params={"v": cache_buster},
                    timeout=(15, 30),
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Cache-Control": "no-cache",
                    },
                )
                release_response.raise_for_status()
                release = release_response.json()
                assets = _release_assets(release)
                manifest_asset = _current_manifest_asset(release, assets)
                manifest_response = requests.get(
                    str(manifest_asset["browser_download_url"]),
                    timeout=(15, 30),
                    headers={"Cache-Control": "no-cache"},
                )
                manifest_response.raise_for_status()
                manifest = _validate_manifest(manifest_response.json())
            version = manifest["version"]
            directory = root / version
            blob_dir = root / "blobs"
            directory.mkdir(parents=True, exist_ok=True)
            blob_dir.mkdir(parents=True, exist_ok=True)

            for name in DATA_FILES:
                info = manifest["files"][name]
                expected_sha = str(info["sha256"])
                expected_size = int(info["size"])
                asset_name = str(info["asset"])
                asset = assets.get(asset_name)
                if asset is None:
                    raise ValueError(f"GitHub Release 파일이 없습니다: {asset_name}")

                blob = blob_dir / expected_sha
                valid = (
                    blob.exists()
                    and blob.stat().st_size == expected_size
                    and _sha256(blob) == expected_sha
                )
                if not valid:
                    _download_file(
                        str(asset["browser_download_url"]),
                        blob,
                        expected_sha,
                        expected_size,
                    )
                target = directory / name
                target_valid = (
                    target.exists()
                    and target.stat().st_size == expected_size
                    and _sha256(target) == expected_sha
                )
                if not target_valid:
                    _link_or_copy(blob, target)

            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            snapshot = DataSnapshot(
                version=version,
                updated_at=str(manifest.get("updated_at") or ""),
                directory=directory,
            )
            _STATE["snapshot"] = snapshot
            _STATE["checked_at"] = now
            _prune_cache(root, version)
            return snapshot
        except Exception:
            fallback = (
                current
                if isinstance(current, DataSnapshot)
                else _cached_snapshot(root)
            )
            _STATE["checked_at"] = now
            if fallback is not None:
                _STATE["snapshot"] = fallback
                return fallback
            raise
