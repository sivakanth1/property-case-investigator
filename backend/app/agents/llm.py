import json
import re
import time

from ..config import Settings


class ModelError(Exception):
    """The configured model endpoint failed or returned unusable output."""


class ModelTimeout(ModelError):
    pass


def make_llm_client(settings: Settings):
    """Featherless.ai client. Featherless is OpenAI-compatible, so its docs use the OpenAI SDK pointed at its base URL."""
    from openai import OpenAI

    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                  timeout=settings.llm_timeout_s, max_retries=1)


def system_prompt(text: str, model: str | None) -> str:
    """Qwen3 hybrid models reason in <think> blocks by default; /no_think keeps replies short and parseable."""
    return f"{text}\n/no_think" if model and "qwen3" in model.lower() else text


def complete(client, **kwargs):
    """chat.completions.create with one retry when Featherless answers HTTP 200 with an error body and no choices."""
    resp = client.chat.completions.create(**kwargs)
    if not getattr(resp, "choices", None):
        time.sleep(2)
        resp = client.chat.completions.create(**kwargs)
    return resp


def first_message(resp):
    choices = getattr(resp, "choices", None)
    if not choices:
        raise ModelError("Model returned no choices.")
    return choices[0].message


def classify_error(exc: Exception) -> ModelError:
    import openai

    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, openai.APITimeoutError):
        return ModelTimeout("Model request timed out.")
    if isinstance(exc, openai.AuthenticationError):
        return ModelError("Model endpoint rejected the API key (401).")
    if isinstance(exc, openai.APIStatusError):
        return ModelError(f"Model endpoint returned HTTP {exc.status_code}: {str(exc.message)[:200]}")
    if isinstance(exc, openai.APIConnectionError):
        return ModelError("Could not connect to the model endpoint.")
    return ModelError(f"Model call failed: {type(exc).__name__}")


def is_tool_rejection(exc: Exception) -> bool:
    """Some Featherless models answer HTTP 500 whenever tool definitions are sent, so server errors count too."""
    import openai

    return isinstance(exc, openai.APIStatusError) and exc.status_code in (400, 404, 422, 500, 501, 502)


def strip_reasoning(text: str | None) -> str:
    """Removes <think> blocks emitted by reasoning-style models (e.g. Qwen3 thinking variants)."""
    body = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "<think>" in body:  # unterminated reasoning block: nothing usable after it
        body = body.split("<think>", 1)[0]
    return body.strip()


def extract_json_object(text: str | None) -> dict | None:
    if not text:
        return None
    body = text.strip()
    tagged = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", body, re.S)
    if tagged:
        body = tagged.group(1)
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body.strip())
    decoder = json.JSONDecoder()
    for i, ch in enumerate(body):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(body[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
