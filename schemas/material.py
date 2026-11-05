from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class WallMaterial(StrEnum):
    BRICK = "brick"
    AERATED_CONCRETE = "aerated_concrete"
    CERAMIC_BLOCK = "ceramic_block"
    TIMBER = "timber"
    FRAME = "frame"
    SIP = "sip"
    MONOLITH = "monolith"


class View(StrEnum):
    NORTH = "N"
    EAST = "E"
    SOUTH = "S"
    WEST = "W"
    TOP_LOW = "top_low"
    TOP_HIGH = "top_high"


MATERIALS = {
    "brick": ("Кирпич", "stone"),
    "aerated_concrete": ("Газобетон / пеноблок", "blocks"),
    "ceramic_block": ("Керамический блок", "blocks"),
    "timber": ("Брус / бревно", "wood"),
    "frame": ("Каркас", "wood"),
    "sip": ("СИП-панели", "wood"),
    "monolith": ("Монолит", "concrete"),
}
FACADES = {View.NORTH, View.EAST, View.SOUTH, View.WEST}
TOP_VIEWS = {View.TOP_LOW, View.TOP_HIGH}
Score = Annotated[float, Field(ge=0, le=1)]
BBox = tuple[Score, Score, Score, Score]


class Project(Contract):
    wall_material: WallMaterial | None = None
    floors: int | None = Field(default=None, ge=1, le=100)
    footprint_m2: float | None = Field(default=None, gt=0, le=1000000)
    total_area_m2: float | None = Field(default=None, gt=0, le=1000000)
    source: str | None = Field(default=None, max_length=1000)


Stage = Literal["none", "foundation", "walls", "roof", "openings", "facade", "done"]
MaterialMatch = Literal["match", "deviation", "mismatch", "unclear", "no_project"]


class Checkpoint(Contract):
    stage: Stage
    due: date


class Tranche(Contract):
    n: int = Field(ge=1)
    amount: float = Field(ge=0)
    paid_at: date | None = None
    condition_stage: Stage | None = None


class Loan(Contract):
    amount: float | None = Field(default=None, ge=0)
    dpd: int = Field(default=0, ge=0)
    collateral_value_rub: float | None = Field(default=None, ge=0)
    tranches: list[Tranche] = Field(default_factory=list)


Position = tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]]


class ParcelPolygon(Contract):
    type: Literal["Polygon"]
    coordinates: list[list[Position]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rings(self) -> "ParcelPolygon":
        for ring in self.coordinates:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Контур участка: минимум 3 точки, первая совпадает с последней")
        return self


class ObjectCreate(Contract):
    address: str = Field(min_length=1, max_length=500)
    cadastral: str | None = Field(default=None, max_length=100)
    parcel_geojson: ParcelPolygon | None = None
    vri: str | None = Field(default=None, max_length=100)
    project: Project | None = None
    notes: str = Field(default="", max_length=4000)
    loan_id: str | None = Field(default=None, max_length=100)
    started_at: date | None = None
    planned_completion_at: date | None = None
    actual_completion_at: date | None = None
    overdue: bool = False
    registered: bool = False
    pledged: bool = False
    has_aerial_photo: bool = False
    loan: Loan | None = None
    checkpoints: list[Checkpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> "ObjectCreate":
        if self.started_at is not None:
            if (
                self.planned_completion_at is not None
                and self.planned_completion_at < self.started_at
            ):
                raise ValueError("Плановая дата раньше даты начала строительства")
            if (
                self.actual_completion_at is not None
                and self.actual_completion_at < self.started_at
            ):
                raise ValueError("Фактическая дата раньше даты начала строительства")
        for checkpoint in self.checkpoints:
            if self.started_at is not None and checkpoint.due < self.started_at:
                raise ValueError("Контрольная точка раньше даты начала строительства")
            if (
                self.planned_completion_at is not None
                and checkpoint.due > self.planned_completion_at
            ):
                raise ValueError("Контрольная точка позже плановой даты завершения")
        return self


def compare_materials(material: str | None, project_material: str | None) -> MaterialMatch:
    if project_material is None:
        return "no_project"
    if material not in MATERIALS or project_material not in MATERIALS:
        return "unclear"
    if material == project_material:
        return "match"
    return "deviation" if MATERIALS[material][1] == MATERIALS[project_material][1] else "mismatch"


class ImageMeta(Contract):
    gps: (
        tuple[Annotated[float, Field(ge=-90, le=90)], Annotated[float, Field(ge=-180, le=180)]]
        | None
    ) = None
    target_bbox: BBox | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.target_bbox is not None:
            x1, y1, x2, y2 = self.target_bbox
            if x2 <= x1 or y2 <= y1:
                raise ValueError("Область нужного объекта должна иметь положительный размер")
        return self


class Observation(Contract):
    image_id: str
    visibility: Literal["visible", "hidden", "no_walls", "unclear"]
    material: WallMaterial | Literal["hidden", "unclear"] | None = None
    confidence: Score = 0
    crop_bbox: BBox | None = None
    cls: Literal["walls_partial", "walls_full"] | None = None
    bbox: BBox | None = None
    detector_confidence: Score | None = None
    material_conf: Score | None = None
    predicted_material: WallMaterial | Literal["hidden", "unclear"] | None = None
    review_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_observation(self) -> "Observation":
        if self.visibility == "visible":
            if self.material not in MATERIALS or self.crop_bbox is None:
                raise ValueError("Для видимой стены нужны материал и рамка")
        elif self.material not in {None, self.visibility} or (
            self.visibility == "no_walls" and self.material is not None
        ):
            raise ValueError("Скрытой или отсутствующей стене нельзя назначать материал")
        for box in (self.crop_bbox, self.bbox):
            if box:
                x1, y1, x2, y2 = box
                if x2 <= x1 or y2 <= y1:
                    raise ValueError("Рамка должна иметь положительную ширину и высоту")
        return self


class ViewObservation(Observation):
    view: View | None = None
    included: bool = False
    exclusion_reason: str | None = None
    crop_url: str | None = None


class MaterialResult(Contract):
    agent: Literal["material"] = "material"
    model: str
    is_demo: bool
    rules: str | None = None
    thresholds: dict[str, float | int] = Field(default_factory=dict)
    status: Literal[
        "determined",
        "hidden",
        "no_walls",
        "insufficient_data",
        "mixed",
        "needs_review",
        "model_unavailable",
    ]
    material: WallMaterial | Literal["hidden", "unclear"] = "unclear"
    material_label: str = "Не удалось определить"
    group: str | None = None
    confidence: Score = 0
    mixed: bool = False
    votes: dict[str, float] = Field(default_factory=dict)
    per_view: list[ViewObservation] = Field(default_factory=list)
    project_material: WallMaterial | None = None
    match: MaterialMatch
    fixed_on_object: bool = False
    explanation: str
    needs_reshoot: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class MaterialReview(Contract):
    action: Literal["accept", "correct"]
    material: WallMaterial | None = None
    comment: str = Field(default="", max_length=4000)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value):
        return "" if value is None else value

    @model_validator(mode="after")
    def validate_correction(self) -> "MaterialReview":
        if self.action == "correct" and (self.material is None or not self.comment.strip()):
            raise ValueError("Для исправления укажите материал и комментарий")
        if self.action == "accept" and self.material is not None:
            raise ValueError("При принятии материал берётся из результата модели")
        return self


def utc_now() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()
