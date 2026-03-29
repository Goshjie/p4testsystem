"""LLM Test Agent – ReAct loop that executes P4 test cases on a remote
BMv2/Mininet environment.

The agent reads a TestcaseOutput JSON, plans an execution strategy, then
iteratively calls tools (ssh_exec, ssh_write_file, etc.) until it has
collected enough observations to report results.

Implementation uses the OpenAI chat-completions API with function calling,
compatible with DashScope / Volc Ark / any OpenAI-like endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .tools import RemoteTools, ToolResult, TOOL_DESCRIPTORS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MAX_AGENT_STEPS = 25
DEFAULT_MODEL_ID = "glm-5"
DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_API_KEY = ""  # loaded from config


def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    return path.read_text(encoding="utf-8")


def _load_api_config() -> dict[str, str]:
    """Load API config from P4LTL_LLM's config file."""
    cfg_path = Path("/home/gosh/P4LTL/P4LTL_LLM/config/api_config.json")
    if cfg_path.exists():
        with cfg_path.open() as f:
            cfg = json.load(f)
        return {
            "model_id": cfg.get("model_id", DEFAULT_MODEL_ID),
            "api_key": cfg.get("api_key", DEFAULT_API_KEY),
            "base_url": cfg.get("base_url", DEFAULT_BASE_URL),
        }
    return {"model_id": DEFAULT_MODEL_ID, "api_key": DEFAULT_API_KEY, "base_url": DEFAULT_BASE_URL}


# ---------------------------------------------------------------------------
# Agent step log
# ---------------------------------------------------------------------------

@dataclass
class StepLog:
    step_id: int
    phase: str
    thought: str = ""
    action: str = ""
    action_input: dict = field(default_factory=dict)
    observation: str = ""
    status: str = "running"
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "phase": self.phase,
            "thought": self.thought,
            "action": self.action,
            "action_input": self.action_input,
            "observation": self.observation[:500],
            "status": self.status,
        }


@dataclass
class AgentResult:
    success: bool
    observations: dict = field(default_factory=dict)
    step_logs: list[StepLog] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# OpenAI-compatible function-calling helpers
# ---------------------------------------------------------------------------

def _build_openai_tools() -> list[dict]:
    """Convert our tool descriptors to OpenAI function-calling format."""
    tools = []
    for td in TOOL_DESCRIPTORS:
        props = {}
        for pname, pinfo in td.get("parameters", {}).items():
            props[pname] = {"type": pinfo["type"], "description": pinfo.get("description", "")}
        tools.append({
            "type": "function",
            "function": {
                "name": td["name"],
                "description": td["description"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": td.get("required", []),
                },
            },
        })
    return tools


def _call_llm(
    messages: list[dict],
    tools: list[dict],
    model_id: str,
    api_key: str,
    base_url: str,
) -> dict:
    """Call the OpenAI-compatible chat completions API."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model_id,
        messages=messages,
        tools=tools if tools else None,
        temperature=0.2,
        max_tokens=4096,
    )
    return response.choices[0].message


# ---------------------------------------------------------------------------
# Test Agent
# ---------------------------------------------------------------------------

class TestAgent:
    """ReAct agent that executes a P4 test case on the remote environment."""

    def __init__(
        self,
        remote_host: str = "root@172.22.231.61",
        work_dir: str = "/home/gsj/P4",
    ):
        self.tools = RemoteTools(remote_host=remote_host, work_dir=work_dir)
        self.work_dir = work_dir
        cfg = _load_api_config()
        self.model_id = cfg["model_id"]
        self.api_key = cfg["api_key"]
        self.base_url = cfg["base_url"]
        self.openai_tools = _build_openai_tools()

    def execute_testcase(
        self,
        task_id: str,
        testcase: dict,
        program_name: str,
        *,
        on_step: Optional[Callable[[StepLog], None]] = None,
    ) -> AgentResult:
        """Execute a single test case and return observations.

        Parameters
        ----------
        task_id : str
            Unique identifier for this test run (used as working dir name).
        testcase : dict
            A TestcaseOutput JSON dictionary.
        program_name : str
            Name of the P4 program (e.g. "firewall").
        on_step : callable, optional
            Callback invoked after each agent step, for progress reporting.
        """
        system_prompt = _load_prompt("test_agent_system.md")
        user_msg = self._build_user_message(task_id, testcase, program_name)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        step_logs: list[StepLog] = []
        step_id = 0

        for _ in range(MAX_AGENT_STEPS):
            step_id += 1
            step = StepLog(step_id=step_id, phase="execute", timestamp=time.strftime("%H:%M:%S"))

            try:
                response = _call_llm(
                    messages, self.openai_tools, self.model_id, self.api_key, self.base_url
                )
            except Exception as exc:
                step.thought = f"LLM call failed: {exc}"
                step.status = "error"
                step_logs.append(step)
                if on_step:
                    on_step(step)
                return AgentResult(success=False, step_logs=step_logs, error=str(exc))

            # If the model wants to call a tool
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(self._msg_from_response(response))

                for tool_call in response.tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        fn_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    step.action = fn_name
                    step.action_input = fn_args
                    step.thought = response.content or ""

                    tool_result = self._dispatch_tool(fn_name, fn_args)
                    observation = tool_result.stdout if tool_result.ok else f"ERROR: {tool_result.stderr}"
                    step.observation = observation[:1000]
                    step.status = "done"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": observation[:3000],
                    })

                step_logs.append(step)
                if on_step:
                    on_step(step)

            # If the model returns a final text response (no tool calls)
            else:
                content = response.content or ""
                step.thought = content
                step.phase = "complete"
                step.status = "done"
                step_logs.append(step)
                if on_step:
                    on_step(step)

                observations = self._parse_final_response(content)
                return AgentResult(success=True, observations=observations, step_logs=step_logs)

        return AgentResult(
            success=False,
            step_logs=step_logs,
            error=f"Agent exceeded max steps ({MAX_AGENT_STEPS})",
        )

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _dispatch_tool(self, name: str, args: dict) -> ToolResult:
        if name == "ssh_exec":
            return self.tools.ssh_exec(args.get("command", "echo no-command"), cwd=args.get("cwd"))
        elif name == "ssh_write_file":
            return self.tools.ssh_write_file(args["remote_path"], args["content"])
        elif name == "ssh_read_file":
            return self.tools.ssh_read_file(args["remote_path"])
        elif name == "parse_pcap":
            return self.tools.parse_pcap(args["remote_pcap_path"])
        elif name == "cleanup_mininet":
            return self.tools.cleanup_mininet()
        else:
            return ToolResult(ok=False, stderr=f"Unknown tool: {name}")

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def _build_user_message(self, task_id: str, testcase: dict, program_name: str) -> str:
        tc_json = json.dumps(testcase, indent=2, ensure_ascii=False)
        if len(tc_json) > 8000:
            tc_json = tc_json[:8000] + "\n... (truncated)"

        return f"""Execute the following P4 test case on the remote environment.

**Task ID**: {task_id}
**P4 Program**: {program_name}
**Working directory**: {self.work_dir}/auto_test/{task_id}/

**Test Case JSON**:
```json
{tc_json}
```

Follow the phases in your instructions: Prepare → Execute → Observe.
After completing all phases, provide your final observation summary."""

    @staticmethod
    def _msg_from_response(response) -> dict:
        """Convert an OpenAI response message to a dict for the messages list."""
        msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            msg["content"] = response.content
        if hasattr(response, "tool_calls") and response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    @staticmethod
    def _parse_final_response(content: str) -> dict:
        """Try to extract JSON from the agent's final response."""
        import re
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw_response": content}
