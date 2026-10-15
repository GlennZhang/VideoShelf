"""oMLX client: OpenAI-compatible chat (vision) and audio transcription.

Kept as the single seam against oMLX so response-shape differences are fixed
in one place (design.md D5 risk). ``base_url`` may be given with or without
the ``/v1`` suffix; it is normalized here.
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)


class OmlxError(Exception):
    """oMLX request failed after retries."""


def normalize_base_url(url: str) -> str:
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


class OmlxClient:
    """Minimal OpenAI-compatible client for chat and transcription."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 600.0,
        max_retries: int = 3,
    ):
        self.base_url = normalize_base_url(base_url)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        self.max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                if resp.status_code >= 500:
                    last_exc = OmlxError(
                        f"{url} -> HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                else:
                    resp.raise_for_status()
                    return resp
            except httpx.HTTPError as exc:
                last_exc = exc
            logger.warning(
                "oMLX %s attempt %d/%d failed: %s",
                url,
                attempt,
                self.max_retries,
                last_exc,
            )
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 10))
        raise OmlxError(
            f"{url} failed after {self.max_retries} attempts:" f" {last_exc}"
        )

    # -- models ------------------------------------------------------------

    def list_models(self) -> List[str]:
        resp = self._request("GET", "/models")
        data = json.loads(resp.text).get("data", [])
        return sorted(m["id"] for m in data)

    # -- chat / vision -------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: Sequence[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> str:
        resp = self._request(
            "POST",
            "/chat/completions",
            json={
                "model": model,
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        data = json.loads(resp.text)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise OmlxError(f"unexpected chat response shape: {data}") from exc

    def chat_vision(
        self,
        model: str,
        system: str,
        prompt: str,
        images: Sequence[Path],
    ) -> str:
        """Chat with base64 JPEG/PNG images attached as multimodal parts."""
        parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(Path(img).read_bytes()).decode()
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        return self.chat(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": parts},
            ],
        )

    # -- transcription ---------------------------------------------------------

    def transcribe(
        self,
        audio: Path,
        model: str,
        language: str = "zh",
    ) -> str:
        """Transcribe one audio file; returns plain text.

        Response shapes differ across OpenAI-compatible servers (plain text
        body vs ``{"text": ...}``); both are handled.
        """
        with open(audio, "rb") as fh:
            resp = self._request(
                "POST",
                "/audio/transcriptions",
                data={"model": model, "language": language},
                files={"file": (audio.name, fh, "audio/wav")},
            )
        body = resp.text.strip()
        if not body:
            return ""
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                return data.get("text") or ""
            return str(data)
        except json.JSONDecodeError:
            return body


def extract_json(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from a model reply.

    Tolerates ```json fences and prose around the object.
    """
    fence = text.split("```")
    candidates: List[str] = []
    if len(fence) >= 3:
        for chunk in fence[1::2]:
            candidates.append(chunk.removeprefix("json").strip())
    candidates.append(text.strip())
    for candidate in candidates:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise OmlxError(f"no JSON object found in model reply: {text[:300]}")
