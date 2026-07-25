from pathlib import Path

import auth


class _StopExecution(Exception):
    pass


def test_get_auth_config_uses_repo_secrets_fallback(monkeypatch, tmp_path):
    secrets_dir = tmp_path / ".streamlit"
    secrets_dir.mkdir()
    (secrets_dir / "secrets.toml").write_text(
        """
[auth.cookie]
name = "farmenager_auth"
key = "test-secret-key"
expiry_days = 7

[auth.credentials.usernames.admin]
name = "Administrator"
password = "$2b$12$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""".strip()
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth.st, "secrets", {})
    monkeypatch.setattr(auth.st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth.st, "stop", lambda: (_ for _ in ()).throw(_StopExecution()))

    config = auth._get_auth_config()

    assert config["cookie"]["key"] == "test-secret-key"
    assert config["credentials"]["usernames"]["admin"]["name"] == "Administrator"
