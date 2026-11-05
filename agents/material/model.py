from importlib import import_module
from typing import Protocol

from PIL import Image

from api.config import Settings
from schemas.material import Observation
from schemas.readiness import Detection


class MaterialPredictor(Protocol):
    """The ML team implements this interface without changing HTTP or storage."""

    version: str
    is_demo: bool

    def predict(self, image: Image.Image, image_id: str) -> list[Observation]: ...


class DemoPredictor:
    version = "demo-unavailable@v1"
    is_demo = True

    def predict(self, image: Image.Image, image_id: str) -> list[Observation]:
        # A demo must not claim to recognise the actual material in a photograph.
        return [Observation(image_id=image_id, visibility="unclear")]


class WallMaterialPredictor(Protocol):
    version: str
    is_demo: bool

    def predict_walls(
        self, image: Image.Image, image_id: str, detections: list[Detection]
    ) -> list[Observation]: ...


def load_predictor(settings: Settings) -> MaterialPredictor | WallMaterialPredictor:
    if settings.mock:
        return DemoPredictor()
    if not settings.model_factory:
        raise ValueError("При MOCK=0 задайте MATERIAL_MODEL_FACTORY=module:factory")
    module, separator, factory = settings.model_factory.partition(":")
    if not separator or not module or not factory:
        raise ValueError("MATERIAL_MODEL_FACTORY должен иметь формат module:factory")
    model = getattr(import_module(module), factory)()
    if not isinstance(model.version, str) or not isinstance(model.is_demo, bool):
        raise TypeError("У адаптера должны быть version: str и is_demo: bool")
    if model.is_demo:
        raise ValueError("При MOCK=0 адаптер должен использовать настоящую модель")
    return model
