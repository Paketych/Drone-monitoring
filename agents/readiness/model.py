from importlib import import_module
from typing import Protocol

from PIL import Image

from api.config import Settings
from schemas.readiness import Detection


class Detector(Protocol):
    """The ML team implements this interface; rules and storage stay unchanged."""

    version: str
    is_demo: bool

    def detect(self, image: Image.Image, image_id: str) -> list[Detection]: ...


class DemoDetector:
    version = "demo-unavailable@v1"
    is_demo = True

    def detect(self, image: Image.Image, image_id: str) -> list[Detection]:
        # A demo must not claim to see construction elements in a photograph.
        return []


def load_detector(settings: Settings) -> Detector:
    if settings.mock:
        return DemoDetector()
    if not settings.detector_factory:
        raise ValueError("При MOCK=0 задайте DETECTOR_FACTORY=module:factory")
    module, separator, factory = settings.detector_factory.partition(":")
    if not separator or not module or not factory:
        raise ValueError("DETECTOR_FACTORY должен иметь формат module:factory")
    model = getattr(import_module(module), factory)()
    if not isinstance(model.version, str) or not isinstance(model.is_demo, bool):
        raise TypeError("У детектора должны быть version: str и is_demo: bool")
    if model.is_demo:
        raise ValueError("При MOCK=0 детектор должен использовать настоящую модель")
    return model
