"""Unified data models for the intent-driven P4 testing system.

SessionTask binds a single natural-language intent to its generated P4LTL
specification, test cases, and (future) automated test results via a unique
task_id, ensuring correct correspondence across all stages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task_id() -> str:
    return uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Stage 3 / 4 models (reserved for LLM Test Agent, not yet implemented)
# ---------------------------------------------------------------------------

class AgentStepLog(BaseModel):
    step_id: int
    phase: str
    thought: str
    action: str
    action_input: dict = Field(default_factory=dict)
    observation: str = ""
    timestamp: str = Field(default_factory=_utc_now)


class PacketVerdict(BaseModel):
    packet_id: int
    expected_outcome: str
    actual_outcome: str
    match: bool
    explanation: str = ""


class TestObservations(BaseModel):
    packets_sent: list[dict] = Field(default_factory=list)
    packets_received: dict = Field(default_factory=dict)
    packets_dropped: list[dict] = Field(default_factory=list)
    switch_logs: dict = Field(default_factory=dict)
    pcap_summaries: dict = Field(default_factory=dict)
    raw_outputs: list[str] = Field(default_factory=list)


class TestVerdict(BaseModel):
    overall: str = "PENDING"
    per_packet: list[PacketVerdict] = Field(default_factory=list)
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core session model
# ---------------------------------------------------------------------------

class SessionTask(BaseModel):
    """A single end-to-end task that tracks intent -> spec -> testcase -> test."""

    task_id: str = Field(default_factory=_new_task_id)
    created_at: str = Field(default_factory=_utc_now)

    # --- Input ---
    natural_language_intent: str
    p4_program_name: str = ""
    p4_program_paths: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    topology_path: str = ""

    # --- Stage 1: Spec generation ---
    ltl_spec_text: Optional[str] = None
    spec_generation_ok: Optional[bool] = None
    spec_generation_detail: Optional[dict[str, Any]] = None

    # --- Stage 2: Test case generation ---
    testcases: Optional[list[dict[str, Any]]] = None
    testcase_run_id: Optional[str] = None

    # --- Stage 3: Test execution (future) ---
    test_execution_status: Optional[str] = None
    test_execution_log: list[AgentStepLog] = Field(default_factory=list)
    test_observations: Optional[TestObservations] = None

    # --- Stage 4: Result judgement (future) ---
    test_verdict: Optional[TestVerdict] = None
