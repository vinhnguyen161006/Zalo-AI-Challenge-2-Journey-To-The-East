from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Protocol
from urllib.parse import urlsplit

from datamate.errors import InvalidModelOutput, ModelUnavailable


class ModelClient(Protocol):
    def generate_structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]: ...


class FakeModelClient:
    """Scripted programs for CPU tests only, never a question/answer lookup."""

    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = deque(outputs)
        self.requests: list[list[dict[str, str]]] = []

    def generate_structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self.requests.append(messages)
        if not self.outputs:
            raise ModelUnavailable("Fake client script exhausted")
        return self.outputs.popleft()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ModelUnavailable("Local model endpoint redirects are forbidden")


class VLLMModelClient:
    def __init__(self, endpoint: str, model: str, max_output_tokens: int):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
        ):
            raise ModelUnavailable("Only loopback HTTP model endpoints are allowed")
        self.endpoint, self.model, self.max_output_tokens = endpoint, model, max_output_tokens
        self.last_output: dict[str, Any] | None = None
        self.last_metrics: dict[str, Any] = {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def generate_structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self.last_metrics = {}
        self.last_output = None
        self.verify_request()
        payload = self.payload(messages, schema)
        started = time.monotonic()
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(2000001)
            if len(body) > 2000000:
                raise InvalidModelOutput("Model response exceeds limit")
            raw = json.loads(body)
            usage = raw.get("usage", {})
            self.last_metrics = {
                "model": self.model,
                "elapsed_seconds": time.monotonic() - started,
                "prompt_tokens": raw.get("prompt_eval_count", usage.get("prompt_tokens")),
                "output_tokens": raw.get("eval_count", usage.get("completion_tokens")),
                "load_duration_ns": raw.get("load_duration"),
                "prompt_eval_duration_ns": raw.get("prompt_eval_duration"),
                "eval_duration_ns": raw.get("eval_duration"),
            }
            output = json.loads(self.content(raw))
            if not isinstance(output, dict):
                raise InvalidModelOutput("Expected a JSON object")
            self.last_output = output
            return output
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailable("Local model request failed") from exc
        except (KeyError, IndexError, ValueError, TypeError, RecursionError) as exc:
            raise InvalidModelOutput("Malformed structured model response") from exc

    def content(self, raw: dict[str, Any]) -> str:
        choice = raw["choices"][0]
        if choice.get("finish_reason") == "length":
            raise InvalidModelOutput("Model output token budget exhausted")
        return str(choice["message"]["content"])

    def verify_request(self) -> None:
        pass

    def payload(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "datamate", "strict": True, "schema": schema},
            },
        }


class OllamaModelClient(VLLMModelClient):
    def __init__(
        self,
        endpoint: str,
        model: str,
        max_output_tokens: int,
        context_tokens: int,
        seed: int,
        thinking: bool | None = None,
    ):
        super().__init__(endpoint, model, max_output_tokens)
        self.context_tokens, self.seed = context_tokens, seed
        self.thinking = thinking
        self.expected_revision: str | None = None

    def verify_installed(self, expected_revision: str | None) -> None:
        endpoint = self.endpoint.rsplit("/", 1)[0] + "/tags"
        try:
            with self.opener.open(endpoint, timeout=10) as response:
                body = response.read(2000001)
            if len(body) > 2000000:
                raise ModelUnavailable("Installed model listing exceeds limit")
            models = json.loads(body)["models"]
            matches = [
                m for m in models if m.get("name") == self.model or m.get("model") == self.model
            ]
            if len(matches) != 1:
                raise ModelUnavailable("Configured model is not installed locally")
            if expected_revision and matches[0].get("digest") != expected_revision:
                raise ModelUnavailable("Installed model digest does not match pinned revision")
            self.expected_revision = expected_revision
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            raise ModelUnavailable("Cannot verify installed local model") from exc

    def verify_request(self) -> None:
        if self.expected_revision:
            self.verify_installed(self.expected_revision)

    def payload(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "format": schema,
            "stream": False,
            **({"think": self.thinking} if self.thinking is not None else {}),
            "options": {
                "temperature": 0,
                "seed": self.seed,
                "num_predict": self.max_output_tokens,
                "num_ctx": self.context_tokens,
            },
        }

    def content(self, raw: dict[str, Any]) -> str:
        if raw.get("done") is not True or raw.get("done_reason") == "length":
            raise InvalidModelOutput("Incomplete model generation")
        if raw.get("prompt_eval_count", 0) >= self.context_tokens - self.max_output_tokens:
            raise InvalidModelOutput("Model context has insufficient output reserve")
        return str(raw["message"]["content"])
