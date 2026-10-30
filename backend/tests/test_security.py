import pytest

from app.core.errors import PolicyError
from app.core.security import ssrf
from app.security.redactor import redact_headers, redact_text
from app.security.ssrf import check_url


def test_redacts_bearer_token():
    assert "sk-test-123456789" not in redact_text("Bearer sk-test-123456789")


def test_redacts_authorization_header():
    out = redact_headers({"Authorization": "Bearer abc", "Accept": "application/json"})
    assert out["Authorization"] == "<REDACTED>"
    assert out["Accept"] == "application/json"


def test_blocks_localhost_port_9():
    with pytest.raises(PolicyError):
        check_url("http://localhost:9/", {"localhost:8001"})


def test_allows_mock():
    check_url("http://localhost:8001/v1/profile", {"localhost:8001"})


def test_allows_bare_public_host_on_default_port(monkeypatch):
    monkeypatch.setattr(ssrf, "_resolve", lambda host, port: set())

    check_url("https://postman-echo.com/get", {"postman-echo.com"})
    check_url("https://postman-echo.com:443/get", {"postman-echo.com"})

    with pytest.raises(PolicyError):
        check_url("https://postman-echo.com:8443/get", {"postman-echo.com"})
    with pytest.raises(PolicyError):
        check_url("http://postman-echo.com/get", {"postman-echo.com:443"})


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "file:///etc/passwd",
    "gopher://localhost:8001/",
])
def test_blocks_unsafe(url):
    with pytest.raises(PolicyError):
        check_url(url, {"localhost:8001"})
