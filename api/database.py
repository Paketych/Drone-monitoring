import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agents.valuation.calculator import valuation_for_object
from api.errors import ServiceError
from api.presentation import (
    fixed_material_response,
    material_data_status,
    material_result_response,
    processing_status,
    readiness_data_status,
)
from schemas.material import utc_now


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Database:
    """SQLite stores both records and the durable processing queue."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, material TEXT
                );
                CREATE TABLE IF NOT EXISTS flights (
                    id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects(id),
                    status TEXT NOT NULL, uploaded_at TEXT NOT NULL, flown_at TEXT,
                    target_confirmed INTEGER NOT NULL, is_demo INTEGER NOT NULL,
                    result TEXT, error TEXT, lease_token TEXT, lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS flights_queue ON flights(status, uploaded_at);
                CREATE INDEX IF NOT EXISTS flights_object_uploaded
                    ON flights(object_id, uploaded_at DESC);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, flight_id TEXT NOT NULL REFERENCES flights(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_id TEXT NOT NULL REFERENCES flights(id), payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS readiness_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_id TEXT NOT NULL REFERENCES flights(id), payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects(id),
                    fingerprint TEXT NOT NULL, context TEXT NOT NULL, requested_model TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL, llm_status TEXT NOT NULL,
                    recommendation TEXT, raw_response TEXT, response_model TEXT, usage TEXT,
                    error_code TEXT, error_message TEXT,
                    lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS analyses_queue ON analyses(status, created_at);
                CREATE INDEX IF NOT EXISTS analyses_object ON analyses(object_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS valuation_runs (
                    id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects(id),
                    inputs TEXT NOT NULL, result TEXT NOT NULL, fingerprint TEXT,
                    author TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS valuation_runs_object ON valuation_runs(object_id, created_at DESC);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(flights)")}
            for column in ("readiness", "detections", "module_errors"):
                if column not in columns:
                    db.execute(f"ALTER TABLE flights ADD COLUMN {column} TEXT")
            object_columns = {row["name"] for row in db.execute("PRAGMA table_info(objects)")}
            if "valuation_inputs" not in object_columns:
                db.execute("ALTER TABLE objects ADD COLUMN valuation_inputs TEXT")

    def create_object(self, payload: dict) -> dict:
        object_id, created_at = "obj_" + uuid4().hex, utc_now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO objects(id,payload,created_at) VALUES (?,?,?)",
                (object_id, encode(payload), created_at),
            )
        return self.object(object_id)

    def object(self, object_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone()
            latest = db.execute(
                "SELECT id FROM flights WHERE object_id=? "
                "ORDER BY uploaded_at DESC, rowid DESC LIMIT 1",
                (object_id,),
            ).fetchone()
        if row is None:
            raise ServiceError(404, "NOT_FOUND", "Объект не найден")
        payload = json.loads(row["payload"])
        project_material = (payload.get("project") or {}).get("wall_material")
        material = json.loads(row["material"]) if row["material"] else None
        last_flight = self.flight(latest["id"]) if latest else None
        result = last_flight["material_result"] if last_flight else None
        readiness = last_flight["result"] if last_flight else None
        effective = (last_flight or {}).get("readiness_review") or readiness or {}
        value = {
            **payload,
            "id": row["id"],
            "created_at": row["created_at"],
            "material": fixed_material_response(material, project_material),
            "last_flight": last_flight,
            "last_flight_id": last_flight["flight_id"] if last_flight else None,
            "last_flight_at": last_flight["flown_at"] if last_flight else None,
            "material_status": result["status"] if result else None,
            "material_data_status": material_data_status(last_flight),
            "readiness_status": readiness["status"] if readiness else None,
            "readiness_data_status": readiness_data_status(last_flight),
            "data_status": processing_status(last_flight),
            "stage": effective.get("stage"),
            "readiness_pct": effective.get("readiness_pct"),
            "bank_readiness": effective.get("bank_readiness"),
            "valuation_inputs": json.loads(row["valuation_inputs"])
            if row["valuation_inputs"]
            else {},
        }
        value["valuation"] = valuation_for_object(value, value["valuation_inputs"])
        return value

    def objects(
        self,
        limit: int,
        offset: int,
        *,
        q: str = "",
        stage: str | None = None,
        data_status: str | None = None,
        overdue: bool | None = None,
        pledged: bool | None = None,
        sort: str = "-created_at",
    ) -> dict:
        with self.connection() as db:
            ids = db.execute(
                "SELECT id FROM objects ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        items = [self.object(row["id"]) for row in ids]
        query = q.strip().casefold()
        if query:
            items = [
                item
                for item in items
                if query
                in " ".join(
                    str(item.get(key) or "") for key in ("address", "cadastral", "loan_id")
                ).casefold()
            ]
        for key, value in (
            ("stage", stage),
            ("data_status", data_status),
            ("overdue", overdue),
            ("pledged", pledged),
        ):
            if value is not None:
                items = [item for item in items if item.get(key, False) == value]
        field = sort.lstrip("-")
        items.sort(
            key=lambda item: (
                (item.get(field) or 0)
                if field == "readiness_pct"
                else str(item.get(field) or "").casefold()
            ),
            reverse=sort.startswith("-"),
        )
        return {"items": items[offset : offset + limit], "total": len(items)}

    def enqueue(self, flight: dict, images: list[dict]) -> None:
        with self.connection() as db:
            db.execute(
                """INSERT INTO flights
                (id,object_id,status,uploaded_at,flown_at,target_confirmed,is_demo)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    flight["id"],
                    flight["object_id"],
                    "queued",
                    utc_now(),
                    flight["flown_at"],
                    flight["target_confirmed"],
                    flight["is_demo"],
                ),
            )
            db.executemany(
                "INSERT INTO images(id,flight_id,payload) VALUES (?,?,?)",
                [(image["id"], flight["id"], encode(image)) for image in images],
            )

    def flight(self, flight_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
            count = db.execute(
                "SELECT count(*) FROM images WHERE flight_id=?", (flight_id,)
            ).fetchone()[0]
            review = db.execute(
                "SELECT payload FROM material_reviews WHERE flight_id=? ORDER BY id DESC LIMIT 1",
                (flight_id,),
            ).fetchone()
            readiness_review = db.execute(
                "SELECT id,payload FROM readiness_reviews WHERE flight_id=? ORDER BY id DESC LIMIT 1",
                (flight_id,),
            ).fetchone()
        if row is None:
            raise ServiceError(404, "NOT_FOUND", "Облёт не найден")
        return {
            "flight_id": row["id"],
            "object_id": row["object_id"],
            "status": row["status"],
            "uploaded_at": row["uploaded_at"],
            "flown_at": row["flown_at"],
            "target_confirmed": bool(row["target_confirmed"]),
            "mock": bool(row["is_demo"]),
            "images_count": count,
            "result": json.loads(row["readiness"]) if row["readiness"] else None,
            "land_result": None,
            "material_result": material_result_response(
                json.loads(row["result"]) if row["result"] else None
            ),
            "error": json.loads(row["error"]) if row["error"] else None,
            "material_review": json.loads(review["payload"]) if review else None,
            "review": self._readiness_review(readiness_review, flight_id)
            if readiness_review
            else None,
            "readiness_review": self._readiness_review(readiness_review, flight_id)
            if readiness_review
            else None,
            "module_errors": json.loads(row["module_errors"]) if row["module_errors"] else {},
        }

    @staticmethod
    def _readiness_review(row: sqlite3.Row, flight_id: str) -> dict:
        return {"id": f"rev_{row['id']}", "flight_id": flight_id, **json.loads(row["payload"])}

    def detections(self, flight_id: str) -> dict[str, list[dict]]:
        self.flight(flight_id)
        with self.connection() as db:
            row = db.execute("SELECT detections FROM flights WHERE id=?", (flight_id,)).fetchone()
        return json.loads(row["detections"]) if row["detections"] else {}

    def flights(self, object_id: str) -> list[dict]:
        self.object(object_id)
        with self.connection() as db:
            ids = db.execute(
                "SELECT id FROM flights WHERE object_id=? ORDER BY uploaded_at DESC, rowid DESC",
                (object_id,),
            ).fetchall()
        return [self.flight(row["id"]) for row in ids]

    def images(self, flight_id: str) -> list[dict]:
        self.flight(flight_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT payload FROM images WHERE flight_id=? ORDER BY rowid", (flight_id,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def image(self, image_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "NOT_FOUND", "Снимок не найден")
        return json.loads(row["payload"])

    def update_image(self, image: dict, token: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE images SET payload=? WHERE id=? AND EXISTS "
                "(SELECT 1 FROM flights WHERE id=images.flight_id AND status='processing' AND lease_token=?)",
                (encode(image), image["id"], token),
            )

    def claim(self, lease_seconds: int) -> tuple[str, str] | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = "status='processing' AND lease_until < ?"
            db.execute(
                f"UPDATE flights SET status='failed',error=? WHERE {expired} AND attempts>=3",
                (
                    encode(
                        {"code": "WORKER_INTERRUPTED", "message": "Обработка прерывалась трижды"}
                    ),
                    time.time(),
                ),
            )
            row = db.execute(
                f"SELECT id FROM flights WHERE status='queued' OR ({expired}) "
                "ORDER BY uploaded_at LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            db.execute(
                "UPDATE flights SET status='processing',lease_token=?,lease_until=?,"
                "attempts=attempts+1 WHERE id=?",
                (token, time.time() + lease_seconds, row["id"]),
            )
            return row["id"], token

    def renew(self, flight_id: str, token: str, lease_seconds: int) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE flights SET lease_until=? WHERE id=? AND lease_token=?",
                (time.time() + lease_seconds, flight_id, token),
            )

    def finish(
        self,
        flight_id: str,
        token: str,
        *,
        result: dict | None = None,
        readiness: dict | None = None,
        detections: dict | None = None,
        module_errors: dict | None = None,
        error: dict | None = None,
    ) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT object_id FROM flights WHERE id=? AND "
                "status='processing' AND lease_token=?",
                (flight_id, token),
            ).fetchone()
            if job is None:
                return False
            if result and result["fixed_on_object"]:
                previous = db.execute(
                    "SELECT material FROM objects WHERE id=?", (job["object_id"],)
                ).fetchone()[0]
                if previous:
                    result = {**result, "fixed_on_object": False}
                    if json.loads(previous)["observed"] != result["material"]:
                        result.update(
                            status="needs_review",
                            match=(
                                "no_project" if result["project_material"] is None else "unclear"
                            ),
                        )
                        result["review_reasons"] = [
                            *result["review_reasons"],
                            "PREVIOUS_MATERIAL_CONFLICT",
                        ]
                        result["explanation"] += (
                            " Результат расходится с сохранённым материалом: нужна проверка."
                        )
            changed = db.execute(
                "UPDATE flights SET status=?,result=?,readiness=?,detections=?,module_errors=?,error=?,lease_until=NULL "
                "WHERE id=? AND status='processing' AND lease_token=?",
                (
                    "failed" if error else "done",
                    encode(result) if result else None,
                    encode(readiness) if readiness else None,
                    encode(detections) if detections is not None else None,
                    encode(module_errors or {}),
                    encode(error) if error else None,
                    flight_id,
                    token,
                ),
            ).rowcount
            if changed and result and result["fixed_on_object"]:
                object_id = db.execute(
                    "SELECT object_id FROM flights WHERE id=?", (flight_id,)
                ).fetchone()[0]
                current = db.execute(
                    "SELECT material FROM objects WHERE id=?", (object_id,)
                ).fetchone()[0]
                if current is None:
                    db.execute(
                        "UPDATE objects SET material=? WHERE id=?",
                        (
                            encode(
                                {
                                    "observed": result["material"],
                                    "confidence": result["confidence"],
                                    "fixed_at_flight_id": flight_id,
                                    "reviewed": False,
                                }
                            ),
                            object_id,
                        ),
                    )
            return bool(changed)

    def add_review(self, flight_id: str, review: dict) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT object_id,status FROM flights WHERE id=?", (flight_id,)
            ).fetchone()
            if row is None:
                raise ServiceError(404, "NOT_FOUND", "Облёт не найден")
            if row["status"] != "done":
                raise ServiceError(409, "FLIGHT_NOT_READY", "Дождитесь окончания обработки")
            db.execute(
                "INSERT INTO material_reviews(flight_id,payload) VALUES (?,?)",
                (flight_id, encode(review)),
            )
            db.execute(
                "UPDATE objects SET material=? WHERE id=?",
                (
                    encode(
                        {
                            "observed": review["material"],
                            "confidence": None,
                            "fixed_at_flight_id": flight_id,
                            "reviewed": True,
                            "author": review["author"],
                            "comment": review["comment"],
                        }
                    ),
                    row["object_id"],
                ),
            )
        return review

    def add_readiness_review(self, flight_id: str, review: dict) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT status FROM flights WHERE id=?", (flight_id,)).fetchone()
            if row is None:
                raise ServiceError(404, "NOT_FOUND", "Облёт не найден")
            if row["status"] != "done":
                raise ServiceError(409, "FLIGHT_NOT_READY", "Дождитесь окончания обработки")
            review_id = db.execute(
                "INSERT INTO readiness_reviews(flight_id,payload) VALUES (?,?)",
                (flight_id, encode(review)),
            ).lastrowid
        return {"id": f"rev_{review_id}", "flight_id": flight_id, **review}

    def actions(self, object_id: str) -> list[dict]:
        self.object(object_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT 'material' AS kind, r.payload FROM material_reviews r JOIN flights f ON f.id=r.flight_id WHERE f.object_id=? "
                "UNION ALL SELECT 'readiness', r.payload FROM readiness_reviews r JOIN flights f ON f.id=r.flight_id WHERE f.object_id=?",
                (object_id, object_id),
            ).fetchall()
        actions = [
            {
                **json.loads(row["payload"]),
                "kind": row["kind"],
                "label": "Проверка материала"
                if row["kind"] == "material"
                else "Проверка готовности",
            }
            for row in rows
        ]
        return sorted(actions, key=lambda action: action["created_at"])

    def history(self, object_id: str) -> dict:
        obj = self.object(object_id)
        plan = []
        if obj.get("started_at") and obj.get("planned_completion_at"):
            plan = [
                {"date": obj["started_at"], "pct": 0},
                {"date": obj["planned_completion_at"], "pct": 100},
            ]
        fact = []
        for flight in reversed(self.flights(object_id)):
            value = flight["readiness_review"] or flight["result"] or {}
            if flight["status"] == "done" and value.get("readiness_pct") is not None:
                fact.append(
                    {
                        "flight_id": flight["flight_id"],
                        "flown_at": flight["flown_at"] or flight["uploaded_at"],
                        "stage": value["stage"],
                        "readiness_pct": value["readiness_pct"],
                        "bank_readiness": value["bank_readiness"],
                        "reviewed": flight["readiness_review"] is not None,
                    }
                )
        return {"plan": plan, "fact": fact, "forecast": None, "steps": []}
