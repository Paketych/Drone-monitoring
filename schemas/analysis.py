from typing import Literal

from pydantic import ConfigDict, Field

from schemas.material import Contract


class AnalysisRequest(Contract):
    force: bool = False


class Recommendation(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    # Financial/legal strategies need verified communication outcomes and are not enabled here.
    strategy_code: Literal["S1", "S5"] | None
    next_step_code: (
        Literal["FLIGHT_TASK", "MANUAL_INSPECTION", "COMM_REASON", "SMS_TERM_REMINDER"] | None
    )
    reason: str = Field(min_length=1, max_length=2400)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    missing_data: list[str] = Field(max_length=20)
