"""Pydantic schemas of the economic data management API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_CSV_CHARS = 2_000_000


class DatasetImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=255)
    source_url: str = Field(default="", max_length=1024)
    reference_year: int = Field(ge=1900, le=2100)
    publication_date: date | None = None
    methodology: str = Field(default="", max_length=20_000)
    csv_text: str = Field(min_length=1, max_length=MAX_CSV_CHARS)


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: int
    source: str
    source_url: str
    publication_date: date | None
    reference_year: int
    methodology: str
    checksum: str
    imported_at: datetime
    validation_status: str


class ModelVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    dataset_ids: list[int] = Field(min_length=1, max_length=50)
    notes: str = Field(default="", max_length=5_000)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: int


class ModelVersionOut(BaseModel):
    id: int
    label: str
    status: str
    notes: str
    created_at: datetime
    published_at: datetime | None
    datasets: list[DatasetOut]


class VersionDiffOut(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[str]
