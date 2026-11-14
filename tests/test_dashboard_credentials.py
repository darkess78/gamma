import tomllib
from unittest.mock import patch

from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.dashboard import credentials, main, reset_password


EXISTING = '''dashboard_auth_enabled = true
dashboard_auth_username = "neety"
dashboard_auth_password = "set-by-codex"
dashboard_session_secret = "old-secret"
tts_provider = "qwen"

[resource_routing.policy]
active_llm_routing = false
'''


def apply_config(path):
    values = tomllib.loads(path.read_text())
    return patch.multiple(
        settings,
        dashboard_auth_enabled=values["dashboard_auth_enabled"],
        dashboard_auth_username=values["dashboard_auth_username"],
        dashboard_auth_password=values.get("dashboard_auth_password", ""),
        dashboard_auth_password_hash=values.get("dashboard_auth_password_hash", ""),
        dashboard_session_secret=values["dashboard_session_secret"],
    )


def login(client, password):
    return client.post("/login", data={"username": "neety", "password": password}, follow_redirects=False)


def test_reset_replaces_plain_password_and_signs_everyone_out(tmp_path):
    path = tmp_path / "app.local.toml"
    path.write_text(EXISTING)
    with apply_config(path):
        old_client = TestClient(main.app)
        assert login(old_client, "set-by-codex").status_code == 303

    credentials.reset_credentials(path, username="neety", password="a new passphrase")

    saved = tomllib.loads(path.read_text())
    assert "dashboard_auth_password" not in saved
    assert saved["dashboard_auth_password_hash"].startswith("scrypt$")
    assert "a new passphrase" not in path.read_text()
    assert saved["dashboard_session_secret"] != "old-secret"
    assert saved["tts_provider"] == "qwen"
    assert saved["resource_routing"]["policy"] == {"active_llm_routing": False}
    assert (path.stat().st_mode & 0o777) == 0o600

    with apply_config(path):
        assert old_client.get("/api/status/header").status_code == 401  # old session cookie no longer valid
        client = TestClient(main.app)
        assert login(client, "set-by-codex").status_code == 401
        assert login(client, "a new passphrase").status_code == 303
        assert client.get("/dashboard", follow_redirects=False).status_code == 200


def test_keys_are_added_above_the_first_table(tmp_path):
    path = tmp_path / "app.local.toml"
    path.write_text('[resource_routing.policy]\nactive_llm_routing = false\n')
    credentials.reset_credentials(path, username="neety", password="a new passphrase")
    saved = tomllib.loads(path.read_text())
    assert saved["dashboard_auth_username"] == "neety"
    assert "dashboard_auth_username" not in saved["resource_routing"]["policy"]


def test_command_refuses_mismatched_or_short_passwords(tmp_path, monkeypatch):
    path = tmp_path / "app.local.toml"
    path.write_text(EXISTING)
    monkeypatch.setattr(reset_password, "app_local_config_path", lambda: path)
    for first, second in (("a new passphrase", "different one"), ("short", "short")):
        answers = iter([first, second])
        monkeypatch.setattr(reset_password.getpass, "getpass", lambda prompt: next(answers))
        assert reset_password.main(["--username", "neety"]) == 1
        assert path.read_text() == EXISTING
