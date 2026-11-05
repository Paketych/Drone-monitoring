import logging
import shutil
import sqlite3
from pathlib import Path

from api.database import Database
from api.errors import ServiceError

logger = logging.getLogger(__name__)


def _directory(parent: Path, name: str) -> Path:
    path = parent / name
    if (
        not name
        or Path(name).name != name
        or name in {".", ".."}
        or path.resolve() != path
        or (path.exists() and not path.is_dir())
    ):
        raise ServiceError(
            409, "INVALID_STORAGE_PATH", "Нужно проверить хранилище фотографий объекта"
        )
    return path


def cleanup_deleted_files(database: Database, storage_dir: Path) -> bool:
    """Purge committed deletions or restore files after an interrupted transaction."""
    changed = False
    try:
        storage = storage_dir.resolve()
        flights = _directory(storage, "flights")
        trash = _directory(storage, ".trash")
        if not trash.exists():
            return False
        with database.connection() as db:
            # A live deletion owns this lock until it commits or restores the files.
            db.execute("BEGIN IMMEDIATE")
            pending = [p for p in trash.iterdir() if p.name.startswith("fl_")]
            for entry in pending[:10]:
                try:
                    source = _directory(trash, entry.name)
                    target = _directory(flights, entry.name)
                    exists = db.execute(
                        "SELECT 1 FROM flights WHERE id=?", (entry.name,)
                    ).fetchone()
                    if exists:
                        if target.exists():
                            raise OSError("Restoration target already exists")
                        flights.mkdir(parents=True, exist_ok=True)
                        source.rename(target)
                    else:
                        shutil.rmtree(source)
                    changed = True
                except (OSError, ServiceError) as error:
                    logger.warning(
                        "object_file_cleanup_pending error_type=%s", type(error).__name__
                    )
    except (OSError, sqlite3.Error, ServiceError) as error:
        logger.warning("object_file_cleanup_unavailable error_type=%s", type(error).__name__)
    return changed


def delete_object(database: Database, storage_dir: Path, object_id: str) -> dict:
    moved = []
    with database.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM objects WHERE id=?", (object_id,)).fetchone() is None:
            raise ServiceError(404, "NOT_FOUND", "Объект не найден")
        if (
            db.execute(
                "SELECT 1 FROM flights WHERE object_id=? AND status='processing'", (object_id,)
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM analyses WHERE object_id=? AND status='processing'", (object_id,)
            ).fetchone()
        ):
            raise ServiceError(
                409,
                "OBJECT_BUSY",
                "Дождитесь завершения обработки фотографий и AI-сводки, затем повторите удаление",
            )
        storage = storage_dir.resolve()
        flights = _directory(storage, "flights")
        # Keep staging paths shorter than photo paths, including on Windows MAX_PATH.
        trash = _directory(storage, ".trash")
        sources = [
            _directory(flights, row["id"])
            for row in db.execute("SELECT id FROM flights WHERE object_id=?", (object_id,))
        ]
        try:
            for source in sources:
                if not source.exists():
                    continue
                trash.mkdir(parents=True, exist_ok=True)
                destination = _directory(trash, source.name)
                if destination.exists():
                    raise OSError("Staging target already exists")
                source.rename(destination)
                moved.append((source, destination))
            for table in ("images", "material_reviews", "readiness_reviews"):
                db.execute(
                    f"DELETE FROM {table} WHERE flight_id IN (SELECT id FROM flights WHERE object_id=?)",
                    (object_id,),
                )
            for table in ("analyses", "valuation_runs", "flights"):
                db.execute(f"DELETE FROM {table} WHERE object_id=?", (object_id,))
            db.execute("DELETE FROM objects WHERE id=?", (object_id,))
            # Explicit commit keeps restoration inside the write lock on commit failure.
            db.commit()
        except Exception as error:
            for source, destination in reversed(moved):
                try:
                    if destination.exists():
                        destination.rename(source)
                except OSError:
                    logger.error("object_file_restoration_pending")
            if isinstance(error, (OSError, sqlite3.Error)):
                raise ServiceError(
                    500,
                    "OBJECT_DELETE_FAILED",
                    "Не удалось удалить объект. Данные сохранены; повторите попытку",
                ) from None
            raise
    cleanup_deleted_files(database, storage_dir)
    return {"id": object_id, "deleted": True}
