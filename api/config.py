import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from integrations.llm.config import LLMSettings


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    max_file_bytes: int = 30 * 1024 * 1024
    max_files: int = 20
    max_pixels: int = 60_000_000
    photo_utc_offset_hours: int = 7
    model_factory: str | None = None
    detector_factory: str | None = None
    mock: bool = True
    job_lease_seconds: int = 900
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")
    llm: LLMSettings = field(default_factory=LLMSettings)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path.cwd() / ".env", override=False)
        return cls(
            storage_dir=Path(os.getenv("STORAGE_DIR", ".local/material-backend")).resolve(),
            photo_utc_offset_hours=int(os.getenv("PHOTO_UTC_OFFSET_HOURS", "7")),
            model_factory=os.getenv("MATERIAL_MODEL_FACTORY") or None,
            detector_factory=os.getenv("DETECTOR_FACTORY") or None,
            mock=os.getenv("MOCK", "1") == "1",
            llm=LLMSettings.from_env(),
            cors_origins=tuple(
                value.strip()
                for value in os.getenv(
                    "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
                ).split(",")
                if value.strip()
            ),
        )

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "service.sqlite3"
