"""Adapters that convert between the data formats used by P4LTL_LLM and SageFuzz.

P4LTL_LLM expects an ``IntentToP4LTLRequest`` with a flat *intent* string.
SageFuzz expects a ``RunConfig`` whose *user_intent* is a structured dict
conforming to ``UserIntent`` (feature_under_test, intent_text, …).

This module provides helpers to build both request types from a
``ProgramCase`` / ``SessionTask`` so the orchestrator does not need to know
the internal schemas of either subsystem.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .models import SessionTask
from .programs.program_registry import ProgramCase, P4LTL_GUIDE_PATH

# ---------------------------------------------------------------------------
# Ensure both projects are importable
# ---------------------------------------------------------------------------

_P4LTL_LLM_ROOT = Path("/home/gosh/P4LTL")
_SAGEFUZZ_ROOT = Path("/home/gosh/SageFuzz")

for _p in (_P4LTL_LLM_ROOT, _SAGEFUZZ_ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


# ---------------------------------------------------------------------------
# P4LTL_LLM request builder
# ---------------------------------------------------------------------------

def build_p4ltl_request(task: SessionTask, case: ProgramCase) -> Any:
    """Build an ``IntentToP4LTLRequest`` for the P4LTL_LLM pipeline."""
    from P4LTL_LLM.pipeline.models import IntentToP4LTLRequest

    return IntentToP4LTLRequest(
        intent=task.natural_language_intent,
        admin_description=case.admin_description,
        p4_program_paths=case.p4_program_paths,
        artifact_paths=case.artifact_paths,
        extra_constraints=case.extra_constraints,
        guide_path=case.guide_path or P4LTL_GUIDE_PATH,
        session_id=task.task_id,
        max_rounds=3,
    )


# ---------------------------------------------------------------------------
# SageFuzz RunConfig builder
# ---------------------------------------------------------------------------

def build_sagefuzz_config(task: SessionTask, case: ProgramCase) -> Any:
    """Build a SageFuzz ``RunConfig`` for test-case generation."""
    from sagefuzz_seedgen.config import (
        AgentModelOverrides,
        AgnoMemoryConfig,
        FallbackConfig,
        ModelConfig,
        ProgramPaths,
        RunConfig,
    )

    root = Path(case.root_dir)

    bmv2_json = _first_match(root, "build", "*.json")
    p4info = _first_match(root, "build", "*.p4.p4info.txtpb") or _first_match(root, "build", "*.txtpb")
    graphs_dir = root / "build" / "graphs"
    topo = Path(case.topology_path) if case.topology_path else root / "pod-topo" / "topology.json"
    p4_source = _first_match(root, "solution", "*.p4") or _first_match(root, "", "*.p4")

    program = ProgramPaths(
        bmv2_json=Path(bmv2_json) if bmv2_json else root / "build" / "program.json",
        graphs_dir=graphs_dir if graphs_dir.exists() else root / "build",
        p4info_txtpb=Path(p4info) if p4info else root / "build" / "program.p4.p4info.txtpb",
        topology_json=topo,
        p4_source=Path(p4_source) if p4_source else None,
    )

    user_intent: dict[str, Any] | None = None
    if case.sagefuzz_intent:
        user_intent = dict(case.sagefuzz_intent)
    else:
        user_intent = {
            "intent_text": task.natural_language_intent,
            "feature_under_test": case.program_name,
            "test_objective": "data_plane_behavior",
        }

    model = ModelConfig.from_env()

    return RunConfig(
        program=program,
        model=model,
        agent_models=AgentModelOverrides(),
        memory=AgnoMemoryConfig(enabled=False),
        fallbacks=FallbackConfig(enabled=True),
        user_intent=user_intent,
        max_retries=4,
        out_path=None,
        session_state_path=None,
    )


def _first_match(root: Path, subdir: str, pattern: str) -> str | None:
    search_dir = root / subdir if subdir else root
    if not search_dir.exists():
        return None
    matches = sorted(search_dir.glob(pattern))
    return str(matches[0]) if matches else None
