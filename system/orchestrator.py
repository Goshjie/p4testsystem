"""Session orchestrator – the central coordinator that chains spec generation
and test-case generation while maintaining per-task identity.

Usage::

    orch = SessionOrchestrator()
    task = orch.create_task(case)
    task = orch.generate_spec(task)
    task = orch.generate_testcases(task)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from .models import SessionTask
from .programs.program_registry import ProgramCase, P4LTL_GUIDE_PATH

# Ensure subsystem projects are importable
_P4LTL_LLM_ROOT = Path("/home/gosh/P4LTL")
_SAGEFUZZ_ROOT = Path("/home/gosh/SageFuzz")
for _p in (_P4LTL_LLM_ROOT, _SAGEFUZZ_ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


class SessionOrchestrator:
    """Orchestrates the end-to-end intent -> spec -> testcase pipeline."""

    def __init__(self, *, guide_path: str = P4LTL_GUIDE_PATH):
        self.guide_path = guide_path
        self._pipeline: Any = None  # lazy

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def create_task(case: ProgramCase, intent_override: Optional[str] = None) -> SessionTask:
        return SessionTask(
            natural_language_intent=intent_override or case.intent,
            p4_program_name=case.program_name,
            p4_program_paths=list(case.p4_program_paths),
            artifact_paths=list(case.artifact_paths),
            topology_path=case.topology_path,
        )

    # ------------------------------------------------------------------
    # Stage 1: Spec generation  (P4LTL_LLM)
    # ------------------------------------------------------------------

    def generate_spec(self, task: SessionTask, case: ProgramCase) -> SessionTask:
        from .intent_adapter import build_p4ltl_request

        pipeline = self._get_pipeline()
        request = build_p4ltl_request(task, case)

        try:
            result = pipeline.generate_and_validate(request)
            task.ltl_spec_text = result.final_spec_text
            task.spec_generation_ok = result.ok
            task.spec_generation_detail = result.model_dump()
        except Exception as exc:
            task.spec_generation_ok = False
            task.spec_generation_detail = {
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        return task

    # ------------------------------------------------------------------
    # Stage 2: Test-case generation  (SageFuzz)
    # ------------------------------------------------------------------

    def generate_testcases(self, task: SessionTask, case: ProgramCase) -> SessionTask:
        from .intent_adapter import build_sagefuzz_config
        from sagefuzz_seedgen.workflow.packet_sequence_workflow import (
            run_packet_sequence_generation,
        )

        cfg = build_sagefuzz_config(task, case)
        index_path = run_packet_sequence_generation(cfg)

        task.testcase_run_id = index_path.stem.replace("_packet_sequence_index", "")
        task.testcases = self._load_testcases(index_path)

        return task

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            from P4LTL_LLM.pipeline.pipeline_protocol import IntentToP4LTLPipeline

            self._pipeline = IntentToP4LTLPipeline(
                use_agents=True,
                allow_heuristic_fallback=False,
                agent_timeout_seconds=30,
                agent_max_retries=1,
                agent_retry_delay_seconds=2,
                enable_learning=False,
                enable_template_family_enhancement=True,
            )
        return self._pipeline

    @staticmethod
    def _load_testcases(index_path: Path) -> list[dict[str, Any]]:
        """Read all testcase JSON files referenced by the index."""
        testcases_dir = index_path.parent / index_path.stem.replace(
            "_packet_sequence_index", "_testcases"
        )
        if not testcases_dir.is_dir():
            run_id_stem = index_path.stem.replace("_packet_sequence_index", "")
            testcases_dir = index_path.parent / f"{run_id_stem}_testcases"

        results: list[dict[str, Any]] = []
        if testcases_dir.is_dir():
            for tc_file in sorted(testcases_dir.glob("*.json")):
                with tc_file.open("r", encoding="utf-8") as f:
                    results.append(json.load(f))
        return results
