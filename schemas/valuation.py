from decimal import Decimal
from typing import Annotated

from pydantic import Field

from schemas.material import Contract, WallMaterial

Area = Annotated[Decimal, Field(gt=0, le=1000000, max_digits=12, decimal_places=3)]
UnitCost = Annotated[Decimal, Field(gt=0, le=10000000, max_digits=12, decimal_places=2)]
LandCost = Annotated[Decimal, Field(ge=0, le=1000000000000, max_digits=16, decimal_places=2)]


class ValuationInputs(Contract):
    area_m2: Area | None = None
    wall_material: WallMaterial | None = None
    unit_cost_rub_m2: UnitCost | None = None
    rate_note: str = Field(default="", max_length=1000)
    land_rub: LandCost | None = None
