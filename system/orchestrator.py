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


def _patch_signal_timeout():
    """Make P4LTL_LLM's signal-based timeout safe for non-main threads (e.g. uvicorn workers).

    The original ``_TimeoutHandler`` uses ``signal.SIGALRM`` which only works
    in the main thread.  This patch makes ``__enter__`` / ``__exit__`` no-ops
    when called from a worker thread, so the pipeline runs without timeout
    enforcement instead of crashing.
    """
    import threading
    import signal as _signal

    from P4LTL_LLM.pipeline import pipeline_protocol as _pp

    _orig_enter = _pp._TimeoutHandler.__enter__
    _orig_exit = _pp._TimeoutHandler.__exit__

    def _safe_enter(self):
        if threading.current_thread() is not threading.main_thread():
            return
        return _orig_enter(self)

    def _safe_exit(self, exc_type, exc, tb):
        if threading.current_thread() is not threading.main_thread():
            return
        return _orig_exit(self, exc_type, exc, tb)

    _pp._TimeoutHandler.__enter__ = _safe_enter
    _pp._TimeoutHandler.__exit__ = _safe_exit


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
        import os
        from .intent_adapter import build_sagefuzz_config
        from sagefuzz_seedgen.workflow.packet_sequence_workflow import (
            run_packet_sequence_generation,
        )

        cfg = build_sagefuzz_config(task, case)

        # SageFuzz expects CWD to be its project root for relative path resolution
        prev_cwd = os.getcwd()
        os.chdir(str(_SAGEFUZZ_ROOT))
        try:
            index_path = run_packet_sequence_generation(cfg)
        finally:
            os.chdir(prev_cwd)

        # index_path may be relative to SageFuzz root
        if not index_path.is_absolute():
            index_path = _SAGEFUZZ_ROOT / index_path

        task.testcase_run_id = index_path.stem.replace("_packet_sequence_index", "")
        task.testcases = self._load_testcases(index_path)

        return task

    # ------------------------------------------------------------------
    # Stage 3+4: Auto test execution + result judgement  (LLM Agent)
    # ------------------------------------------------------------------

    def run_auto_test(
        self,
        task: SessionTask,
        *,
        on_step: Optional[Any] = None,
    ) -> SessionTask:
        """Execute test cases on the remote BMv2/Mininet environment and judge results.

        Parameters
        ----------
        task : SessionTask
            Must have ``testcases`` populated from Stage 2.
        on_step : callable, optional
            ``fn(StepLog)`` callback for real-time progress reporting.
        """
        from .agent.test_agent import TestAgent
        from .agent.judge import ResultJudge
        from .models import AgentStepLog, TestObservations, TestVerdict, PacketVerdict

        if not task.testcases:
            task.test_execution_status = "failed"
            return task

        task.test_execution_status = "running"
        agent = TestAgent()

        # Execute each test case scenario
        all_observations: dict[str, Any] = {}
        all_step_logs: list[AgentStepLog] = []

        for i, tc in enumerate(task.testcases):
            scenario = tc.get("meta", {}).get("scenario", f"scenario_{i}")
            sub_task_id = f"{task.task_id}_{scenario}"

            def _on_step_wrapper(step):
                log = AgentStepLog(
                    step_id=step.step_id,
                    phase=step.phase,
                    thought=step.thought,
                    action=step.action,
                    action_input=step.action_input,
                    observation=step.observation,
                )
                all_step_logs.append(log)
                if on_step:
                    on_step(step)

            result = agent.execute_testcase(
                task_id=sub_task_id,
                testcase=tc,
                program_name=task.p4_program_name,
                on_step=_on_step_wrapper,
            )
            all_observations[scenario] = result.observations

        task.test_execution_log = all_step_logs
        task.test_observations = TestObservations(
            raw_outputs=[json.dumps(all_observations, ensure_ascii=False)]
        )

        # Judge results
        judge = ResultJudge()
        for i, tc in enumerate(task.testcases):
            oracle = tc.get("oracle_prediction", {})
            scenario = tc.get("meta", {}).get("scenario", f"scenario_{i}")
            obs = all_observations.get(scenario, {})
            if oracle and obs:
                verdict_dict = judge.judge(
                    intent=task.natural_language_intent,
                    ltl_spec=task.ltl_spec_text,
                    oracle_prediction=oracle,
                    observations=obs,
                )
                task.test_verdict = TestVerdict(
                    overall=verdict_dict.get("overall", "INCONCLUSIVE"),
                    per_packet=[
                        PacketVerdict(**pv) for pv in verdict_dict.get("per_packet", [])
                        if isinstance(pv, dict)
                    ],
                    reasoning=verdict_dict.get("reasoning", ""),
                    evidence=verdict_dict.get("evidence", []),
                )

        task.test_execution_status = "completed"
        return task

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            _patch_signal_timeout()

            from P4LTL_LLM.pipeline.pipeline_protocol import IntentToP4LTLPipeline

            self._pipeline = IntentToP4LTLPipeline(
                use_agents=True,
                allow_heuristic_fallback=False,
                agent_timeout_seconds=45,
                agent_max_retries=2,
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
