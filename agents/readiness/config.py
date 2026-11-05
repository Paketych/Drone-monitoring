from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from schemas.material import Contract, Score
from schemas.readiness import STAGE_CLASSES, DetectorClass, Stage

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
ViewGroup = Literal["facade", "top_low", "top_high"]


class Label(Contract):
    id: str | int
    label: str


class ClassConfig(Contract):
    id: DetectorClass
    ml_id: int = Field(ge=0)
    label: str
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    views: list[ViewGroup] = Field(min_length=1)


class AutoTargetConfig(Contract):
    anchor_classes: list[DetectorClass] = Field(min_length=1)
    min_conf: Score
    centre_zone: float = Field(gt=0, le=0.5)
    merge_overlap: float = Field(gt=0, le=1)
    stack_overlap: float = Field(gt=0, le=1)
    stack_gap: float = Field(ge=0, le=1)
    margin: float = Field(ge=0, le=0.5)


class FlightPointConfig(Contract):
    min_tilt_deg: float = Field(gt=0, le=90)
    min_altitude_m: float = Field(ge=0)
    max_point_distance: float = Field(ge=0, le=1)


class ClassesConfig(Contract):
    version: str
    views: list[Label]
    top_high_min_relative_altitude_m: float | None = None
    top_low_relative_ratio: float | None = Field(default=None, gt=1)
    facade_side_tolerance_deg: float | None = Field(default=None, gt=0, lt=45)
    flight_point: FlightPointConfig | None = None
    auto_target: AutoTargetConfig | None = None
    classes: list[ClassConfig]

    @model_validator(mode="before")
    @classmethod
    def normalize_ml_classes(cls, value: dict) -> dict:
        data = dict(value)
        data["classes"] = [
            {
                "id": item["name"] if isinstance(item["id"], int) else item["id"],
                "ml_id": item["id"] if isinstance(item["id"], int) else index,
                "label": item.get("label_ru", item.get("label")),
                "color": item["color"],
                "views": item["views"],
            }
            for index, item in enumerate(data.get("classes", []))
        ]
        return data

    @model_validator(mode="after")
    def validate_classes(self) -> "ClassesConfig":
        names = [item.id for item in self.classes]
        if len(names) != len(set(names)) or not set(STAGE_CLASSES) <= set(names):
            raise ValueError("classes.yaml должен содержать классы готовности без повторов")
        if [item.ml_id for item in self.classes] != list(range(len(self.classes))):
            raise ValueError("Числовые id классов должны идти подряд с 0")
        return self

    def allowed(self, cls: str) -> set[str]:
        return next((set(item.views) for item in self.classes if item.id == cls), set())


class Weights(Contract):
    site_prep: int
    foundation: int
    walls: int
    roof: int
    windows: int
    door: int
    facade: int
    done: int

    @model_validator(mode="after")
    def validate_total(self) -> "Weights":
        if sum(self.model_dump().values()) - self.site_prep != 100:
            raise ValueError("Вклады этапов без site_prep должны давать 100")
        return self


class QualityConfig(Contract):
    working_size: int = Field(ge=256)
    blur_min_laplacian_var: float = Field(ge=0)
    max_clipped_share: Score


class StagesConfig(Contract):
    version: str
    present_threshold: Score
    evidence_min_conf: Score = 0.0
    low_confidence: Score
    min_facades: int = Field(ge=1, le=4)
    done_windows_ratio: Score
    parcel_buffer_m: float = Field(ge=0)
    max_nadir_spread_m: float | None = Field(default=None, gt=0)
    quality: QualityConfig
    weights: Weights
    stages: list[Label]
    bank_readiness: list[Label]
    reshoot_reasons: dict[str, str]

    @model_validator(mode="after")
    def validate_stages(self) -> "StagesConfig":
        if [item.id for item in self.stages] != list(Stage):
            raise ValueError("stages.yaml должен перечислять все стадии по порядку")
        return self

    def stage_label(self, stage: Stage) -> str:
        return next(item.label for item in self.stages if item.id == stage)


def _load(name: str) -> dict:
    with (CONFIG_DIR / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


@lru_cache
def classes_config() -> ClassesConfig:
    return ClassesConfig.model_validate(_load("classes.yaml"))


@lru_cache
def stages_config() -> StagesConfig:
    return StagesConfig.model_validate(_load("stages.yaml"))
