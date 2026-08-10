"""Analytics block and result models.

A *block* says what to extract from a finished call; a *result* is what one block
extracted from one session. Blocks attach to agents, so the same block can run for
many agents and every result points back at the block that produced it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["str", "int", "float", "bool", "list[str]", "enum"]


class AnalyticsField(BaseModel):
    """One extraction field on a block.

    ``choices`` is required for (and only valid on) ``type="enum"``; the server
    rejects anything else with a 422 at create time.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: FieldType = "str"
    description: str = ""
    choices: list[str] | None = None


class AnalyticsBlock(BaseModel):
    """An analytics block (matches sv_analytics_blocks response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    block_id: str
    name: str
    prompt: str
    fields_spec: list[dict[str, Any]] = Field(default_factory=list, alias="fields")
    summary_description: str | None = None
    model: str | None = None
    project_id: str | None = None
    org_id: str | None = None
    success_enabled: bool = True
    success_description: str | None = None
    success_type: str = "bool"
    success_choices: list[str] | None = None
    condition: dict[str, Any] | None = None
    template_id: str | None = None
    version: int = 1
    state: str = "active"
    # Agents this block is attached to. Populated on GET of a single block.
    agent_ids: list[str] = Field(default_factory=list)
    created: datetime | None = None
    modified: datetime | None = None


class AnalyticsResult(BaseModel):
    """One block's extraction from one session.

    ``status`` is ``ok`` (extracted), ``failed`` (the run errored, see
    ``error``), or ``skipped`` (nothing to analyse — no transcript).
    ``summary`` is present on every block; ``data`` holds the block's own fields.
    """

    model_config = ConfigDict(extra="allow")

    result_id: str
    block_id: str
    name: str | None = None
    session_id: str
    call_id: str | None = None
    agent_id: str | None = None
    status: str = "ok"
    summary: str = ""
    # Promoted out of ``data``: the field worth aggregating across blocks.
    success_evaluation: bool | str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    block_version: int = 1
    created: datetime | None = None
