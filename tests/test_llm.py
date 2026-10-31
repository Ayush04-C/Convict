import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from google.genai import errors
from pydantic import BaseModel, Field, ValidationError
import pytest

from app.llm import GeminiClient, LlmError
from app.settings import ProviderConfigurationError, Settings


class Output(BaseModel):
    value: int = Field(ge=0)


def configuration(**updates):
    return Settings(
        _env_file=None,
        gemini_api_key="fake-gemini-key",
        model_fast="test-fast",
        model_mid="test-mid",
        model_pro="test-pro",
        **updates,
    )


def fake_client(*responses):
    return SimpleNamespace(
        models=SimpleNamespace(generate_content=AsyncMock(side_effect=responses)),
        aclose=AsyncMock(),
    )


async def test_structured_output_and_explicit_model():
    sdk = fake_client(SimpleNamespace(parsed=None, text='{"value":1}'))
    async with GeminiClient(configuration(), client=sdk) as client:
        result = await client.generate("Synthetic prompt", Output, "test-mid")
    assert result == Output(value=1)
    kwargs = sdk.models.generate_content.await_args.kwargs
    assert kwargs["model"] == "test-mid"
    assert kwargs["config"].response_schema is Output
    assert kwargs["config"].response_mime_type == "application/json"
    sdk.aclose.assert_not_awaited()


async def test_already_parsed_model_is_validated():
    sdk = fake_client(SimpleNamespace(parsed={"value": 2}, text=None))
    async with GeminiClient(configuration(), client=sdk) as client:
        assert (await client.generate("x", Output, "test-fast")).value == 2


@pytest.mark.parametrize("bad", ["not-json", '{"value":-1}', '{"wrong":"private-output"}'])
async def test_invalid_output_retries_once_without_echoing_content(bad):
    sdk = fake_client(
        SimpleNamespace(parsed=None, text=bad), SimpleNamespace(parsed={"value": 3}, text=None)
    )
    async with GeminiClient(configuration(), client=sdk) as client:
        assert (await client.generate("Original prompt", Output, "test-fast")).value == 3
    assert sdk.models.generate_content.await_count == 2
    retry = sdk.models.generate_content.await_args.kwargs["contents"]
    assert retry.startswith("Original prompt") and "validation" in retry
    assert bad not in retry and "private-output" not in retry


async def test_sdk_validation_exception_also_retries():
    try:
        Output.model_validate({"value": -1})
    except ValidationError as error:
        invalid = error
    sdk = fake_client(invalid, SimpleNamespace(parsed={"value": 4}, text=None))
    async with GeminiClient(configuration(), client=sdk) as client:
        assert (await client.generate("x", Output, "test-fast")).value == 4


async def test_twice_invalid_output_returns_typed_error():
    sdk = fake_client(
        SimpleNamespace(parsed=None, text="private"), SimpleNamespace(parsed=None, text="private")
    )
    async with GeminiClient(configuration(), client=sdk) as client:
        with pytest.raises(LlmError) as error:
            await client.generate("x", Output, "test-fast")
        assert error.value.kind == "validation" and "private" not in str(error.value)
    assert sdk.models.generate_content.await_count == 2


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, "request"),
        (401, "auth"),
        (403, "permission"),
        (404, "model"),
        (429, "rate_limit"),
        (503, "server"),
    ],
)
async def test_api_errors_are_typed_and_not_retried(status, kind):
    failure = errors.APIError(
        status, {"error": {"message": "private-key-value", "status": "FAILURE"}}
    )
    sdk = fake_client(failure)
    async with GeminiClient(configuration(), client=sdk) as client:
        with pytest.raises(LlmError) as error:
            await client.generate("x", Output, "test-fast")
        assert error.value.kind == kind and error.value.status == status
        assert "private-key-value" not in str(error.value)
    assert sdk.models.generate_content.await_count == 1


async def test_blocked_empty_output_is_not_silently_successful():
    sdk = fake_client(SimpleNamespace(parsed=None, text=None))
    async with GeminiClient(configuration(), client=sdk) as client:
        with pytest.raises(LlmError) as error:
            await client.generate("x", Output, "test-fast")
        assert error.value.kind == "blocked"


async def test_timeout_is_global_and_cancellation_propagates():
    async def wait(**kwargs):
        await asyncio.sleep(1)

    sdk = fake_client()
    sdk.models.generate_content.side_effect = wait
    async with GeminiClient(configuration(gemini_timeout=0.02), client=sdk) as client:
        with pytest.raises(LlmError) as error:
            await client.generate("x", Output, "test-fast")
        assert error.value.kind == "timeout"
    sdk = fake_client(asyncio.CancelledError())
    async with GeminiClient(configuration(), client=sdk) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.generate("x", Output, "test-fast")


async def test_invalid_input_model_and_closed_client_never_call_sdk():
    sdk = fake_client()
    client = GeminiClient(configuration(), client=sdk)
    with pytest.raises(ValueError):
        await client.generate("", Output, "test-fast")
    with pytest.raises(ValueError):
        await client.generate("x", dict, "test-fast")
    with pytest.raises(LlmError) as error:
        await client.generate("x", Output, "not-configured")
    assert error.value.kind == "config"
    await client.aclose()
    with pytest.raises(LlmError):
        await client.generate("x", Output, "test-fast")
    sdk.models.generate_content.assert_not_awaited()


def test_missing_key_is_an_actionable_configuration_error():
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
        GeminiClient(Settings(_env_file=None, gemini_api_key=""))
