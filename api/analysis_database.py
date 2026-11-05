import json
import time
from uuid import uuid4

from api.database import Database, encode
from api.errors import ServiceError
from schemas.material import utc_now


class AnalysisRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _decode(row) -> dict:
        value = dict(row)
        for key in ("context", "recommendation", "usage"):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    def get(self, analysis_id: str) -> dict:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "NOT_FOUND", "AI-заключение не найдено")
        return self._decode(row)

    def latest(self, object_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM analyses WHERE object_id=? ORDER BY rowid DESC LIMIT 1", (object_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def enqueue(
        self, object_id: str, context: dict, fingerprint: str, model: str, *, force: bool = False
    ) -> dict:
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM analyses WHERE object_id=? AND fingerprint=? "
                "AND (status IN ('queued','processing') OR (llm_status='accepted' AND ?=0)) "
                "ORDER BY rowid DESC LIMIT 1",
                (object_id, fingerprint, force),
            ).fetchone()
            if existing:
                return self._decode(existing)
            analysis_id, now = "an_" + uuid4().hex, utc_now()
            db.execute(
                "INSERT INTO analyses(id,object_id,fingerprint,context,requested_model,created_at,updated_at,status,llm_status) "
                "VALUES(?,?,?,?,?,?,?,'queued','pending')",
                (analysis_id, object_id, fingerprint, encode(context), model, now, now),
            )
        return self.get(analysis_id)

    def claim(self, lease_seconds: int) -> tuple[str, str] | None:
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE analyses SET status='done',llm_status='unavailable',error_code='WORKER_INTERRUPTED',"
                "error_message='Обработка заключения прерывалась трижды',updated_at=? "
                "WHERE status='processing' AND lease_until<? AND attempts>=3",
                (utc_now(), time.time()),
            )
            row = db.execute(
                "SELECT id FROM analyses WHERE status='queued' OR (status='processing' AND lease_until<?) "
                "ORDER BY created_at,rowid LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            db.execute(
                "UPDATE analyses SET status='processing',lease_token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                (token, time.time() + lease_seconds, utc_now(), row["id"]),
            )
            return row["id"], token

    def finish(
        self,
        analysis_id: str,
        token: str,
        *,
        llm_status: str,
        recommendation: dict | None = None,
        raw_response: str | None = None,
        response_model: str | None = None,
        usage: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        if llm_status not in {"accepted", "unavailable", "rejected"} or (
            recommendation is not None
        ) != (llm_status == "accepted"):
            raise ValueError("Invalid analysis completion")
        with self.database.connection() as db:
            changed = db.execute(
                "UPDATE analyses SET status='done',llm_status=?,recommendation=?,raw_response=?,response_model=?,usage=?,"
                "error_code=?,error_message=?,updated_at=?,lease_until=NULL WHERE id=? AND status='processing' AND lease_token=?",
                (
                    llm_status,
                    encode(recommendation) if recommendation else None,
                    raw_response[:20000] if raw_response else None,
                    response_model,
                    encode(usage or {}),
                    error_code,
                    error_message,
                    utc_now(),
                    analysis_id,
                    token,
                ),
            ).rowcount
        return bool(changed)
