import json
from uuid import uuid4

from agents.valuation.calculator import valuation_for_object
from api.database import Database, encode
from schemas.material import utc_now
from schemas.valuation import ValuationInputs


class ValuationRepository:
    def __init__(self, database: Database):
        self.database = database

    def calculate(self, object_id: str, changes: dict, author: str) -> dict:
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            obj = self.database.object(object_id)
            parameters = ValuationInputs.model_validate(
                {**obj["valuation_inputs"], **changes}
            ).model_dump(mode="json")
            result = valuation_for_object(obj, parameters)
            run_id, now = "val_" + uuid4().hex, utc_now()
            result.update(id=run_id, author=author, calculated_at=now)
            db.execute(
                "UPDATE objects SET valuation_inputs=? WHERE id=?", (encode(parameters), object_id)
            )
            db.execute(
                "INSERT INTO valuation_runs(id,object_id,inputs,result,fingerprint,author,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    object_id,
                    encode(parameters),
                    encode(result),
                    result["fingerprint"],
                    author,
                    now,
                ),
            )
        return result

    def history(self, object_id: str) -> list[dict]:
        obj = self.database.object(object_id)
        current = obj["valuation"]["fingerprint"]
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT * FROM valuation_runs WHERE object_id=? ORDER BY rowid DESC", (object_id,)
            ).fetchall()
        return [
            {
                **json.loads(row["result"]),
                "inputs": json.loads(row["inputs"]),
                "stale": current != row["fingerprint"],
            }
            for row in rows
        ]
