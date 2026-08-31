from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")
MODEL_REQUEST_TIMEOUT = 300.0


class GigachatSettings(BaseSettings):
    """Corporate gateway settings, using the names from the supplied example."""

    model_config = SettingsConfigDict(env_prefix="GIGACHAT_")

    local: bool = Field(default=False, validation_alias="LOCAL")
    host: str
    port: int = Field(ge=1, le=65535)
    endpoint: str = "/v1"
    tls_cert_filepath: str = ""
    key_filepath: str = ""
    ca_bundle_filepath: str = ""
    verify_ssl_certs: bool = True

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value or any(char in value for char in "/\\:@?# \t\r\n"):
            raise ValueError("GIGACHAT_HOST must be a hostname without scheme or port")
        return value

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value.startswith("/") or any(char in value for char in "?#\\\r\n"):
            raise ValueError("GIGACHAT_ENDPOINT must be an absolute URL path")
        return value.rstrip("/")

    @property
    def base_url(self) -> str:
        # LOCAL=false is the explicitly configured in-cluster HTTP gateway,
        # not a fallback after an HTTPS failure.
        protocol = "https" if self.local else "http"
        return f"{protocol}://{self.host}:{self.port}{self.endpoint}"

    def certificate_params(self) -> dict[str, str]:
        if not self.local:
            return {}
        result = {}
        for parameter, variable, value in (
            ("cert_file", "GIGACHAT_TLS_CERT_FILEPATH", self.tls_cert_filepath),
            ("key_file", "GIGACHAT_KEY_FILEPATH", self.key_filepath),
            ("ca_bundle_file", "GIGACHAT_CA_BUNDLE_FILEPATH", self.ca_bundle_filepath),
        ):
            if parameter == "ca_bundle_file" and not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if not value or not path.is_file():
                raise ValueError(f"{variable} must point to an existing file")
            result[parameter] = str(path.resolve())
        return result


def get_llm(
    request_timeout: float = MODEL_REQUEST_TIMEOUT,
    max_retries: int = 0,
) -> GigaChat:
    settings = GigachatSettings()
    model = GigaChat(
        model="glm-5.2",
        temperature=0,
        max_tokens=5120,
        top_p=0.1,
        repetition_penalty=1.0,
        base_url=settings.base_url,
        verify_ssl_certs=settings.verify_ssl_certs,
        timeout=request_timeout,
        max_retries=max_retries,
        **settings.certificate_params(),
    )
    # Materialize the shared sync transport before subagents/deadline callbacks.
    # This validates TLS files but does not send an HTTP request.
    model._client._client
    return model


def set_llm_timeout(model: GigaChat, timeout: float) -> None:
    """Update the cached SDK and transport, not just the LangChain field.

    Private client access is isolated here and tested against the pinned SDK.
    The runner uses synchronous invocation, including general-purpose tasks.
    """
    model.timeout = timeout
    client = model._client
    client._settings.timeout = timeout
    transport_timeout = httpx.Timeout(timeout, connect=min(30.0, timeout))
    client._client.timeout = transport_timeout
    if client._auth_client_instance is not None:
        client._auth_client_instance.timeout = transport_timeout


def close_llm(model: GigaChat | None) -> None:
    """Close an existing sync SDK client without creating one during cleanup."""
    if model is not None:
        client = model.__dict__.get("_client")
        if client is not None:
            client.close()


__all__ = ["MODEL_REQUEST_TIMEOUT", "close_llm", "get_llm", "set_llm_timeout"]
