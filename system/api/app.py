"""FastAPI application – Web backend for the intent-driven P4 testing system.

Endpoints:
    GET  /api/programs             – list available P4 programs / test cases
    POST /api/p4/upload            – upload & compile a P4 program on remote server
    POST /api/spec/generate        – generate P4LTL spec from intent
    POST /api/testcase/generate    – generate test cases from spec context
    POST /api/test/run             – run automated test (async with SSE)
    GET  /api/test/stream/{tid}    – SSE stream of test execution progress
    GET  /api/task/{tid}           – get full task state
    GET  /api/history              – list past tasks
    GET  /                         – serve frontend
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

for _p in ["/home/gosh/P4LTL", "/home/gosh/SageFuzz", str(Path(__file__).resolve().parents[2])]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from system.models import SessionTask
from system.orchestrator import SessionOrchestrator
from system.programs.program_registry import (
    get_all_cases,
    get_case_by_id,
    ProgramCase,
    P4LTL_GUIDE_PATH,
)
from system.agent.tools import RemoteTools

# ---------------------------------------------------------------------------

app = FastAPI(title="P4 Test System", version="1.1")
orch = SessionOrchestrator()
remote = RemoteTools()

_tasks: dict[str, SessionTask] = {}
_task_cases: dict[str, ProgramCase] = {}
_test_events: dict[str, asyncio.Queue] = {}
_progress_queues: dict[str, asyncio.Queue] = {}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
HISTORY_DIR = Path(__file__).resolve().parents[2] / "data" / "history"
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"
REMOTE_UPLOAD_BASE = "/home/gsj/P4/user_programs"


# ---------------------------------------------------------------------------
# Startup: load persisted history
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _load_history():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(HISTORY_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            task = SessionTask(**data["task"])
            _tasks[task.task_id] = task
            if "case" in data and data["case"]:
                _task_cases[task.task_id] = ProgramCase(**data["case"])
        except Exception:
            pass


def _persist_task(task: SessionTask, case: Optional[ProgramCase] = None):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"task": task.model_dump()}
    if case:
        payload["case"] = {
            "case_id": case.case_id,
            "suite": case.suite,
            "program_name": case.program_name,
            "intent": case.intent,
            "admin_description": case.admin_description,
            "root_dir": case.root_dir,
            "p4_program_paths": list(case.p4_program_paths),
            "artifact_paths": list(case.artifact_paths),
            "topology_path": case.topology_path,
            "extra_constraints": list(case.extra_constraints),
            "guide_path": case.guide_path,
        }
    path = HISTORY_DIR / f"{task.task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class SpecGenerateRequest(BaseModel):
    case_id: str
    intent_override: Optional[str] = None
    max_rounds: int = 3
    agent_timeout: int = 45


class TestcaseGenerateRequest(BaseModel):
    task_id: str


class AutoTestRequest(BaseModel):
    task_id: str


# ---------------------------------------------------------------------------
# P4 upload & compile
# ---------------------------------------------------------------------------

@app.post("/api/p4/upload")
async def upload_and_compile(
    p4_file: UploadFile = File(...),
    topology_file: Optional[UploadFile] = File(None),
    intent: str = Form(""),
    program_name: str = Form(""),
):
    """Upload a P4 source file, compile it on the remote server, and register it as a case."""
    if not p4_file.filename or not p4_file.filename.endswith(".p4"):
        raise HTTPException(400, "Please upload a .p4 file")

    prog_name = program_name or p4_file.filename.replace(".p4", "")
    ts = str(int(time.time()))
    local_dir = UPLOAD_DIR / f"{prog_name}_{ts}"
    local_dir.mkdir(parents=True, exist_ok=True)

    local_p4 = local_dir / p4_file.filename
    content = await p4_file.read()
    local_p4.write_bytes(content)

    local_topo = None
    if topology_file and topology_file.filename:
        local_topo = local_dir / topology_file.filename
        topo_content = await topology_file.read()
        local_topo.write_bytes(topo_content)

    remote_dir = f"{REMOTE_UPLOAD_BASE}/{prog_name}_{ts}"
    remote.ensure_dir(remote_dir)
    remote.ensure_dir(f"{remote_dir}/build")

    r = remote.ssh_write_file(f"{remote_dir}/{p4_file.filename}", content.decode("utf-8", errors="replace"))
    if not r.ok:
        raise HTTPException(500, f"Failed to upload P4 file: {r.stderr}")

    if local_topo:
        topo_text = topo_content.decode("utf-8", errors="replace")
        remote.ssh_write_file(f"{remote_dir}/pod-topo/topology.json", topo_text)
        remote.ensure_dir(f"{remote_dir}/pod-topo")
        remote.ssh_write_file(f"{remote_dir}/pod-topo/topology.json", topo_text)

    stem = p4_file.filename.replace(".p4", "")
    compile_cmd = (
        f"cd {remote_dir} && "
        f"p4c-bm2-ss --p4v 16 "
        f"--p4runtime-format text "
        f"--p4runtime-file build/{stem}.p4.p4info.txtpb "
        f"-o build/{stem}.json "
        f"{p4_file.filename} 2>&1"
    )
    r = remote.ssh_exec(compile_cmd, timeout=60, cwd=remote_dir)

    compile_ok = r.ok
    compile_output = r.stdout + r.stderr

    if compile_ok:
        remote.ssh_exec(
            f"mkdir -p {remote_dir}/build/graphs && "
            f"p4c-graphs --graphs-dir {remote_dir}/build/graphs {remote_dir}/{p4_file.filename} 2>/dev/null || true",
            cwd=remote_dir,
        )

    case_id = f"upload:{prog_name}:{ts}"
    case = ProgramCase(
        case_id=case_id,
        suite="upload",
        program_name=prog_name,
        intent=intent or f"用户上传的 P4 程序: {prog_name}",
        admin_description=f"User-uploaded program {p4_file.filename}",
        root_dir=remote_dir,
        p4_program_paths=[f"{remote_dir}/{p4_file.filename}"],
        artifact_paths=[
            f"{remote_dir}/build/{stem}.json",
            f"{remote_dir}/build/{stem}.p4.p4info.txtpb",
        ],
        topology_path=f"{remote_dir}/pod-topo/topology.json" if local_topo else "",
        guide_path=P4LTL_GUIDE_PATH,
    )

    return {
        "case_id": case_id,
        "program_name": prog_name,
        "compile_ok": compile_ok,
        "compile_output": compile_output,
        "remote_dir": remote_dir,
        "case": {
            "case_id": case.case_id,
            "program_name": case.program_name,
            "intent": case.intent,
            "suite": case.suite,
        },
    }


@app.post("/api/spec/generate")
def generate_spec(req: SpecGenerateRequest):
    """Start spec generation. Returns task_id immediately.
    Subscribe to /api/progress/{task_id} for real-time progress via SSE."""
    case = get_case_by_id(req.case_id)

    if not case and req.case_id.startswith("upload:"):
        for tid, c in _task_cases.items():
            if c.case_id == req.case_id:
                case = c
                break

    if not case:
        raise HTTPException(404, f"Case not found: {req.case_id}")

    task = orch.create_task(case, intent_override=req.intent_override)
    _tasks[task.task_id] = task
    _task_cases[task.task_id] = case

    queue: asyncio.Queue = asyncio.Queue()
    _progress_queues[task.task_id] = queue

    import threading
    from .progress_capture import ProgressCapture, parse_progress_line

    def _run():
        def _on_line(line):
            msg = parse_progress_line(line)
            if msg:
                try:
                    queue.put_nowait({"event": "progress", "data": {"message": msg}})
                except Exception:
                    pass

        with ProgressCapture(_on_line):
            try:
                nonlocal task
                task = orch.generate_spec(
                    task, case,
                    max_rounds=req.max_rounds,
                    agent_timeout=req.agent_timeout,
                )
            except Exception as exc:
                task.spec_generation_ok = False
                task.spec_generation_detail = {"error": str(exc), "traceback": traceback.format_exc()}

        _tasks[task.task_id] = task
        _persist_task(task, case)
        queue.put_nowait({"event": "complete", "data": _task_to_dict(task)})

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task.task_id, "status": "started"}


# ---------------------------------------------------------------------------
# Programs list (include uploaded programs)
# ---------------------------------------------------------------------------

@app.get("/api/programs")
def list_programs():
    cases = get_all_cases()
    result = [
        {
            "case_id": c.case_id,
            "program_name": c.program_name,
            "intent": c.intent,
            "suite": c.suite,
        }
        for c in cases
    ]
    seen = {c["case_id"] for c in result}
    for c in _task_cases.values():
        if c.case_id not in seen and c.suite == "upload":
            result.append({
                "case_id": c.case_id,
                "program_name": c.program_name,
                "intent": c.intent,
                "suite": c.suite,
            })
            seen.add(c.case_id)
    return result


# ---------------------------------------------------------------------------
# Testcase generation
# ---------------------------------------------------------------------------

@app.post("/api/testcase/generate")
def generate_testcases(req: TestcaseGenerateRequest):
    """Start testcase generation. Returns immediately.
    Subscribe to /api/progress/{task_id} for real-time progress via SSE."""
    task = _tasks.get(req.task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {req.task_id}")
    case = _task_cases.get(req.task_id)
    if not case:
        raise HTTPException(400, "No case associated with this task")

    queue: asyncio.Queue = asyncio.Queue()
    _progress_queues[task.task_id] = queue

    import threading
    from .progress_capture import ProgressCapture, parse_progress_line

    def _run():
        def _on_line(line):
            msg = parse_progress_line(line)
            if msg:
                try:
                    queue.put_nowait({"event": "progress", "data": {"message": msg}})
                except Exception:
                    pass

        with ProgressCapture(_on_line):
            try:
                nonlocal task
                task = orch.generate_testcases(task, case)
            except Exception as exc:
                task.testcases = []

        _tasks[task.task_id] = task
        _persist_task(task, case)
        queue.put_nowait({"event": "complete", "data": _task_to_dict(task)})

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task.task_id, "status": "started"}


# ---------------------------------------------------------------------------
# Auto test
# ---------------------------------------------------------------------------

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

            updated = orch.run_auto_test(task, on_step=on_step)
            _tasks[req.task_id] = updated
            case = _task_cases.get(req.task_id)
            _persist_task(updated, case)
            queue.put_nowait({"event": "complete", "data": _task_to_dict(updated)})
        except Exception as exc:
            queue.put_nowait({"event": "error", "data": {"error": str(exc)}})

    asyncio.get_event_loop().run_in_executor(None, lambda: asyncio.run(_run()))
    return {"status": "started", "task_id": req.task_id}


@app.get("/api/progress/{task_id}")
async def stream_progress(task_id: str):
    """SSE stream for spec/testcase generation progress (real backend output)."""
    queue = _progress_queues.get(task_id)
    if not queue:
        raise HTTPException(404, "No active generation for this task")

    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=600)
                yield {
                    "event": msg["event"],
                    "data": json.dumps(msg["data"], ensure_ascii=False),
                }
                if msg["event"] in ("complete", "error"):
                    break
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "keepalive"}

    return EventSourceResponse(event_generator())


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


# ---------------------------------------------------------------------------
# Task & History
# ---------------------------------------------------------------------------

@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    return _task_to_dict(task)


@app.get("/api/history")
def list_history():
    """Return a summary of all past tasks, newest first."""
    items = []
    for task in sorted(_tasks.values(), key=lambda t: t.created_at, reverse=True):
        items.append({
            "task_id": task.task_id,
            "created_at": task.created_at,
            "program_name": task.p4_program_name,
            "intent_short": task.natural_language_intent[:80],
            "has_spec": task.ltl_spec_text is not None,
            "spec_ok": task.spec_generation_ok,
            "testcase_count": len(task.testcases) if task.testcases else 0,
            "test_verdict": task.test_verdict.overall if task.test_verdict else None,
        })
    return items


# ---------------------------------------------------------------------------
# Frontend
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
    if d.get("spec_generation_detail"):
        detail = d["spec_generation_detail"]
        d["spec_generation_detail"] = {
            "ok": detail.get("ok"),
            "final_spec_text": detail.get("final_spec_text"),
            "error": detail.get("error"),
            "error_type": detail.get("error_type"),
        }
    return d
