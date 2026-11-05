from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from schemas.material import BBox, Contract, Score


class DetectorClass(StrEnum):
    SITE_PREP = "site_prep"
    FOUNDATION = "foundation"
    WALLS_PARTIAL = "walls_partial"
    WALLS_FULL = "walls_full"
    ROOF_FRAME = "roof_frame"
    ROOF_COVERED = "roof_covered"
    OPENING_EMPTY = "opening_empty"
    WINDOW_INSTALLED = "window_installed"
    DOOR_INSTALLED = "door_installed"
    FACADE_FINISH = "facade_finish"
    BUILDING = "building"
    OUTBUILDING = "outbuilding"
    FENCE = "fence"
    PAVING = "paving"


class Stage(StrEnum):
    NONE = "none"
    FOUNDATION = "foundation"
    WALLS = "walls"
    ROOF = "roof"
    OPENINGS = "openings"
    FACADE = "facade"
    DONE = "done"


# Classes 0–9 describe construction progress; 10–13 belong to the land-use agent.
STAGE_CLASSES = tuple(DetectorClass)[:10]
BankReadiness = Literal[0, 50, 100]


class Detection(Contract):
    # The JSON field is «class»; dump with by_alias=True.
    model_config = ConfigDict(populate_by_name=True)

    image_id: str
    cls: DetectorClass = Field(alias="class")
    conf: Score
    bbox: BBox
    target_included: bool = True
    target_exclusion_reason: Literal["OUTSIDE_TARGET"] | None = None

    @model_validator(mode="after")
    def validate_bbox(self) -> "Detection":
        x1, y1, x2, y2 = self.bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Рамка должна иметь положительную ширину и высоту")
        return self


class Element(Contract):
    present: bool
    implied: bool = False
    conf: Score | None = None
    views: list[str] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)


class ReadinessResult(Contract):
    agent: Literal["readiness"] = "readiness"
    model: str
    rules: str
    is_demo: bool
    status: Literal["determined", "needs_review", "needs_reshoot", "model_unavailable"]
    stage: Stage | None = None
    stage_label: str | None = None
    readiness_pct: int | None = Field(default=None, ge=0, le=100)
    bank_readiness: BankReadiness | None = None
    confidence: Score = 0
    elements: dict[str, Element] = Field(default_factory=dict)
    windows_ratio: Score = 0
    facades_seen: int = 0
    explanation: str
    needs_reshoot: bool = False
    reshoot_reasons: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)


def default_bank_readiness(stage: Stage) -> BankReadiness:
    return 0 if stage == Stage.NONE else 100 if stage == Stage.DONE else 50


class ReadinessReview(Contract):
    action: Literal["accept", "correct"]
    stage: Stage | None = None
    readiness_pct: int | None = Field(default=None, ge=0, le=100)
    # Stages before «done» can match either 50 or 100 on the bank scale, so it may be given.
    bank_readiness: BankReadiness | None = None
    comment: str = Field(default="", max_length=4000)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value):
        return "" if value is None else value

    @model_validator(mode="after")
    def validate_correction(self) -> "ReadinessReview":
        manual = (self.stage, self.readiness_pct, self.bank_readiness)
        if self.action == "accept":
            if any(value is not None for value in manual):
                raise ValueError("При принятии стадия берётся из результата модели")
            return self
        if self.stage is None or self.readiness_pct is None or not self.comment.strip():
            raise ValueError("Для исправления укажите стадию, процент и комментарий")
        if self.bank_readiness is None:
            self.bank_readiness = default_bank_readiness(self.stage)
        if (self.stage == Stage.NONE) != (self.bank_readiness == 0):
            raise ValueError("Шкала банка 0 соответствует только стадии «Нет работ»")
        if self.stage == Stage.DONE and self.bank_readiness != 100:
            raise ValueError("Стадия «Готов» соответствует шкале банка 100")
        return self
