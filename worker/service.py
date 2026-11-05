import logging

from agents.analysis.service import AnalysisService
from agents.geo import flight_point
from agents.material.aggregation import aggregate
from agents.material.model import (
    DemoPredictor,
    MaterialPredictor,
    WallMaterialPredictor,
    load_predictor,
)
from agents.readiness import aggregation as readiness
from agents.readiness.model import DemoDetector, Detector, load_detector
from agents.readiness.quality import quality_issues
from agents.targeting import exclusion_reason, locate_target
from api.config import Settings
from api.database import Database
from api.images import load_oriented_image, save_crop, save_preview
from schemas.material import TOP_VIEWS, Observation, ViewObservation
from schemas.readiness import Detection

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        settings: Settings,
        predictor: MaterialPredictor | WallMaterialPredictor | None = None,
        detector: Detector | None = None,
        analysis_client=None,
    ) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.analysis = AnalysisService(settings, self.database, client=analysis_client)
        self._analysis_next = False
        self.load_errors: dict[str, dict] = {}
        self.predictor = (
            predictor
            if predictor is not None
            else self._load("material", load_predictor, DemoPredictor, settings.model_factory)
        )
        self.detector = (
            detector
            if detector is not None
            else self._load("readiness", load_detector, DemoDetector, settings.detector_factory)
        )

    def _load(self, name, loader, unavailable, factory):
        # Each model is independent. An unavailable model produces an explicit state.
        if not self.settings.mock and not factory:
            self.load_errors[name] = {
                "code": "MODEL_NOT_CONFIGURED",
                "message": "Модель не настроена",
            }
            model = unavailable()
            model.version = "unavailable@v1"
            logger.info("model_not_configured module=%s", name)
            return model
        try:
            return loader(self.settings)
        except Exception:
            logger.exception("model_unavailable module=%s", name)
            self.load_errors[name] = {
                "code": "MODEL_LOAD_FAILED" if factory else "MODEL_NOT_CONFIGURED",
                "message": "Модель не удалось загрузить" if factory else "Модель не настроена",
            }
            model = unavailable()
            model.version = "unavailable@v1"
            return model

    def process_one(self) -> bool:
        if self._analysis_next and self.analysis.process_one():
            self._analysis_next = False
            return True
        job = self.database.claim(self.settings.job_lease_seconds)
        if job is None:
            return self.analysis.process_one()
        self._analysis_next = True
        flight_id, token = job
        errors = dict(self.load_errors)
        try:
            flight = self.database.flight(flight_id)
            obj = self.database.object(flight["object_id"])
            images = self.database.images(flight_id)
            directory = self.settings.storage_dir / "flights" / flight_id
            observations: list[ViewObservation] = []
            detections: list[Detection] = []
            # The ground point the whole flight is aimed at; frames are matched against it.
            point = flight_point(images)
            for info in images:
                with load_oriented_image(directory / info["storage_name"]) as image:
                    save_preview(image, directory / f"{info['id']}.thumb.jpg")
                    issues = quality_issues(image)
                    info.update(quality_ok=not issues, quality_issues=issues)
                    self.database.update_image(info, token)
                    image_detections = []
                    if "readiness" not in errors:
                        try:
                            for found in self.detector.detect(image, info["id"]):
                                detection = Detection.model_validate(found)
                                if detection.image_id != info["id"]:
                                    raise ValueError("Детектор вернул другой image_id")
                                image_detections.append(detection)
                            if info.get("target_region_source") != "provided":
                                # Without a manual region: the house under the flight point,
                                # otherwise the house in the centre of the frame.
                                region, source, projected = locate_target(
                                    image_detections, info, point
                                )
                                info.update(
                                    target_bbox=region,
                                    target_region_source=source,
                                    target_point=projected,
                                )
                                self.database.update_image(info, token)
                            for detection in image_detections:
                                reason = exclusion_reason(detection.bbox, info)
                                detection.target_included = reason is None
                                detection.target_exclusion_reason = reason
                            detections.extend(image_detections)
                        except Exception:
                            self._inference_error("readiness", errors)
                            detections.clear()
                    if "material" not in errors:
                        try:
                            wall_predictor = getattr(self.predictor, "predict_walls", None)
                            if info["is_overhead"] or info["view"] in TOP_VIEWS:
                                predictions = [
                                    Observation(image_id=info["id"], visibility="unclear")
                                ]
                            elif wall_predictor is not None:
                                if "readiness" in errors or self.detector.is_demo:
                                    errors["material"] = {
                                        "code": "DETECTOR_REQUIRED",
                                        "message": "Для материала нужен работающий детектор стен",
                                    }
                                    observations.clear()
                                    predictions = []
                                else:
                                    predictions = wall_predictor(
                                        image, info["id"], image_detections
                                    )
                            else:
                                predictions = self.predictor.predict(image, info["id"])
                            for index, prediction in enumerate(predictions):
                                value = Observation.model_validate(prediction)
                                if value.image_id != info["id"]:
                                    raise ValueError("Адаптер вернул другой image_id")
                                view = ViewObservation(**value.model_dump())
                                box = value.bbox or value.crop_bbox
                                if box is not None:
                                    view.exclusion_reason = exclusion_reason(box, info)
                                if value.crop_bbox is not None:
                                    save_crop(
                                        image,
                                        value.crop_bbox,
                                        directory / f"{info['id']}.crop.{token}.{index}.jpg",
                                    )
                                    view.crop_url = f"/api/v1/images/{info['id']}/crops/{index}?revision={token}"
                                observations.append(view)
                        except Exception:
                            self._inference_error("material", errors)
                            observations.clear()
                self.database.renew(flight_id, token, self.settings.job_lease_seconds)
            result = aggregate(
                observations,
                images,
                model=self.predictor.version,
                is_demo=self.predictor.is_demo or "material" in errors,
                project_material=(obj.get("project") or {}).get("wall_material"),
                target_confirmed=flight["target_confirmed"],
            )
            stage = readiness.aggregate(
                detections,
                images,
                model=self.detector.version,
                is_demo=self.detector.is_demo or "readiness" in errors,
                target_confirmed=flight["target_confirmed"],
                parcel=obj.get("parcel_geojson"),
            )
            if "material" in errors:
                result.explanation = "Материал не определён: " + errors["material"]["message"] + "."
            if "readiness" in errors:
                stage.explanation = (
                    "Готовность не определена: " + errors["readiness"]["message"] + "."
                )
            saved = self.database.finish(
                flight_id,
                token,
                result=result.model_dump(mode="json"),
                readiness=stage.model_dump(mode="json"),
                detections=readiness.annotate(detections, images),
                module_errors=errors,
            )
            if saved:
                self.analysis.automatic(flight["object_id"])
            logger.info("flight_done flight_id=%s modules_failed=%s", flight_id, list(errors))
        except Exception:
            logger.exception("flight_failed flight_id=%s", flight_id)
            self.database.finish(
                flight_id,
                token,
                error={
                    "code": "IMAGE_PROCESSING_FAILED",
                    "message": "Не удалось подготовить снимки. Подробности в журнале воркера.",
                    "details": {},
                },
            )
        return True

    def close(self) -> None:
        self.analysis.close()

    @staticmethod
    def _inference_error(name: str, errors: dict) -> None:
        logger.exception("inference_failed module=%s", name)
        errors[name] = {"code": "INFERENCE_FAILED", "message": "Ошибка обработки моделью"}
