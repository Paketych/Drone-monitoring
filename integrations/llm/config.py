import hashlib
import ipaddress
import json
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = 60
    max_tokens: int = 1200
    auto_analyze: bool = True
    json_mode: str = "json_object"
    token_limit_field: str = "max_tokens"
    extra_body_json: str = field(default="{}", repr=False)

    @classmethod
    def from_env(cls) -> "LLMSettings":
        return cls(
            enabled=os.getenv("LLM_ENABLED", "0") == "1",
            api_key=os.getenv("LLM_API_KEY", "").strip(),
            base_url=os.getenv("LLM_BASE_URL", "").strip().rstrip("/"),
            model=os.getenv("LLM_MODEL", "").strip(),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1200")),
            auto_analyze=os.getenv("LLM_AUTO_ANALYZE", "1") == "1",
            json_mode=os.getenv("LLM_JSON_MODE", "json_object").strip(),
            token_limit_field=os.getenv("LLM_TOKEN_LIMIT_FIELD", "max_tokens").strip(),
            extra_body_json=os.getenv("LLM_EXTRA_BODY_JSON", "{}").strip() or "{}",
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())

    def extra_body(self) -> dict:
        value = json.loads(self.extra_body_json)
        reserved = {
            "model",
            "messages",
            "stream",
            "response_format",
            "max_tokens",
            "max_completion_tokens",
            "tools",
            "tool_choice",
            "functions",
            "function_call",
            "n",
            "api_key",
            "base_url",
            "extra_headers",
            "extra_query",
            "extra_body",
        }
        if not isinstance(value, dict) or reserved.intersection(value):
            raise ValueError("Invalid LLM_EXTRA_BODY_JSON")
        return value

    def validation_error(self) -> str | None:
        try:
            url = urlsplit(self.base_url)
            # Plain HTTP is useful for local Ollama/vLLM/LM Studio only.
            local = url.hostname == "localhost"
            if not local and url.hostname:
                try:
                    local = ipaddress.ip_address(url.hostname).is_loopback
                except ValueError:
                    pass
            if (
                not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or "?" in self.base_url
                or "#" in self.base_url
                or (url.scheme != "https" and not (url.scheme == "http" and local))
                or not 0 < (url.port or 443) <= 65535
            ):
                return "Проверьте LLM_BASE_URL: нужен HTTPS-адрес API без ключей и параметров"
            if not self.model.strip() or any(c in self.model for c in "\r\n"):
                return "Задайте LLM_MODEL из кабинета провайдера"
            if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 600:
                return "LLM_TIMEOUT_SECONDS должен быть от 0 до 600 секунд"
            if not 1 <= self.max_tokens <= 131072:
                return "LLM_MAX_TOKENS должен быть от 1 до 131072"
            if self.json_mode not in {"json_object", "json_schema", "text"}:
                return "LLM_JSON_MODE: используйте json_object, json_schema или text"
            if self.token_limit_field not in {"max_tokens", "max_completion_tokens"}:
                return "Неизвестное значение LLM_TOKEN_LIMIT_FIELD"
            self.extra_body()
        except (ValueError, TypeError):
            return "Проверьте формат настроек LLM и LLM_EXTRA_BODY_JSON"
        return None

    def configuration_fingerprint(self) -> str:
        # Credentials are excluded: rotating a key does not change the analysis facts.
        try:
            extra = self.extra_body()
        except (ValueError, TypeError):
            extra = "invalid"
        data = {
            "base_url": self.base_url.rstrip("/"),
            "model": self.model,
            "json_mode": self.json_mode,
            "max_tokens": self.max_tokens,
            "token_limit_field": self.token_limit_field,
            "extra_body": extra,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def public_status(self, mock: bool) -> dict:
        valid = self.validation_error() is None
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "configuration_valid": valid,
            "network_allowed": self.enabled and self.configured and valid and not mock,
            "mock": mock,
            "model": self.model,
            "protocol": "openai_compatible",
            "json_mode": self.json_mode,
        }
