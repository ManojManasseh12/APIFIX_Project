"""App-managed SSRF allowlist: list, trust, and enforce without a restart."""

import pytest

from app.core import security
from app.core.errors import PolicyError
@pytest.fixture()
def hosts_file(monkeypatch, tmp_path):
    path = tmp_path / "allowed_hosts.txt"
    monkeypatch.setattr(security, "ALLOWED_HOSTS_FILE", path)
    return path


def test_load_merges_env_and_file(hosts_file, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.allowed_hosts", "api.github.com"
    )
    hosts_file.write_text("# comment\napi.restful-api.dev\n\nbad line\n")
    hosts = security.load_allowed_hosts()
    assert "api.github.com" in hosts
    assert "api.restful-api.dev" in hosts
    assert "bad line" not in hosts


def test_trust_host_appends_and_skips_duplicates(hosts_file):
    security.trust_host("Example.COM.")
    security.trust_host("example.com")
    content = hosts_file.read_text()
    assert content.count("example.com") == 1
    assert "example.com" in security.load_allowed_hosts()


def test_trust_host_rejects_garbage(hosts_file):
    for bad in ("bad host", "*", "-lead.com", "http://x.com", "spam" * 100):
        with pytest.raises(PolicyError):
            security.trust_host(bad)
    assert not hosts_file.exists()


def test_list_and_trust_endpoints(hosts_file):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        added = client.post("/api/allowed-hosts", json={"host": "example.com"})
        assert added.status_code == 200
        assert added.json()["added"] == "example.com"

        listed = client.get("/api/allowed-hosts")
        assert listed.status_code == 200
        assert "example.com" in listed.json()["hosts"]

        again = client.post("/api/allowed-hosts", json={"host": "example.com"})
        assert again.status_code == 200
        assert hosts_file.read_text().count("example.com") == 1


def test_policy_rejects_garbage_host_over_api(hosts_file):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/allowed-hosts", json={"host": "<script>"})
        assert response.status_code == 422
        assert "detail" in response.json()
