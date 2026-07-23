from __future__ import annotations

import hashlib
import json
import os
import re
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
)
DEFAULT_RAW_BASE = (
    "https://github.com/oqq21/ppoo213/raw/refs/heads/data-latest"
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
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"{name} sha256이 올바르지 않습니다.")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"{name} size가 올바르지 않습니다.")
    result = dict(value)
    result["version"] = version
    return result


def _cached_snapshot(cache_root: Path) -> DataSnapshot | None:
    manifests = sorted(
        cache_root.glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifests:
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
            continue
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


def ensure_data_snapshot(
    cache_root: str | Path | None = None,
    check_interval: float = 60.0,
    raw_base: str | None = None,
) -> DataSnapshot:
    root = Path(
        cache_root
        or os.environ.get("PP213_DATA_CACHE_DIR")
        or (Path(tempfile.gettempdir()) / "ppoo213-data")
    )
    base = (
        raw_base
        or os.environ.get("PP213_DATA_RAW_BASE")
        or DEFAULT_RAW_BASE
    ).rstrip("/")

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
            response = requests.get(
                f"{base}/manifest.json?v={cache_buster}",
                timeout=(15, 30),
                headers={"Cache-Control": "no-cache"},
            )
            response.raise_for_status()
            manifest = _validate_manifest(response.json())
            version = manifest["version"]
            directory = root / version
            directory.mkdir(parents=True, exist_ok=True)

            for name in DATA_FILES:
                target = directory / name
                info = manifest["files"][name]
                expected_sha = str(info["sha256"])
                expected_size = int(info["size"])
                valid = (
                    target.exists()
                    and target.stat().st_size == expected_size
                    and _sha256(target) == expected_sha
                )
                if not valid:
                    _download_file(
                        f"{base}/{name}?v={version}",
                        target,
                        expected_sha,
                        expected_size,
                    )

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
