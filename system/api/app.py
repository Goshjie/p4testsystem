"""FastAPI application – Web backend for the intent-driven P4 testing system.

Endpoints:
    GET  /api/programs             – list available P4 programs / test cases
    POST /api/spec/generate        – generate P4LTL spec from intent
    POST /api/testcase/generate    – generate test cases from spec context
    POST /api/test/run             – run automated test (async with SSE)
    GET  /api/test/stream/{tid}    – SSE stream of test execution progress
    GET  /api/task/{tid}           – get full task state
    GET  /                         – serve frontend
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Ensure subsystem projects importable
for _p in ["/home/gosh/P4LTL", "/home/gosh/SageFuzz", str(Path(__file__).resolve().parents[2])]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from system.models import SessionTask
from system.orchestrator import SessionOrchestrator
from system.programs.program_registry import get_all_cases, get_case_by_id, ProgramCase

# ---------------------------------------------------------------------------

app = FastAPI(title="P4 Test System", version="1.0")
orch = SessionOrchestrator()

# In-memory task store (sufficient for single-user demo)
_tasks: dict[str, SessionTask] = {}
_task_cases: dict[str, ProgramCase] = {}
_test_events: dict[str, asyncio.Queue] = {}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SpecGenerateRequest(BaseModel):
    case_id: str
    intent_override: Optional[str] = None


class TestcaseGenerateRequest(BaseModel):
    task_id: str


class AutoTestRequest(BaseModel):
    task_id: str


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/programs")
def list_programs():
    cases = get_all_cases()
    return [
        {
            "case_id": c.case_id,
            "program_name": c.program_name,
            "intent": c.intent,
            "suite": c.suite,
        }
        for c in cases
    ]


@app.post("/api/spec/generate")
def generate_spec(req: SpecGenerateRequest):
    case = get_case_by_id(req.case_id)
    if not case:
        raise HTTPException(404, f"Case not found: {req.case_id}")

    task = orch.create_task(case, intent_override=req.intent_override)
    _tasks[task.task_id] = task
    _task_cases[task.task_id] = case

    try:
        task = orch.generate_spec(task, case)
    except Exception as exc:
        task.spec_generation_ok = False
        task.spec_generation_detail = {"error": str(exc), "traceback": traceback.format_exc()}

    _tasks[task.task_id] = task
    return _task_to_dict(task)


@app.post("/api/testcase/generate")
def generate_testcases(req: TestcaseGenerateRequest):
    task = _tasks.get(req.task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {req.task_id}")
    case = _task_cases.get(req.task_id)
    if not case:
        raise HTTPException(400, "No case associated with this task")

    try:
        task = orch.generate_testcases(task, case)
    except Exception as exc:
        task.testcases = []
        task.spec_generation_detail = task.spec_generation_detail or {}

    _tasks[task.task_id] = task
    return _task_to_dict(task)


@app.post("/api/test/run")
async def run_auto_test(req: AutoTestRequest):
    task = _tasks.get(req.task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {req.task_id}")
    if not task.testcases:
        raise HTTPException(400, "No test cases to execute")

    queue: asyncio.Queue = asyncio.Queue()
    _test_events[req.task_id] = queue

    async def _run():
        try:
            def on_step(step):
                asyncio.get_event_loop().call_soon_threadsafe(
                    queue.put_nowait,
                    {"event": "step", "data": step.to_dict()},
                )

            case = _task_cases.get(req.task_id)
            updated = orch.run_auto_test(task, on_step=on_step)
            _tasks[req.task_id] = updated
            queue.put_nowait({"event": "complete", "data": _task_to_dict(updated)})
        except Exception as exc:
            queue.put_nowait({"event": "error", "data": {"error": str(exc)}})

    asyncio.get_event_loop().run_in_executor(None, lambda: asyncio.run(_run()))
    return {"status": "started", "task_id": req.task_id}


@app.get("/api/test/stream/{task_id}")
async def stream_test_progress(task_id: str):
    queue = _test_events.get(task_id)
    if not queue:
        raise HTTPException(404, "No active test for this task")

    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=300)
                yield {
                    "event": msg["event"],
                    "data": json.dumps(msg["data"], ensure_ascii=False),
                }
                if msg["event"] in ("complete", "error"):
                    break
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "keepalive"}

    return EventSourceResponse(event_generator())


@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    return _task_to_dict(task)


# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)
    return HTMLResponse(index.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_to_dict(task: SessionTask) -> dict[str, Any]:
    d = task.model_dump()
    # Trim large nested details for API response
    if d.get("spec_generation_detail"):
        detail = d["spec_generation_detail"]
        d["spec_generation_detail"] = {
            "ok": detail.get("ok"),
            "final_spec_text": detail.get("final_spec_text"),
            "error": detail.get("error"),
            "error_type": detail.get("error_type"),
        }
    return d
