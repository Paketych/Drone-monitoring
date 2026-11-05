import json
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from agents.analysis.service import AnalysisService
from agents.material.config import material_config
from agents.readiness.config import classes_config, stages_config
from agents.readiness.views import assign_facade_sides, overhead_view, promote_low_overhead
from api.config import Settings
from api.database import Database
from api.errors import ServiceError
from api.images import inspect_image
from api.presentation import MATERIAL_REVIEW_REASONS, MATERIAL_STATUSES
from api.valuation_database import ValuationRepository
from schemas.analysis import AnalysisRequest
from schemas.material import (
    MATERIALS,
    TOP_VIEWS,
    ImageMeta,
    MaterialReview,
    ObjectCreate,
    View,
    utc_now,
)
from schemas.readiness import ReadinessReview, Stage
from schemas.valuation import ValuationInputs


def parse_list(raw: str | None, count: int, name: str) -> list:
    if raw is None:
        return [None] * count
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServiceError(
            400, "VALIDATION_ERROR", f"Поле {name} должно содержать JSON-массив"
        ) from exc
    if not isinstance(values, list) or len(values) != count:
        raise ServiceError(400, "VIEWS_MISMATCH", f"Длина {name} должна совпадать с числом файлов")
    return values


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    analysis = AnalysisService(settings, database)
    valuations = ValuationRepository(database)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        yield

    app = FastAPI(
        title="Мониторинг ИЖС: материалы и готовность",
        version="0.5.0",
        lifespan=lifespan,
        description=(
            "Загрузка снимков, материалы стен и готовность строительства. "
            "В деморежиме обученная модель не вызывается."
        ),
    )
    app.state.settings = settings
    app.state.database = database
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceError)
    async def service_error(request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {},
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Проверьте поля запроса",
                    "details": {
                        "errors": [
                            {"loc": list(error["loc"]), "message": error["msg"]}
                            for error in exc.errors()
                        ]
                    },
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                    "message": "Ресурс не найден" if exc.status_code == 404 else str(exc.detail),
                    "details": {},
                }
            },
        )

    prefix = "/api/v1"

    @app.get(prefix + "/health", tags=["Служебные"])
    def health():
        return {"status": "ok", "scope": "inspection", "mock": settings.mock}

    @app.get(prefix + "/meta", tags=["Служебные"])
    def metadata():
        classes, stages = classes_config(), stages_config()
        material_rules = material_config()
        return {
            "scope": "inspection",
            "material_rules_version": material_rules.version,
            "material_thresholds": material_rules.thresholds(),
            "mock": settings.mock,
            "capabilities": {
                "wall_material": True,
                "material_review": True,
                "target_region_filter": True,
                "automatic_target_identification": False,
                "readiness": True,
                "readiness_review": True,
                "risk": False,
                "conductor": False,
                "llm_analysis": True,
                "alerts": False,
                "tasks": False,
                "history": True,
                "actions": True,
                "land": False,
                "valuation": True,
                "act": False,
                "registry_import": False,
            },
            "material_model_version": "demo-unavailable@v1"
            if settings.mock
            else "external-adapter",
            "model_version": "demo-unavailable@v1" if settings.mock else "external-adapter",
            "model_configured": {
                "material": not settings.mock and bool(settings.model_factory),
                "readiness": not settings.mock and bool(settings.detector_factory),
            },
            "rules_version": stages.version,
            "materials": [
                {"id": key, "label": value[0], "group": value[1]}
                for key, value in MATERIALS.items()
            ]
            + [
                {"id": "hidden", "label": "Скрыт отделкой", "group": None},
                {"id": "unclear", "label": "Не определить", "group": None},
            ],
            "material_statuses": [
                {"id": key, "label": label} for key, label in MATERIAL_STATUSES.items()
            ],
            "material_review_reasons": [
                {"id": key, "label": label} for key, label in MATERIAL_REVIEW_REASONS.items()
            ],
            "views": [item.model_dump() for item in classes.views],
            "classes": [
                {"id": item.id, "ml_id": item.ml_id, "label": item.label, "color": item.color}
                for item in classes.classes
            ],
            "stages": [{**item.model_dump(), "order": i} for i, item in enumerate(stages.stages)],
            "bank_readiness": [item.model_dump() for item in stages.bank_readiness],
            "reshoot_reasons": [
                {"id": key, "label": label} for key, label in stages.reshoot_reasons.items()
            ],
        }

    @app.post(prefix + "/objects", status_code=201, tags=["Объекты"])
    def create_object(body: ObjectCreate):
        return database.create_object(body.model_dump(mode="json"))

    @app.get(prefix + "/llm/status", tags=["LLM"])
    def llm_status():
        return settings.llm.public_status(settings.mock)

    @app.get(prefix + "/objects/{object_id}/analysis", tags=["LLM"])
    def get_analysis(object_id: str):
        return analysis.latest(object_id)

    @app.post(prefix + "/objects/{object_id}/analysis", status_code=202, tags=["LLM"])
    def request_analysis(object_id: str, body: AnalysisRequest | None = None):
        record = analysis.enqueue(object_id, force=body.force if body else False)
        return analysis.public(record, object_id)

    @app.get(prefix + "/analyses/{analysis_id}", tags=["LLM"])
    def get_analysis_job(analysis_id: str):
        record = analysis.repository.get(analysis_id)
        return analysis.public(record, record["object_id"])

    @app.get(prefix + "/objects", tags=["Объекты"])
    def list_objects(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        q: Annotated[str, Query(max_length=500)] = "",
        stage: Stage | None = None,
        data_status: Literal["ok", "processing", "review", "reshoot", "no_data"] | None = None,
        overdue: bool | None = None,
        pledged: bool | None = None,
        sort: Literal[
            "address",
            "-address",
            "created_at",
            "-created_at",
            "last_flight_at",
            "-last_flight_at",
            "readiness_pct",
            "-readiness_pct",
        ] = "-created_at",
    ):
        return {
            **database.objects(
                page_size,
                (page - 1) * page_size,
                q=q,
                stage=stage,
                data_status=data_status,
                overdue=overdue,
                pledged=pledged,
                sort=sort,
            ),
            "page": page,
            "page_size": page_size,
        }

    @app.get(prefix + "/objects/{object_id}", tags=["Объекты"])
    def get_object(object_id: str):
        return database.object(object_id)

    @app.get(prefix + "/objects/{object_id}/valuation", tags=["Стоимость строительства"])
    def get_valuation(object_id: str):
        return database.object(object_id)["valuation"]

    @app.post(prefix + "/objects/{object_id}/valuation", tags=["Стоимость строительства"])
    def calculate_cost(
        object_id: str,
        body: ValuationInputs | None = None,
        x_user: Annotated[str, Header(max_length=100)] = "demo",
    ):
        result = valuations.calculate(
            object_id, body.model_dump(mode="json", exclude_unset=True) if body else {}, x_user
        )
        analysis.automatic(object_id)
        return result

    @app.get(prefix + "/objects/{object_id}/valuation/history", tags=["Стоимость строительства"])
    def valuation_history(object_id: str):
        return valuations.history(object_id)

    @app.get(prefix + "/objects/{object_id}/history", tags=["Объекты"])
    def get_history(object_id: str):
        return database.history(object_id)

    @app.get(prefix + "/objects/{object_id}/actions", tags=["Объекты"])
    def get_actions(object_id: str):
        return database.actions(object_id)

    @app.post(prefix + "/objects/{object_id}/flights", status_code=202, tags=["Облёты"])
    def upload_flight(
        object_id: str,
        files: Annotated[list[UploadFile], File()],
        views: Annotated[str | None, Form()] = None,
        meta: Annotated[str | None, Form()] = None,
        flown_at: Annotated[str | None, Form()] = None,
        target_confirmed: Annotated[bool, Form()] = False,
        x_user: Annotated[str, Header(max_length=100)] = "demo",
    ):
        directory: Path | None = None
        committed = False
        try:
            database.object(object_id)
            if not 1 <= len(files) <= settings.max_files:
                raise ServiceError(400, "VALIDATION_ERROR", "Нужно от 1 до 20 снимков")
            view_values = parse_list(views, len(files), "views")
            meta_values = parse_list(meta, len(files), "meta")
            try:
                parsed_views = [View(value) if value is not None else None for value in view_values]
                parsed_meta = [
                    ImageMeta.model_validate({} if value is None else value)
                    for value in meta_values
                ]
                timestamp = datetime.fromisoformat(flown_at) if flown_at else None
                if timestamp is not None and timestamp.utcoffset() is None:
                    raise ValueError("Нет часового пояса")
            except (ValueError, TypeError, ValidationError) as exc:
                raise ServiceError(
                    400, "VALIDATION_ERROR", "Проверьте ракурсы, GPS и дату облёта с часовым поясом"
                ) from exc

            flight_id = "fl_" + uuid4().hex
            directory = settings.storage_dir / "flights" / flight_id
            directory.mkdir(parents=True)
            images = []
            hashes = set()
            for incoming, view, manual_meta in zip(files, parsed_views, parsed_meta):
                image_id = "img_" + uuid4().hex
                path = directory / f"{image_id}.upload"
                digest, size = sha256(), 0
                with path.open("xb") as destination:
                    while chunk := incoming.file.read(1024 * 1024):
                        size += len(chunk)
                        if size > settings.max_file_bytes:
                            raise ServiceError(
                                413, "FILE_TOO_LARGE", "Снимок превышает лимит 30 МБ"
                            )
                        destination.write(chunk)
                        digest.update(chunk)
                metadata = inspect_image(path, settings)
                fingerprint = digest.hexdigest()
                if fingerprint in hashes:
                    raise ServiceError(
                        400, "DUPLICATE_IMAGE", "В одном облёте передан один и тот же снимок дважды"
                    )
                hashes.add(fingerprint)
                if not metadata["has_exif"] and view is None:
                    raise ServiceError(
                        400, "VIEWS_REQUIRED", "Без EXIF укажите ракурс снимка в views"
                    )
                if view is not None:
                    metadata.update(view=view.value, view_source="manual")
                elif metadata["is_overhead"] and (
                    role := overhead_view(metadata.get("relative_altitude"))
                ):
                    metadata.update(view=role, view_source="exif")
                if view in TOP_VIEWS:
                    metadata["is_overhead"] = True
                if manual_meta.gps is not None:
                    metadata.update(gps=list(manual_meta.gps), gps_source="meta")
                metadata.update(
                    target_bbox=manual_meta.target_bbox,
                    target_region_source="provided" if manual_meta.target_bbox else None,
                    target_selected_by=x_user if manual_meta.target_bbox else None,
                    target_selected_at=utc_now() if manual_meta.target_bbox else None,
                )
                suffix = ".jpg" if metadata["format"] == "JPEG" else ".png"
                stored = directory / f"{image_id}{suffix}"
                path.rename(stored)
                images.append(
                    {
                        **metadata,
                        "id": image_id,
                        "flight_id": flight_id,
                        "filename": (incoming.filename or "image")
                        .replace("\\", "/")
                        .split("/")[-1],
                        "storage_name": stored.name,
                        "size_bytes": size,
                        "sha256": fingerprint,
                        "url": f"{prefix}/images/{image_id}/file",
                        "thumb_url": f"{prefix}/images/{image_id}/thumb",
                        "technical_ok": True,
                        "quality_ok": None,
                    }
                )
            promote_low_overhead(images)
            suggested = [dict(image) for image in images]
            assign_facade_sides(suggested)
            for image, suggestion in zip(images, suggested):
                image["suggested_view"] = suggestion["view"] if image["view"] is None else None
                if image["suggested_view"] is not None:
                    # A manual view always wins; otherwise the camera azimuth gives the side.
                    image.update(view=image["suggested_view"], view_source="exif")
            dates = [image["taken_at"] for image in images if image["taken_at"]]
            database.enqueue(
                {
                    "id": flight_id,
                    "object_id": object_id,
                    "flown_at": timestamp.isoformat()
                    if timestamp
                    else min(dates, key=datetime.fromisoformat, default=None),
                    "target_confirmed": target_confirmed,
                    "is_demo": settings.mock,
                },
                images,
            )
            committed = True
            return {"flight_id": flight_id, "status": "queued", "mock": settings.mock}
        finally:
            for incoming in files:
                incoming.file.close()
            if directory is not None and not committed:
                # Only this request's generated files are removed, never user source images.
                for generated in directory.iterdir():
                    generated.unlink()
                directory.rmdir()

    @app.get(prefix + "/objects/{object_id}/flights", tags=["Облёты"])
    def list_flights(object_id: str):
        return database.flights(object_id)

    @app.get(prefix + "/flights/{flight_id}", tags=["Облёты"])
    def get_flight(flight_id: str):
        return database.flight(flight_id)

    @app.get(prefix + "/flights/{flight_id}/images", tags=["Снимки"])
    def get_images(flight_id: str):
        detections = database.detections(flight_id)
        return [
            {
                **{key: value for key, value in image.items() if key != "storage_name"},
                "detections": detections.get(image["id"], []),
            }
            for image in database.images(flight_id)
        ]

    def image_path(image_id: str, kind: str) -> tuple[dict, Path]:
        image = database.image(image_id)
        name = image["storage_name"] if kind == "file" else f"{image_id}.thumb.jpg"
        path = settings.storage_dir / "flights" / image["flight_id"] / name
        if not path.is_file():
            raise ServiceError(409, "PREVIEW_NOT_READY", "Превью появится после обработки снимка")
        return image, path

    @app.get(prefix + "/images/{image_id}/file", tags=["Снимки"])
    def get_original(image_id: str):
        image, path = image_path(image_id, "file")
        return FileResponse(
            path,
            filename=image["filename"],
            media_type=("image/jpeg" if image["format"] == "JPEG" else "image/png"),
        )

    @app.get(prefix + "/images/{image_id}/thumb", tags=["Снимки"])
    def get_thumbnail(image_id: str):
        _, path = image_path(image_id, "thumb")
        return FileResponse(path, media_type="image/jpeg")

    @app.get(prefix + "/images/{image_id}/crops/{crop_index}", tags=["Снимки"])
    def get_crop(
        image_id: str,
        crop_index: int,
        revision: Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")],
    ):
        image = database.image(image_id)
        path = (
            settings.storage_dir
            / "flights"
            / image["flight_id"]
            / f"{image_id}.crop.{revision}.{crop_index}.jpg"
        )
        if crop_index < 0 or not path.is_file():
            raise ServiceError(404, "NOT_FOUND", "Участок стены не найден")
        return FileResponse(path, media_type="image/jpeg")

    @app.post(prefix + "/flights/{flight_id}/material/review", status_code=201, tags=["Проверка"])
    def review_material(
        flight_id: str,
        body: MaterialReview,
        x_user: Annotated[str, Header(max_length=100)] = "demo",
    ):
        flight = database.flight(flight_id)
        if flight["status"] != "done":
            raise ServiceError(409, "FLIGHT_NOT_READY", "Дождитесь окончания обработки")
        result = flight["material_result"]
        material = body.material
        if body.action == "accept":
            if result is None or result["is_demo"] or result["material"] not in MATERIALS:
                raise ServiceError(
                    409, "MATERIAL_NOT_DETERMINED", "Нет результата модели, который можно принять"
                )
            material = result["material"]
        value = database.add_review(
            flight_id,
            jsonable_encoder(
                {
                    **body.model_dump(),
                    "material": material,
                    "author": x_user,
                    "created_at": utc_now(),
                    "source": "employee",
                }
            ),
        )
        analysis.automatic(flight["object_id"])
        return value

    @app.post(prefix + "/flights/{flight_id}/review", status_code=201, tags=["Проверка"])
    def review_readiness(
        flight_id: str,
        body: ReadinessReview,
        x_user: Annotated[str, Header(max_length=100)] = "demo",
    ):
        flight = database.flight(flight_id)
        if flight["status"] != "done":
            raise ServiceError(409, "FLIGHT_NOT_READY", "Дождитесь окончания обработки")
        values = body.model_dump(mode="json", exclude={"action", "comment"})
        if body.action == "accept":
            result = flight["result"]
            if result is None or result["is_demo"] or result["stage"] is None:
                raise ServiceError(
                    409, "STAGE_NOT_DETERMINED", "Нет результата модели, который можно принять"
                )
            values = {key: result[key] for key in ("stage", "readiness_pct", "bank_readiness")}
        value = database.add_readiness_review(
            flight_id,
            {
                "action": body.action,
                **values,
                "comment": body.comment,
                "author": x_user,
                "created_at": utc_now(),
                "source": "employee",
            },
        )
        analysis.automatic(flight["object_id"])
        return value

    return app


app = create_app()
