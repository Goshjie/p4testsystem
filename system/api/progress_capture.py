"""Capture stdout/stderr progress messages from P4LTL_LLM and SageFuzz pipelines
and route them to an asyncio Queue for SSE delivery.

Both pipelines print progress like:
  - P4LTL_LLM: loguru WARNING lines, agent stage transitions
  - SageFuzz:   [进度] Agent1 正在执行语义分析...

This module intercepts these messages in real time, prefixes them with a
stage label so the frontend can clearly distinguish spec generation from
testcase generation.
"""

from __future__ import annotations

import re
import sys
from typing import Optional


class ProgressCapture:
    """Context manager that tee's stdout/stderr to a callback."""

    def __init__(self, on_line: callable):
        self._on_line = on_line
        self._original_stdout = None
        self._original_stderr = None

    def __enter__(self):
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = _TeeWriter(self._original_stdout, self._on_line)
        sys.stderr = _TeeWriter(self._original_stderr, self._on_line)
        return self

    def __exit__(self, *args):
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr


class _TeeWriter:
    def __init__(self, original, on_line):
        self._original = original
        self._on_line = on_line
        self._buffer = ""

    def write(self, text):
        self._original.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._on_line(line.strip())

    def flush(self):
        self._original.flush()
        if self._buffer.strip():
            self._on_line(self._buffer.strip())
            self._buffer = ""

    def __getattr__(self, name):
        return getattr(self._original, name)


# ---------------------------------------------------------------------------
# Stage-aware progress parsing
# ---------------------------------------------------------------------------

_SAGEFUZZ_PROGRESS = re.compile(r"\[进度\]\s*(.+)")

# SageFuzz keywords → friendly messages
_SAGEFUZZ_KEYWORDS = {
    "加载P4源码": "加载 P4 源码",
    "已接收意图": "意图解析完成",
    "Agent1 正在执行语义分析": "Agent1: 语义分析中",
    "Agent1 已生成TaskSpec": "Agent1: TaskSpec 生成完成",
    "Agent3 正在审查TaskSpec": "Agent3: 审查 TaskSpec",
    "TaskSpec 语义审查通过": "TaskSpec 审查通过，进入生成阶段",
    "Agent3 审查未通过": "Agent3: 审查未通过，回传修订",
    "Agent2 正在生成packet_sequence": "Agent2: 生成数据包序列",
    "packet_sequence 已通过": "数据包序列审查通过",
    "Agent3 正在审查packet_sequence": "Agent3: 审查数据包序列",
    "Agent4 正在生成": None,
    "Agent5 正在审查": None,
    "Agent6 正在生成": None,
    "Oracle预测已生成": None,
    "实体失败": None,
    "Agent1 需要补充信息": "Agent1: 自动补充意图信息",
    "处理场景": None,
    "Agno memory": "初始化 Agent",
}

# P4LTL_LLM patterns (detected from loguru/agno output on stderr)
_P4LTL_PATTERNS = [
    (re.compile(r"loading.*context|load_context", re.I), "加载程序上下文"),
    (re.compile(r"decompos|intent.*feature|heuristic.*decomp", re.I), "意图特征拆解"),
    (re.compile(r"template.*family|family.*guided|_family_guided", re.I), "模板家族匹配"),
    (re.compile(r"generate.*candidate|candidate.*generat|generation.*agent", re.I), "候选规范生成中"),
    (re.compile(r"repair.*p4ltl|dsl.*repair", re.I), "DSL 修复"),
    (re.compile(r"syntax.*valid|validate.*syntax|P4LTLAgentSyntax", re.I), "语法校验"),
    (re.compile(r"context.*valid|validate.*context|context_alignment", re.I), "上下文校验"),
    (re.compile(r"semantic.*review|review.*semantic", re.I), "语义评审"),
    (re.compile(r"round.*\d|attempt.*\d|轮", re.I), None),  # pass through for round info
]


def parse_progress_line(raw_line: str, *, stage: str = "") -> Optional[str]:
    """Extract a user-friendly progress message from a raw output line.

    Parameters
    ----------
    raw_line : str
        Raw stdout/stderr line.
    stage : str
        Stage prefix, e.g. "规范生成" or "用例生成".
    """
    prefix = f"[{stage}] " if stage else ""

    # --- SageFuzz [进度] messages ---
    m = _SAGEFUZZ_PROGRESS.search(raw_line)
    if m:
        msg = m.group(1).strip()
        for keyword, friendly in _SAGEFUZZ_KEYWORDS.items():
            if keyword in msg:
                return prefix + (friendly if friendly else msg)
        return prefix + msg

    # --- P4LTL_LLM-specific patterns ---
    for pattern, friendly in _P4LTL_PATTERNS:
        if pattern.search(raw_line):
            if friendly:
                return prefix + friendly
            # For round info, extract useful part
            round_match = re.search(r"round[_ ]?(\d+)|attempt[_ ]?(\d+)|第(\d+)[/轮]", raw_line, re.I)
            if round_match:
                num = round_match.group(1) or round_match.group(2) or round_match.group(3)
                return prefix + f"第 {num} 轮处理中"
            return None

    # --- Generic WARNING from agno/loguru ---
    if "WARNING" in raw_line:
        if "Failed to parse" in raw_line or "Failed to convert" in raw_line:
            return prefix + "LLM 输出格式不匹配，重试中..."
        if "All parsing attempts failed" in raw_line:
            return None  # duplicate of above, skip
        if "timed out" in raw_line.lower():
            return prefix + "LLM 调用超时，重试中..."

    return None
