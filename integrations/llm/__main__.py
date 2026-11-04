import argparse
import json

from api.config import Settings
from integrations.llm import LLMClient, LLMError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check an OpenAI-compatible API without generation"
    )
    parser.add_argument("--check", action="store_true", required=True, help="Request model IDs")
    parser.parse_args()
    settings = Settings.from_env()
    status = settings.llm.public_status(settings.mock)
    if not status["network_allowed"]:
        print(
            json.dumps(
                {
                    **status,
                    "ok": False,
                    "message": "Set MOCK=0, LLM_ENABLED=1, LLM_API_KEY, LLM_BASE_URL and LLM_MODEL",
                }
            )
        )
        return 2
    client = None
    try:
        client = LLMClient(settings.llm)
        models = client.models()
        available = settings.llm.model in models
        print(
            json.dumps({"ok": available, "models": models, "selected_model_available": available})
        )
        return 0 if available else 1
    except LLMError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": error.message}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
