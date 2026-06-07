"""HTTP-boundary tests for the dev-menu routes: POST /api/me/reset and the
SSH key upload at POST /api/dev/keys (local storage; S3 stays unconfigured)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


class FakeERP:
    def __init__(self):
        self.reset_called = 0

    async def reset(self):
        self.reset_called += 1
        return {"message": "reset complete", "detail": {"vms": "reboot requested"}}

    async def aclose(self):  # lifespan shutdown calls this on app.state.erp
        pass


@pytest.fixture
def client(tmp_path):
    erp = FakeERP()
    with TestClient(app) as c:
        # keys land next to ssh_private_key_path; point it at the tmp dir.
        app.state.settings = Settings(ssh_private_key_path=str(tmp_path / "your-key.pem"))
        app.state.erp = erp
        c.erp = erp
        c.keys_dir = tmp_path
        yield c


def test_reset_me_proxies_erp(client):
    res = client.post("/api/me/reset")
    assert res.status_code == 200
    assert res.json()["message"] == "reset complete"
    assert client.erp.reset_called == 1


def test_upload_keys_saves_locally(client):
    res = client.post(
        "/api/dev/keys",
        files=[
            ("files", ("case3_key.pem", b"PRIVATE-KEY-BYTES", "application/octet-stream")),
            ("files", ("case4_key.pem", b"ANOTHER-KEY", "application/octet-stream")),
        ],
    )
    assert res.status_code == 200
    body = res.json()
    assert {k["name"] for k in body["saved"]} == {"case3_key.pem", "case4_key.pem"}
    assert all(k["s3"] is False for k in body["saved"])  # S3 not configured
    saved = client.keys_dir / "case3_key.pem"
    assert saved.read_bytes() == b"PRIVATE-KEY-BYTES"
    assert saved.stat().st_mode & 0o777 == 0o600  # private keys must be 0600


def test_upload_keys_rejects_non_key_file(client):
    res = client.post(
        "/api/dev/keys",
        files=[("files", ("notes.txt", b"x", "text/plain"))],
    )
    assert res.status_code == 400
    assert "only .pem/.key" in res.json()["detail"]
    assert not (client.keys_dir / "notes.txt").exists()


def test_upload_keys_strips_path_traversal(client):
    res = client.post(
        "/api/dev/keys",
        files=[("files", ("../../escape.pem", b"x", "application/octet-stream"))],
    )
    assert res.status_code == 200
    assert res.json()["saved"][0]["name"] == "escape.pem"
    assert (client.keys_dir / "escape.pem").exists()
    assert not (Path(client.keys_dir).parent / "escape.pem").exists()
