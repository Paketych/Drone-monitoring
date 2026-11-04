import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agents.analysis.service import AnalysisService
from api.config import Settings
from api.database import Database
from api.main import create_app
from integrations.llm import LLMSettings


class FactsClient:
    def generate(self, *, context, **kwargs):
        return {"content": json.dumps(context["fallback"]), "model": "test-model"}


@pytest.fixture
def analysis_setup(tmp_path):
    settings = Settings(
        storage_dir=tmp_path,
        mock=False,
        llm=LLMSettings(
            enabled=True,
            api_key="private-key",
            model="test-model",
            base_url="https://provider.example/v1",
            auto_analyze=False,
        ),
    )
    database = Database(settings.database_path)
    database.initialize()
    obj = database.create_object({"address": "PRIVATE_ADDRESS"})["id"]
    return settings, database, obj


@pytest.mark.parametrize(
    "change",
    [
        {"model": "another-model"},
        {"base_url": "https://another.example/v1"},
        {"json_mode": "text"},
        {"extra_body_json": '{"enable_thinking":false}'},
    ],
)
def test_provider_switch_invalidates_old_summary_and_allows_new_job(analysis_setup, change):
    settings, database, obj = analysis_setup
    first = AnalysisService(settings, database, client=FactsClient())
    old = first.enqueue(obj)
    assert first.process_one() and first.latest(obj)["llm_status"] == "accepted"
    updated = replace(settings, llm=replace(settings.llm, **change))
    second = AnalysisService(updated, database, client=FactsClient())
    assert second.latest(obj)["stale"]
    assert second.latest(obj)["decision_source"] == "rules_fallback"
    new = second.enqueue(obj)
    assert new["id"] != old["id"]
    assert second.process_one() and second.latest(obj)["llm_status"] == "accepted"
    assert first.repository.get(old["id"])["llm_status"] == "accepted"


def test_key_rotation_does_not_invalidate_existing_summary(analysis_setup):
    settings, database, obj = analysis_setup
    service = AnalysisService(settings, database, client=FactsClient())
    service.enqueue(obj)
    service.process_one()
    updated = replace(settings, llm=replace(settings.llm, api_key="new-private-key"))
    public = AnalysisService(updated, database).latest(obj)
    assert not public["stale"] and public["llm_status"] == "accepted"
    assert "new-private-key" not in json.dumps(public)


def test_meta_and_status_expose_only_generic_ai_contract(analysis_setup):
    settings, _, _ = analysis_setup
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/meta").json()["capabilities"]["llm_analysis"]
        status = client.get("/api/v1/llm/status").json()
        assert status["network_allowed"] and status["protocol"] == "openai_compatible"
        assert "private-key" not in json.dumps(status) and "provider.example" not in json.dumps(
            status
        )
