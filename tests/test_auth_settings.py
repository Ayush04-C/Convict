import pytest
from pydantic import SecretStr

from app.main import create_app
from app.settings import AuthConfigurationError, Settings


@pytest.mark.parametrize("changes,name", [
    ({"demo_user": ""}, "DEMO_USER"), ({"demo_user": " "}, "DEMO_USER"),
    ({"demo_pass_hash": SecretStr("")}, "DEMO_PASS_HASH"),
    ({"demo_pass_hash": SecretStr("not-a-hash")}, "DEMO_PASS_HASH"),
    ({"demo_pass_hash": SecretStr("$argon2id$malformed-private-value")}, "DEMO_PASS_HASH"),
    ({"jwt_secret": SecretStr("")}, "JWT_SECRET"),
    ({"jwt_secret": SecretStr("short-private-value")}, "JWT_SECRET"),
    ({"jwt_secret": SecretStr("x" * 64)}, "JWT_SECRET"),
])
def test_invalid_startup_configuration_is_safe(auth_settings, changes, name):
    settings = auth_settings.model_copy(update=changes)
    with pytest.raises(AuthConfigurationError) as captured:
        create_app(settings)
    message = str(captured.value)
    assert name in message
    assert "configure_auth" in message
    for value in changes.values():
        if isinstance(value, SecretStr) and value.get_secret_value():
            assert value.get_secret_value() not in message


def test_api_auth_does_not_require_provider_credentials(auth_settings):
    settings = auth_settings.model_copy(update={"gemini_api_key": SecretStr(""),
                                               "crustdata_api_key": SecretStr("")})
    assert create_app(settings).state.settings is settings


def test_empty_environment_is_rejected(monkeypatch):
    for name in ("DEMO_USER", "DEMO_PASS_HASH", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AuthConfigurationError):
        create_app(Settings(_env_file=None))
