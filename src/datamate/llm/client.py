from __future__ import annotations

import json
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
    def __init__(self, endpoint: str, model: str):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
        ):
            raise ModelUnavailable("Only loopback HTTP model endpoints are allowed")
        self.endpoint, self.model = endpoint, model
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def generate_structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "datamate", "strict": True, "schema": schema},
            },
        }
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
            output = json.loads(raw["choices"][0]["message"]["content"])
            if not isinstance(output, dict):
                raise InvalidModelOutput("Expected a JSON object")
            return output
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailable("Local model request failed") from exc
        except (KeyError, IndexError, ValueError, TypeError, RecursionError) as exc:
            raise InvalidModelOutput("Malformed structured model response") from exc
