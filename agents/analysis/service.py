import logging

from agents.analysis.context import (
    STEP_LABELS,
    SYSTEM_PROMPT,
    build_context,
    fingerprint,
    response_schema,
    validate_recommendation,
)
from api.analysis_database import AnalysisRepository
from api.config import Settings
from api.database import Database
from integrations.llm import LLMClient, LLMError

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, settings: Settings, database: Database, *, client=None):
        self.settings = settings
        self.database = database
        self.repository = AnalysisRepository(database)
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()

    def context(self, object_id: str) -> dict:
        context = build_context(
            self.database.object(object_id), utc_offset=self.settings.photo_utc_offset_hours
        )

        context["_llm_configuration"] = self.settings.llm.configuration_fingerprint()
        return context

    def enqueue(self, object_id: str, *, force: bool = False) -> dict:
        context = self.context(object_id)
        return self.repository.enqueue(
            object_id, context, fingerprint(context), self.settings.llm.model, force=force
        )

    def automatic(self, object_id: str) -> None:
        if self.settings.llm.enabled and self.settings.llm.auto_analyze:
            self.enqueue(object_id)

    def public(self, record: dict | None, object_id: str) -> dict:
        current = self.context(object_id)
        stale = record is not None and record["fingerprint"] != fingerprint(current)
        context = current if stale or record is None else record["context"]
        llm_status = "rejected" if stale else record["llm_status"] if record else "unavailable"
        recommendation = record["recommendation"] if record and llm_status == "accepted" else None
        effective = recommendation or context["fallback"]
        error = (
            {
                "code": "STALE_CONTEXT",
                "message": "Данные объекта изменились; заключение нужно обновить",
            }
            if stale
            else {"code": record["error_code"], "message": record["error_message"]}
            if record and record["error_code"]
            else None
        )
        return {
            "analysis_id": record["id"] if record else None,
            "object_id": object_id,
            "status": record["status"] if record else "idle",
            "llm_status": llm_status,
            "decision_source": "llm" if recommendation else "rules_fallback",
            "recommendation": recommendation,
            "explanation": effective["reason"],
            "strategy_code": effective["strategy_code"],
            "next_step": {
                "code": effective["next_step_code"],
                "label": STEP_LABELS[effective["next_step_code"]],
                "requires_confirmation": True,
            }
            if effective["next_step_code"]
            else None,
            "evidence_refs": effective["evidence_refs"],
            "missing_data": effective["missing_data"],
            "facts": context["facts"],
            "stale": stale,
            "proposal_only": True,
            "model": (record["response_model"] or record["requested_model"])
            if record
            else self.settings.llm.model,
            "rules_version": context["rules_version"],
            "prompt_version": context["prompt_version"],
            "updated_at": record["updated_at"] if record else None,
            "error": error,
            "connection": self.settings.llm.public_status(self.settings.mock),
        }

    def latest(self, object_id: str) -> dict:
        return self.public(self.repository.latest(object_id), object_id)

    def process_one(self) -> bool:
        job = self.repository.claim(self.settings.job_lease_seconds)
        if job is None:
            return False
        analysis_id, token = job
        record = self.repository.get(analysis_id)
        raw = None
        response_model = None
        usage = None
        try:
            if fingerprint(self.context(record["object_id"])) != record["fingerprint"]:
                self.repository.finish(
                    analysis_id,
                    token,
                    llm_status="rejected",
                    error_code="STALE_CONTEXT",
                    error_message="Данные объекта изменились",
                )
                return True
            config = self.settings.llm
            if self.settings.mock or not config.enabled or not config.configured:
                code, message = (
                    ("MOCK_MODE", "Сетевые запросы AI отключены в деморежиме сервера")
                    if self.settings.mock
                    else ("INTEGRATION_DISABLED", "Подключение к AI выключено")
                    if not config.enabled
                    else ("NOT_CONFIGURED", "Задайте LLM_API_KEY, LLM_BASE_URL и LLM_MODEL")
                )
                raise LLMError(code, message)
            if self._client is None:
                self._client = LLMClient(config)
            # The snapshot contains only whitelisted facts, never comments or object identifiers.
            snapshot = record["context"]
            response = self._client.generate(
                system=SYSTEM_PROMPT, context=snapshot, schema=response_schema(snapshot)
            )
            raw = response["content"]
            model = response.get("model")
            response_model = model[:200] if isinstance(model, str) and model else config.model
            reported_usage = response.get("usage")
            usage = (
                {
                    key: value
                    for key, value in reported_usage.items()
                    if key
                    in {
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "precached_prompt_tokens",
                    }
                    and type(value) is int
                    and value >= 0
                }
                if isinstance(reported_usage, dict)
                else {}
            )
            recommendation = validate_recommendation(raw, snapshot)
            if fingerprint(self.context(record["object_id"])) != record["fingerprint"]:
                raise ValueError("STALE_CONTEXT")
            self.repository.finish(
                analysis_id,
                token,
                llm_status="accepted",
                recommendation=recommendation,
                raw_response=raw,
                response_model=response_model,
                usage=usage,
            )
        except LLMError as error:
            logger.warning("llm_unavailable analysis_id=%s code=%s", analysis_id, error.code)
            self.repository.finish(
                analysis_id,
                token,
                llm_status="unavailable",
                error_code=error.code,
                error_message=error.message,
            )
        except ValueError as error:
            safe_codes = {
                "DISALLOWED_DECISION",
                "INVALID_EVIDENCE",
                "INVALID_MISSING_DATA",
                "STALE_CONTEXT",
            }
            code = str(error) if str(error) in safe_codes else "INVALID_RECOMMENDATION"
            logger.warning("llm_rejected analysis_id=%s code=%s", analysis_id, code)
            self.repository.finish(
                analysis_id,
                token,
                llm_status="rejected",
                raw_response=raw,
                response_model=response_model,
                usage=usage,
                error_code=code,
                error_message="Ответ модели не прошёл проверку; показан вывод по правилам",
            )
        except Exception:
            # No exception dump: third-party exceptions can contain private HTTP data.
            logger.error("llm_processing_failed analysis_id=%s", analysis_id)
            self.repository.finish(
                analysis_id,
                token,
                llm_status="unavailable",
                error_code="PROCESSING_FAILED",
                error_message="Не удалось подготовить AI-заключение",
            )
        return True
