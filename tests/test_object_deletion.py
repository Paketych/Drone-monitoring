import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from test_material_service import ReadyModel, upload

from agents.analysis.service import AnalysisService
from api import object_deletion
from api.analysis_database import AnalysisRepository
from api.config import Settings
from api.database import Database
from api.errors import ServiceError
from api.main import create_app
from worker.service import Worker

TABLES = (
    "objects",
    "flights",
    "images",
    "material_reviews",
    "readiness_reviews",
    "analyses",
    "valuation_runs",
)


def counts(database):
    with database.connection() as db:
        return {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES
        }


def files(storage):
    root = storage / "flights"
    return {
        file.relative_to(root): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in root.rglob("*")
        if file.is_file()
    }


@pytest.fixture
def graph(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    database = Database(settings.database_path)
    with TestClient(create_app(settings)) as client:
        obj = client.post(
            "/api/v1/objects", json={"address": "Delete this synthetic house"}
        ).json()["id"]
        first = upload(client, obj).json()["flight_id"]
        worker = Worker(settings, predictor=ReadyModel())
        try:
            assert worker.process_one()
        finally:
            worker.close()
        assert (
            client.post(
                f"/api/v1/flights/{first}/material/review", json={"action": "accept"}
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/flights/{first}/review",
                json={
                    "action": "correct",
                    "stage": "walls",
                    "readiness_pct": 45,
                    "comment": "Synthetic check",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(f"/api/v1/objects/{obj}/valuation", json={"area_m2": 120}).status_code
            == 200
        )
        analysis = AnalysisService(settings, database).enqueue(obj)
        second = upload(client, obj).json()["flight_id"]
        yield settings, database, client, obj, first, second, analysis["id"]


def test_deletes_whole_object_graph_and_files_but_preserves_other_house(graph):
    settings, database, client, obj, first, second, analysis = graph
    other = client.post("/api/v1/objects", json={"address": "Keep this house"}).json()["id"]
    other_flight = upload(client, other).json()["flight_id"]
    keep_dir = settings.storage_dir / "flights" / other_flight
    keep = {p.name: p.read_bytes() for p in keep_dir.iterdir()}
    assert client.get("/api/v1/meta").json()["capabilities"]["object_delete"] is True
    result = client.delete(f"/api/v1/objects/{obj}")
    assert result.status_code == 200 and result.json() == {"id": obj, "deleted": True}
    for path in [
        f"/objects/{obj}",
        f"/objects/{obj}/flights",
        f"/objects/{obj}/history",
        f"/objects/{obj}/analysis",
        f"/objects/{obj}/valuation",
        f"/flights/{first}",
        f"/analyses/{analysis}",
    ]:
        assert client.get("/api/v1" + path).status_code == 404
    assert client.delete(f"/api/v1/objects/{obj}").status_code == 404
    assert client.get(f"/api/v1/objects/{other}").status_code == 200
    assert not (settings.storage_dir / "flights" / first).exists()
    assert not (settings.storage_dir / "flights" / second).exists()
    assert keep == {p.name: p.read_bytes() for p in keep_dir.iterdir()}
    assert counts(database) == dict(zip(TABLES, [1, 1, 2, 0, 0, 0, 0]))
    with database.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert database.claim(900)[0] == other_flight
    assert AnalysisRepository(database).claim(900) is None


@pytest.mark.parametrize("module", ["flight", "analysis"])
def test_processing_any_flight_or_ai_blocks_deletion_without_data_loss(graph, module):
    settings, database, client, obj, first, _, analysis = graph
    with database.connection() as db:
        table, row = ("flights", first) if module == "flight" else ("analyses", analysis)
        db.execute(f"UPDATE {table} SET status='processing' WHERE id=?", (row,))
    before, images = counts(database), files(settings.storage_dir)
    response = client.delete(f"/api/v1/objects/{obj}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "OBJECT_BUSY"
    assert counts(database) == before and files(settings.storage_dir) == images


@pytest.mark.parametrize("module", ["flight", "analysis"])
def test_claim_and_delete_are_serialized(graph, module):
    settings, database, client, obj, _, _, _ = graph
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        return (
            database.claim(900) if module == "flight" else AnalysisRepository(database).claim(900)
        )

    def delete():
        barrier.wait()
        return client.delete(f"/api/v1/objects/{obj}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_future, delete_future = pool.submit(claim), pool.submit(delete)
        job, result = claim_future.result(), delete_future.result()
    assert result.status_code in {200, 409}
    if result.status_code == 200:
        assert job is None and counts(database)["objects"] == 0
    else:
        assert job is not None and counts(database)["objects"] == 1
        assert files(settings.storage_dir)


def test_sql_failure_rolls_back_database_and_restores_files(graph):
    settings, database, client, obj, *_ = graph
    before, images = counts(database), files(settings.storage_dir)
    with database.connection() as db:
        db.execute(
            "CREATE TRIGGER stop_delete BEFORE DELETE ON objects BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    result = client.delete(f"/api/v1/objects/{obj}")
    assert result.status_code == 500 and result.json()["error"]["code"] == "OBJECT_DELETE_FAILED"
    assert counts(database) == before and files(settings.storage_dir) == images


def test_file_move_failure_preserves_records_and_already_moved_photos(graph, monkeypatch):
    settings, database, client, obj, *_ = graph
    before, images = counts(database), files(settings.storage_dir)
    rename = Path.rename
    calls = 0

    def fail_second(path, destination):
        nonlocal calls
        if path.parent == settings.storage_dir / "flights":
            calls += 1
            if calls == 2:
                raise PermissionError("test lock")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_second)
    response = client.delete(f"/api/v1/objects/{obj}")
    assert response.status_code == 500
    assert counts(database) == before and files(settings.storage_dir) == images


def test_worker_retries_locked_deleted_files(graph, monkeypatch):
    settings, database, client, obj, *_ = graph
    with monkeypatch.context() as patch:

        def locked(*args, **kwargs):
            raise PermissionError("test lock")

        patch.setattr(object_deletion.shutil, "rmtree", locked)
        assert client.delete(f"/api/v1/objects/{obj}").status_code == 200
    trash = settings.storage_dir / ".trash"
    assert list(trash.iterdir())
    worker = Worker(settings)
    try:
        assert worker.process_one()
    finally:
        worker.close()
    assert not list(trash.iterdir()) and counts(database)["objects"] == 0


def test_startup_restores_files_from_uncommitted_deletion(graph):
    settings, database, _, obj, first, *_ = graph
    original = settings.storage_dir / "flights" / first
    trash = settings.storage_dir / ".trash"
    trash.mkdir(parents=True)
    quarantine = trash / first
    original.rename(quarantine)
    assert not original.exists()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/v1/objects/{obj}").status_code == 200
        assert original.exists() and not quarantine.exists()


def test_unsafe_stored_flight_path_is_rejected_without_touching_external_files(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    outside = tmp_path / "external"
    outside.mkdir()
    important = outside / "keep.txt"
    important.write_text("keep")
    with TestClient(create_app(settings)) as client:
        obj = client.post("/api/v1/objects", json={"address": "Synthetic"}).json()["id"]
        database = Database(settings.database_path)
        database.enqueue(
            {
                "id": "../../external",
                "object_id": obj,
                "flown_at": None,
                "target_confirmed": False,
                "is_demo": True,
            },
            [],
        )
        response = client.delete(f"/api/v1/objects/{obj}")
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "INVALID_STORAGE_PATH"
        )
        assert important.read_text() == "keep" and counts(database)["objects"] == 1


def test_upload_losing_race_to_delete_returns_404_and_removes_new_files(tmp_path, monkeypatch):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        obj = client.post("/api/v1/objects", json={"address": "Synthetic"}).json()["id"]
        enqueue = Database.enqueue

        def after_delete(database, flight, images):
            object_deletion.delete_object(database, settings.storage_dir, obj)
            return enqueue(database, flight, images)

        monkeypatch.setattr(Database, "enqueue", after_delete)
        response = upload(client, obj)
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
        assert not list((settings.storage_dir / "flights").iterdir())


def test_analysis_with_old_context_cannot_recreate_deleted_object(graph):
    settings, database, client, obj, *_ = graph
    service = AnalysisService(settings, database)
    context = service.context(obj)
    assert client.delete(f"/api/v1/objects/{obj}").status_code == 200
    with pytest.raises(ServiceError) as error:
        service.repository.enqueue(obj, context, "old-context", "test-model")
    assert error.value.code == "NOT_FOUND" and counts(database)["analyses"] == 0


def test_delete_empty_object_and_unknown_object(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        obj = client.post("/api/v1/objects", json={"address": "Empty"}).json()["id"]
        assert client.delete(f"/api/v1/objects/{obj}").json() == {"id": obj, "deleted": True}
        assert client.delete("/api/v1/objects/not-found").status_code == 404
