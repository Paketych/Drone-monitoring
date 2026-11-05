from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from schemas.material import Contract, WallMaterial
from schemas.valuation import UnitCost

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/valuation.yaml"


class ValuationConfig(Contract):
    version: str
    currency: Literal["RUB"]
    rate_kind: Literal["demonstration", "reference"]
    rate_label: str = Field(min_length=1)
    completion_scope: str = Field(min_length=1)
    excludes: str
    range_percent: int = Field(ge=0, lt=100)
    round_to_rub: int = Field(ge=1, le=1000000)
    rates_rub_m2: dict[WallMaterial, UnitCost]

    @model_validator(mode="after")
    def all_materials_have_rates(self):
        if set(self.rates_rub_m2) != set(WallMaterial):
            raise ValueError("Нужны тарифы для всех конкретных материалов стен")
        return self


@lru_cache(maxsize=8)
def _load(path: str, modified: int, size: int) -> ValuationConfig:
    return ValuationConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def valuation_config() -> ValuationConfig:
    info = CONFIG_PATH.stat()
    return _load(str(CONFIG_PATH), info.st_mtime_ns, info.st_size)
