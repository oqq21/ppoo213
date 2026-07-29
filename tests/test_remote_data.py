from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from your_app.common import remote_data


class _Response:
    def __init__(self, *, data: bytes = b"", json_value=None):
        self.data = data
        self.json_value = json_value
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.json_value

    def iter_content(self, chunk_size: int):
        yield self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _manifest(version: str, payloads: dict[str, bytes]) -> dict:
    return {
        "version": version,
        "updated_at": "2026-07-24T00:00:00+00:00",
        "files": {
            name: {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "asset": f"{name.encode('utf-8').hex()[:12]}-{hashlib.sha256(data).hexdigest()[:8]}.parquet",
            }
            for name, data in payloads.items()
        },
    }


def _release(manifest: dict) -> dict:
    manifest_name = f"manifest-{manifest['version']}.json"
    return {
        "body": f"Streamlit 최신 데이터 전용 Release\n\ncurrent_manifest={manifest_name}",
        "assets": [
            {
                "id": 1,
                "name": manifest_name,
                "created_at": "2026-07-24T00:00:00Z",
                "browser_download_url": f"https://example.invalid/{manifest_name}",
            },
            *[
                {
                    "id": index + 2,
                    "name": info["asset"],
                    "created_at": "2026-07-24T00:00:00Z",
                    "browser_download_url": f"https://example.invalid/{info['asset']}",
                }
                for index, info in enumerate(manifest["files"].values())
            ],
        ],
    }


class RemoteDataTests(unittest.TestCase):
    def setUp(self):
        remote_data._STATE["checked_at"] = 0.0
        remote_data._STATE["snapshot"] = None

    def test_downloads_verified_release_snapshot(self):
        payloads = {
            name: f"data:{name}".encode("utf-8")
            for name in remote_data.DATA_FILES
        }
        manifest = _manifest("test-version", payloads)
        release = _release(manifest)

        def fake_get(url, **kwargs):
            if "api.github.com" in url:
                return _Response(json_value=release)
            if "manifest-" in url:
                return _Response(json_value=manifest)
            for name, info in manifest["files"].items():
                if info["asset"] in url:
                    return _Response(data=payloads[name])
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as temp, patch(
            "your_app.common.remote_data.requests.get",
            side_effect=fake_get,
        ):
            snapshot = remote_data.ensure_data_snapshot(
                temp,
                check_interval=0,
                release_api="https://api.github.com/repos/test/repo/releases/tags/latest",
            )
            self.assertEqual(snapshot.version, "test-version")
            for name, expected in payloads.items():
                self.assertEqual(snapshot.path(name).read_bytes(), expected)
            self.assertTrue((Path(temp) / "test-version" / "manifest.json").exists())

    def test_direct_manifest_avoids_release_api(self):
        payloads = {
            name: f"direct:{name}".encode("utf-8")
            for name in remote_data.DATA_FILES
        }
        manifest = _manifest("direct-version", payloads)
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if url.endswith("/manifest.json"):
                return _Response(json_value=manifest)
            for name, info in manifest["files"].items():
                if info["asset"] in url:
                    return _Response(data=payloads[name])
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as temp, patch(
            "your_app.common.remote_data.requests.get",
            side_effect=fake_get,
        ):
            snapshot = remote_data.ensure_data_snapshot(
                temp,
                check_interval=0,
                release_api="https://api.github.com/repos/test/repo/releases/tags/latest",
                manifest_url="https://example.invalid/manifest.json",
            )

        self.assertEqual(snapshot.version, "direct-version")
        self.assertFalse(any("api.github.com" in url for url in calls))

    def test_reuses_unchanged_file_blob_between_versions(self):
        payloads_v1 = {
            name: f"v1:{name}".encode("utf-8")
            for name in remote_data.DATA_FILES
        }
        payloads_v2 = dict(payloads_v1)
        payloads_v2["packet_active.parquet"] = b"changed-active"
        manifests = [
            _manifest("version-one", payloads_v1),
            _manifest("version-two", payloads_v2),
        ]
        releases = [_release(manifest) for manifest in manifests]
        state = {"version": 0, "asset_downloads": []}

        def fake_get(url, **kwargs):
            index = state["version"]
            manifest = manifests[index]
            if "api.github.com" in url:
                return _Response(json_value=releases[index])
            if "manifest-" in url:
                return _Response(json_value=manifest)
            for name, info in manifest["files"].items():
                if info["asset"] in url:
                    state["asset_downloads"].append(name)
                    return _Response(data=(payloads_v1, payloads_v2)[index][name])
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as temp, patch(
            "your_app.common.remote_data.requests.get",
            side_effect=fake_get,
        ):
            remote_data.ensure_data_snapshot(
                temp,
                check_interval=0,
                release_api="https://api.github.com/repos/test/repo/releases/tags/latest",
            )
            self.assertEqual(state["asset_downloads"], list(remote_data.DATA_FILES))

            state["version"] = 1
            state["asset_downloads"].clear()
            snapshot = remote_data.ensure_data_snapshot(
                temp,
                check_interval=0,
                release_api="https://api.github.com/repos/test/repo/releases/tags/latest",
            )
            self.assertEqual(snapshot.version, "version-two")
            self.assertEqual(state["asset_downloads"], ["packet_active.parquet"])


if __name__ == "__main__":
    unittest.main()
