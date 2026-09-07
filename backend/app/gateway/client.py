import os
from functools import lru_cache
from typing import Optional, TypeVar

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

_DEFAULT_MODEL = "primary"

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)


class GatewayError(RuntimeError):
    """Raised when the LiteLLM proxy is unreachable or misconfigured."""


@lru_cache(maxsize=None)
def get_chat_model(model: Optional[str] = None) -> ChatOpenAI:
    """Builds a ChatOpenAI client pointed at the LiteLLM proxy - the only place a model client is constructed."""
    base_url = os.environ.get("LITELLM_BASE_URL")
    api_key = os.environ.get("LITELLM_API_KEY")
    if not base_url or not api_key:
        raise GatewayError("LITELLM_BASE_URL and LITELLM_API_KEY must be set - see backend/.env.example")
    return ChatOpenAI(model=model or os.environ.get("LITELLM_MODEL", _DEFAULT_MODEL), base_url=base_url, api_key=api_key)


def chat(messages: list[BaseMessage], model: Optional[str] = None) -> AIMessage:
    """Sends `messages` through the LiteLLM proxy (primary/fallback resolved by litellm-proxy/config.yaml) and returns the reply."""
    try:
        return get_chat_model(model).invoke(messages)
    except GatewayError:
        raise
    except Exception as exc:
        raise GatewayError(f"LiteLLM proxy call failed: {exc}") from exc


def chat_structured(messages: list[BaseMessage], schema: type[_SchemaT], model: Optional[str] = None) -> _SchemaT:
    """Sends `messages` through the LiteLLM proxy and parses the reply into `schema` via tool-calling
    structured output, instead of freeform text - use this whenever the caller needs a validated
    field written to state (e.g. a route/verdict), not prose. Raises GatewayError if the proxy is
    unreachable or the model can't produce a schema-conforming reply."""
    try:
        result = get_chat_model(model).with_structured_output(schema).invoke(messages)
    except GatewayError:
        raise
    except Exception as exc:
        raise GatewayError(f"LiteLLM proxy structured call failed: {exc}") from exc
    if not isinstance(result, schema):
        raise GatewayError(f"LiteLLM proxy returned unexpected structured output: {result!r}")
    return result
