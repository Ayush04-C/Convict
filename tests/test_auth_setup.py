import os

from dotenv import dotenv_values
from pwdlib import PasswordHash
import pytest

from app.settings import AuthConfigurationError, Settings
from scripts.configure_auth import configure

PASSWORD = "setup-test-password-42"


def inputs(*values):
    responses = iter(values)
    return lambda prompt: next(responses)


def unexpected_prompt(prompt):
    raise AssertionError("Existing credentials must not prompt for a password")


def test_new_configuration_round_trips_without_plaintext(tmp_path, capsys):
    path = tmp_path / ".env"
    assert configure(path, input_fn=inputs("ayush"), password_fn=inputs(PASSWORD, PASSWORD))
    values = dotenv_values(path)
    assert values["DEMO_USER"] == "ayush"
    assert PasswordHash.recommended().verify(PASSWORD, values["DEMO_PASS_HASH"])
    assert len(values["JWT_SECRET"]) >= 32
    assert PASSWORD not in path.read_text()
    assert PASSWORD not in capsys.readouterr().out
    assert os.stat(path).st_mode & 0o777 == 0o600
    Settings(_env_file=path).require_auth_credentials()


def test_provider_values_comments_and_existing_secret_are_preserved(tmp_path, auth_settings):
    path = tmp_path / ".env"
    secret = auth_settings.jwt_secret.get_secret_value()
    original = ("# keep this comment\nDEMO_USER=ayush\nDEMO_PASS_HASH=\n"
                f"JWT_SECRET='{secret}'\nGEMINI_API_KEY='provider-test-value'\nMODEL_MID=test-model")
    path.write_text(original)
    assert configure(path, input_fn=unexpected_prompt, password_fn=inputs(PASSWORD, PASSWORD))
    content = path.read_text()
    assert "# keep this comment\n" in content
    assert "GEMINI_API_KEY='provider-test-value'\nMODEL_MID=test-model" in content
    assert dotenv_values(path)["JWT_SECRET"] == secret
    assert PASSWORD not in content


def test_existing_credentials_are_never_rotated(tmp_path, auth_settings):
    path = tmp_path / ".env"
    original = (f"DEMO_USER={auth_settings.demo_user}\n"
                f"DEMO_PASS_HASH='{auth_settings.demo_pass_hash.get_secret_value()}'\n"
                f"JWT_SECRET='{auth_settings.jwt_secret.get_secret_value()}'\n")
    path.write_text(original)
    assert configure(path, input_fn=unexpected_prompt, password_fn=unexpected_prompt) is False
    assert path.read_text() == original


@pytest.mark.parametrize("passwords", [("too-short",), ("x" * 1025,), (PASSWORD, "different-password")])
def test_invalid_password_leaves_environment_unchanged(tmp_path, passwords):
    path = tmp_path / ".env"
    original = "DEMO_USER=ayush\nGEMINI_API_KEY=existing-test-value\n"
    path.write_text(original)
    with pytest.raises(ValueError):
        configure(path, input_fn=unexpected_prompt, password_fn=inputs(*passwords))
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_interrupted_input_does_not_create_file(tmp_path):
    def interrupted(prompt):
        raise KeyboardInterrupt()
    path = tmp_path / ".env"
    with pytest.raises(KeyboardInterrupt):
        configure(path, input_fn=inputs("ayush"), password_fn=interrupted)
    assert not path.exists()


def test_failed_atomic_replace_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    original = "DEMO_USER=ayush\n"
    path.write_text(original)
    def fail_replace(source, destination):
        raise OSError("simulated write failure")
    monkeypatch.setattr("scripts.configure_auth.os.replace", fail_replace)
    with pytest.raises(OSError):
        configure(path, input_fn=unexpected_prompt, password_fn=inputs(PASSWORD, PASSWORD))
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_existing_auth_is_not_silently_overwritten(tmp_path, auth_settings):
    path = tmp_path / ".env"
    original = ("DEMO_USER=ayush\nDEMO_PASS_HASH=broken-existing-value\n"
                f"JWT_SECRET='{auth_settings.jwt_secret.get_secret_value()}'\n")
    path.write_text(original)
    with pytest.raises(AuthConfigurationError):
        configure(path, input_fn=unexpected_prompt, password_fn=unexpected_prompt)
    assert path.read_text() == original


def test_empty_username_uses_default(tmp_path):
    path = tmp_path / ".env"
    configure(path, input_fn=inputs(""), password_fn=inputs(PASSWORD, PASSWORD))
    assert dotenv_values(path)["DEMO_USER"] == "ayush"


def test_cli_password_error_is_actionable_without_echoing_input(monkeypatch):
    from scripts.configure_auth import AuthSetupError, main
    def fail(path):
        raise AuthSetupError("Passwords did not match; .env was not changed.")
    monkeypatch.setattr("scripts.configure_auth.configure", fail)
    with pytest.raises(SystemExit, match="Passwords did not match"):
        main()
