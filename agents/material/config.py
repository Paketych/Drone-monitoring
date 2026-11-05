from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, model_validator

from schemas.material import MATERIALS, Contract, Score, WallMaterial

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/materials.yaml"
PositiveScore = Annotated[float, Field(gt=0, le=1)]
MaterialName = WallMaterial | Literal["hidden", "unclear"]


class MaterialLabel(Contract):
    id: int = Field(ge=0)
    name: MaterialName
    label_ru: str = Field(min_length=1)
    group: str | None


class MaterialConfig(Contract):
    version: str = Field(min_length=1)
    min_crop_confidence: PositiveScore
    min_object_confidence: PositiveScore
    min_wall_confidence: Score
    min_confirmed_facades: int = Field(ge=1, le=4)
    mixed_material_share: float = Field(gt=0, le=0.5)
    min_cladding_confidence: Score
    min_cladding_overlap: PositiveScore
    crop_margin: float = Field(ge=0, le=0.5)
    min_crop_px: int = Field(ge=1, le=1024)
    wall_classes: list[Literal["walls_partial", "walls_full"]] = Field(min_length=1)
    materials: list[MaterialLabel]

    @model_validator(mode="after")
    def validate_labels(self):
        names = [str(item.name) for item in self.materials]
        if len(names) != len(set(names)) or set(names) != set(MATERIALS) | {"hidden", "unclear"}:
            raise ValueError("materials.yaml должен содержать все материалы сервиса без повторов")
        if [item.id for item in self.materials] != list(range(len(self.materials))):
            raise ValueError("id материалов должны идти подряд с 0")
        if len(self.wall_classes) != len(set(self.wall_classes)):
            raise ValueError("Классы стен не должны повторяться")
        return self

    def thresholds(self) -> dict:
        return self.model_dump(
            exclude={"version", "wall_classes", "materials", "crop_margin", "min_crop_px"}
        )


@lru_cache
def material_config() -> MaterialConfig:
    return MaterialConfig.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
