import json

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from integrations.llm.config import LLMSettings


class LLMError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class LLMClient:
    """Chat Completions through OpenAI SDK; no provider-specific authorization."""

    def __init__(self, settings: LLMSettings, *, transport: httpx.BaseTransport | None = None):
        if not settings.configured:
            raise LLMError("NOT_CONFIGURED", "Задайте LLM_API_KEY, LLM_BASE_URL и LLM_MODEL")
        error = settings.validation_error()
        if error:
            raise LLMError("CONFIGURATION_ERROR", error)
        self.settings = settings
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url.rstrip("/") + "/",
            timeout=settings.timeout_seconds,
            max_retries=0,
            http_client=httpx.Client(
                transport=transport, timeout=settings.timeout_seconds, follow_redirects=False
            ),
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _call(operation):
        try:
            return operation()
        except APITimeoutError:
            raise LLMError("TIMEOUT", "AI-сервис не ответил вовремя") from None
        except APIConnectionError:
            raise LLMError("CONNECTION_ERROR", "Не удалось подключиться к AI-сервису") from None
        except APIStatusError as error:
            code, message = {
                400: ("BAD_REQUEST", "AI-сервис отклонил запрос; проверьте модель и JSON-режим"),
                401: ("AUTH_FAILED", "AI-сервис отклонил API-ключ"),
                402: ("QUOTA_EXHAUSTED", "Недостаточно средств или квоты AI-сервиса"),
                403: ("ACCESS_DENIED", "Нет доступа к AI-сервису или выбранной модели"),
                404: ("MODEL_NOT_FOUND", "Проверьте адрес API и идентификатор модели"),
                429: ("RATE_LIMITED", "Превышен лимит запросов AI-сервиса"),
            }.get(error.status_code, ("UPSTREAM_ERROR", "AI-сервис временно недоступен"))
            # SDK exceptions may contain credentials, provider bodies or request URLs.
            raise LLMError(code, message, error.status_code) from None
        except (APIError, ValueError, TypeError):
            raise LLMError("INVALID_RESPONSE", "AI-сервис вернул некорректный ответ") from None

    def models(self) -> list[str]:
        page = self._call(lambda: self._client.models.list())
        try:
            # Avoid implicit auto-pagination and additional unexpected requests.
            return [item.id for item in page.data if isinstance(item.id, str) and item.id]
        except (AttributeError, TypeError):
            raise LLMError("INVALID_RESPONSE", "Не удалось прочитать список моделей") from None

    def generate(self, *, system: str, context: dict, schema: dict) -> dict:
        # Configuration hashes are local audit metadata, not model input.
        facts = {key: value for key, value in context.items() if not key.startswith("_")}
        instruction = system + "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        if "fallback" in facts:
            instruction += "\nПример JSON по текущим фактам:\n" + json.dumps(
                facts["fallback"], ensure_ascii=False
            )
        request = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            "stream": False,
            self.settings.token_limit_field: self.settings.max_tokens,
            "extra_body": self.settings.extra_body(),
        }
        if self.settings.json_mode == "json_schema":
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "inspection_recommendation",
                    "schema": schema,
                    "strict": True,
                },
            }
        elif self.settings.json_mode == "json_object":
            request["response_format"] = {"type": "json_object"}
        completion = self._call(lambda: self._client.chat.completions.create(**request))
        try:
            choice = completion.choices[0]
            message = choice.message
            if (
                choice.finish_reason != "stop"
                or message.refusal
                or message.tool_calls
                or not isinstance(message.content, str)
                or not message.content.strip()
            ):
                raise ValueError
            return {
                "content": message.content,
                "model": completion.model,
                "usage": completion.usage.model_dump() if completion.usage else {},
            }
        except (AttributeError, IndexError, TypeError, ValueError):
            raise LLMError("INVALID_RESPONSE", "AI-сервис вернул неполный ответ") from None
