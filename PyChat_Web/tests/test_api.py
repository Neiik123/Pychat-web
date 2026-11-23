import os
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())

from backend import main

client = TestClient(main.app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_public_config_never_exposes_server_secrets():
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "supabaseUrl": "https://example.supabase.co",
        "supabaseAnonKey": "test-anon-key",
    }
    assert "test-service-role-key" not in response.text
    assert os.environ["APP_ENCRYPTION_KEY"] not in response.text


def test_database_requires_login():
    response = client.get("/api/db")
    assert response.status_code == 401


def test_nested_api_keys_are_encrypted_at_rest_and_restored():
    original = {"cfg": {"api": {"openai": {"key": "sk-test-secret"}}}}
    encrypted = main.transform_secrets(original, True)
    assert encrypted["cfg"]["api"]["openai"]["key"] != "sk-test-secret"
    assert "__pychat_secret__" in encrypted["cfg"]["api"]["openai"]["key"]
    assert main.transform_secrets(encrypted, False) == original


def test_localhost_proxy_destinations_are_rejected():
    for url in ("http://localhost:11434", "https://localhost", "https://127.0.0.1"):
        try:
            main.assert_public_https(url)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        else:
            raise AssertionError(f"Expected {url} to be rejected")


def test_public_hostname_resolving_to_private_ip_is_rejected():
    fake = [(None, None, None, None, ("10.0.0.5", 443))]
    with patch.object(main.socket, "getaddrinfo", return_value=fake):
        try:
            main.assert_public_https("https://example.com/v1")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        else:
            raise AssertionError("Private DNS result should be rejected")


def test_database_reads_are_scoped_to_authenticated_user():
    async def fake_user(_authorization):
        return {"id": "user-a"}

    async def fake_db(_method, user_id, _payload=None):
        assert user_id == "user-a"
        return {"account": "user-a"}

    with patch.object(main, "current_user", side_effect=fake_user), patch.object(main, "db_request", side_effect=fake_db):
        response = client.get("/api/db", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json() == {"account": "user-a"}


def test_database_rejects_non_object_json():
    async def fake_user(_authorization):
        return {"id": "user-a"}

    with patch.object(main, "current_user", side_effect=fake_user):
        response = client.put("/api/db", content=b"[]", headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
    assert response.status_code == 400


def test_local_connector_only_accepts_private_or_loopback_targets():
    from local_connector import validate_local_url
    assert validate_local_url("http://localhost:11434/api/chat")
    assert validate_local_url("http://127.0.0.1:11434/api/chat")
    public_dns = [(None, None, None, None, ("8.8.8.8", 443))]
    with patch.object(main.socket, "getaddrinfo", return_value=public_dns):
        with patch("local_connector.socket.getaddrinfo", return_value=public_dns):
            try:
                validate_local_url("http://example.com:11434")
            except ValueError:
                pass
            else:
                raise AssertionError("Public internet destinations must be rejected by local connector")
