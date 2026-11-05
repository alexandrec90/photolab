"""Runtime settings, read from the environment and `.env`."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything that differs between a checkout, a container and a test."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str
    # The `*_HOST_DIR` names are the ones in `.env`, so a run on the host finds the same
    # folders the containers mount. Compose sets the first name, which wins.
    library_dir: Path = Field(
        default=Path("photos"),
        validation_alias=AliasChoices("PHOTO_LIBRARY_DIR", "PHOTO_LIBRARY_HOST_DIR"),
    )
    data_dir: Path = Field(
        default=Path("data"),
        validation_alias=AliasChoices("PHOTOLAB_DATA_DIR", "PHOTOLAB_DATA_HOST_DIR"),
    )

    # Which of `agents.DEFAULT_MODELS` annotates; empty model means that backend's default.
    annotation_backend: Literal["codex", "claude", "api"] = "codex"
    annotation_model: str = ""
    annotation_concurrency: int = 4
    anthropic_api_key: str = ""

    # Runs Codex's image tool. It does not draw, so it need not be a strong model.
    image_agent_model: str = "gpt-6-luna"

    pexels_api_key: str = ""

    # Measured on a 4-core CPU with one 1920x1280 photo: this takes 26s and peaks at 1 GB.
    # `birefnet-portrait` has visibly cleaner edges, at 115s and 5.1 GB, which is more
    # than a default Docker Desktop VM has: it is OOM-killed there rather than slow.
    matting_model: str = "isnet-general-use"
