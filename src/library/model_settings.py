"""Local model profiles; each lesson receives an immutable client snapshot."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import json
import os
import threading
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator
from library.config import LibraryConfig
from library.course_documents import write_text
from library.omlx_client import OmlxClient

SETTINGS_LOCK = threading.RLock()
PURPOSES = ("text", "vision", "asr")


class ModelProfile(BaseModel):
    provider: Literal["omlx", "litellm", "openai-compatible"] = "omlx"
    base_url: str
    model: str = Field(min_length=1)
    api_key: SecretStr = SecretStr("")
    api_key_env: str = ""

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("服务地址需为不包含密钥的 HTTP/HTTPS URL")
        return value.rstrip("/")

    @field_validator("api_key_env")
    @classmethod
    def validate_env(cls, value: str) -> str:
        if value and not value.replace("_", "").isalnum():
            raise ValueError("环境变量名只能包含字母、数字和下划线")
        return value

    def identity(self) -> dict:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
        }

    def public(self) -> dict:
        return {
            **self.identity(),
            "api_key_env": self.api_key_env,
            "has_api_key": bool(
                os.environ.get(self.api_key_env, "")
                if self.api_key_env
                else self.api_key.get_secret_value()
            ),
        }

    def key(self) -> str:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if not value:
                raise ValueError(f"服务进程未配置环境变量 {self.api_key_env}")
            return value
        return self.api_key.get_secret_value() or "noapikey"


class ModelProfiles(BaseModel):
    text: ModelProfile
    vision: ModelProfile
    asr: ModelProfile


class RoutedClient:
    def __init__(self, cfg: LibraryConfig, profiles: ModelProfiles):
        self.profiles = profiles
        self.clients = {
            purpose: OmlxClient(
                profile.base_url,
                profile.key(),
                timeout_s=cfg.omlx.timeout_s,
                max_retries=cfg.omlx.max_retries,
                provider=profile.provider,
            )
            for purpose in PURPOSES
            if (profile := getattr(profiles, purpose))
        }

    def chat(self, model, messages, temperature=0.2, max_tokens=8192):
        return self.clients["text"].chat(
            self.profiles.text.model, messages, temperature, max_tokens
        )

    def chat_vision(self, model, system, prompt, images):
        return self.clients["vision"].chat_vision(
            self.profiles.vision.model, system, prompt, images
        )

    def transcribe(self, audio, model, language="zh"):
        return self.clients["asr"].transcribe(audio, self.profiles.asr.model, language)

    def close(self):
        for client in self.clients.values():
            client.close()


class ModelSettings:
    def __init__(self, cfg: LibraryConfig):
        self.cfg = cfg
        self.path = cfg.data_dir / "model-settings.json"
        self.secrets_path = cfg.data_dir / "model-settings.secrets.json"

    def defaults(self) -> ModelProfiles:
        common = {
            "provider": "omlx",
            "base_url": self.cfg.omlx.base_url,
            "api_key": self.cfg.omlx.effective_api_key,
        }
        return ModelProfiles(
            text=ModelProfile(**common, model=self.cfg.omlx.vision_model),
            vision=ModelProfile(**common, model=self.cfg.omlx.vision_model),
            asr=ModelProfile(**common, model=self.cfg.omlx.asr_model),
        )

    def load(self) -> ModelProfiles:
        with SETTINGS_LOCK:
            profiles = self.defaults()
            if not self.path.exists():
                return profiles
            settings = json.loads(self.path.read_text(encoding="utf-8"))
            secrets = (
                json.loads(self.secrets_path.read_text(encoding="utf-8"))
                if self.secrets_path.exists()
                else {}
            )
            for purpose in PURPOSES:
                value = settings[purpose]
                value["api_key"] = secrets.get(purpose, {}).get(
                    "api_key", getattr(profiles, purpose).api_key.get_secret_value()
                )
                setattr(profiles, purpose, ModelProfile.model_validate(value))
            return profiles

    def save(self, profiles: ModelProfiles) -> dict:
        with SETTINGS_LOCK:
            old = self.load()
            settings = {}
            secrets = {}
            for purpose in PURPOSES:
                profile = getattr(profiles, purpose)
                if (
                    not profile.api_key.get_secret_value()
                    and profile.base_url == getattr(old, purpose).base_url
                ):
                    profile = profile.model_copy(
                        update={"api_key": getattr(old, purpose).api_key}
                    )
                settings[purpose] = {
                    **profile.identity(),
                    "api_key_env": profile.api_key_env,
                }
                secrets[purpose] = {"api_key": profile.api_key.get_secret_value()}
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            # Create with owner-only access before writing any secret bytes.
            fd = os.open(
                self.secrets_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                os.fchmod(file.fileno(), 0o600)
                json.dump(secrets, file, ensure_ascii=False)
            write_text(self.path, json.dumps(settings, ensure_ascii=False, indent=2))
            return self.public()

    def public(self) -> dict:
        profiles = self.load()
        return {purpose: getattr(profiles, purpose).public() for purpose in PURPOSES}

    def models(
        self, purpose: Literal["text", "vision", "asr"], profile: ModelProfile
    ) -> list[str]:
        if (
            not profile.api_key.get_secret_value()
            and not profile.api_key_env
            and profile.base_url == getattr(self.load(), purpose).base_url
        ):
            profile = profile.model_copy(
                update={"api_key": getattr(self.load(), purpose).api_key}
            )
        client = OmlxClient(
            profile.base_url,
            profile.key(),
            timeout_s=15,
            max_retries=1,
            provider=profile.provider,
        )
        try:
            return client.list_models()
        finally:
            client.close()

    @contextmanager
    def runtime(self, existing_client):
        if not self.path.exists():
            yield self.cfg, existing_client
            return
        profiles = self.load()
        analysis = dict(self.cfg.analysis)
        defaults = self.defaults()
        for purpose in ("text", "vision"):
            profile = getattr(profiles, purpose)
            if profile.identity() != getattr(defaults, purpose).identity():
                analysis[purpose + "_identity"] = profile.identity()
        cfg = replace(
            self.cfg,
            analysis=analysis,
            omlx=replace(
                self.cfg.omlx,
                base_url=(
                    self.cfg.omlx.base_url
                    if profiles.asr.base_url == defaults.asr.base_url
                    else profiles.asr.base_url
                ),
                asr_model=profiles.asr.model,
                vision_model=profiles.vision.model,
            ),
        )
        client = RoutedClient(cfg, profiles)
        try:
            yield cfg, client
        finally:
            client.close()
