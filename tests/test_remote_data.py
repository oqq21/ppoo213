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


class RemoteDataTests(unittest.TestCase):
    def setUp(self):
        remote_data._STATE["checked_at"] = 0.0
        remote_data._STATE["snapshot"] = None

    def test_downloads_verified_snapshot(self):
        payloads = {
            name: f"data:{name}".encode("utf-8")
            for name in remote_data.DATA_FILES
        }
        manifest = {
            "version": "test-version",
            "updated_at": "2026-07-24T00:00:00+00:00",
            "files": {
                name: {
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in payloads.items()
            },
        }

        def fake_get(url, **kwargs):
            if "manifest.json" in url:
                return _Response(json_value=manifest)
            for name, data in payloads.items():
                if name in url:
                    return _Response(data=data)
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as temp, patch(
            "your_app.common.remote_data.requests.get",
            side_effect=fake_get,
        ):
            snapshot = remote_data.ensure_data_snapshot(
                temp,
                check_interval=0,
                raw_base="https://example.invalid/data",
            )
            self.assertEqual(snapshot.version, "test-version")
            for name, expected in payloads.items():
                self.assertEqual(snapshot.path(name).read_bytes(), expected)
            self.assertTrue((Path(temp) / "test-version" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
