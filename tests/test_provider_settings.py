from pydantic import ValidationError
import pytest

from app.settings import ProviderConfigurationError, Settings


def test_individual_provider_can_be_configured_without_other_credentials():
    Settings(_env_file=None, crustdata_api_key="fake", gemini_api_key="").require_live_credentials(
        "crustdata"
    )
    Settings(_env_file=None, gemini_api_key="fake", crustdata_api_key="").require_live_credentials(
        "gemini"
    )


def test_full_configuration_requires_keys_and_models():
    with pytest.raises(ProviderConfigurationError) as error:
        Settings(
            _env_file=None,
            crustdata_api_key="",
            gemini_api_key="",
            model_fast="",
            model_mid="",
            model_pro="",
        ).require_live_credentials()
    assert all(
        name in str(error.value)
        for name in ["CRUSTDATA_API_KEY", "GEMINI_API_KEY", "MODEL_FAST", "MODEL_MID", "MODEL_PRO"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_request_timeout", 0),
        ("provider_total_timeout", float("inf")),
        ("provider_max_retries", 4),
        ("gemini_timeout", -1),
        ("ingest_inr_per_usd", 0),
        ("ingest_inr_per_usd", float("nan")),
    ],
)
def test_invalid_provider_limits_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
