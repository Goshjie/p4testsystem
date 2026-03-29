"""Capture stdout/stderr progress messages from P4LTL_LLM and SageFuzz pipelines
and route them to an asyncio Queue for SSE delivery.

Both pipelines print progress like:
  - P4LTL_LLM: WARNING lines, agent stage messages
  - SageFuzz:   [进度] Agent1 正在执行语义分析...

This module intercepts these messages in real time.
"""

from __future__ import annotations

import io
import re
import sys
import threading
import time
from typing import Optional


class ProgressCapture:
    """Context manager that tee's stdout to a callback while preserving normal output."""

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
    """Writes to the original stream and also calls on_line for each line."""

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


# Patterns to extract meaningful progress from pipeline output
_SAGEFUZZ_PROGRESS = re.compile(r"\[进度\]\s*(.+)")
_P4LTL_WARNING = re.compile(r"WARNING\s+(.+)")
_STAGE_KEYWORDS = {
    "加载P4源码": "加载 P4 源码",
    "已接收意图": "意图解析完成",
    "Agent1 正在执行语义分析": "Agent1: 语义分析中",
    "Agent1 已生成TaskSpec": "Agent1: 生成 TaskSpec 完成",
    "Agent3 正在审查TaskSpec": "Agent3: 审查 TaskSpec 中",
    "TaskSpec 语义审查通过": "TaskSpec 审查通过",
    "Agent3 审查未通过": "Agent3: 审查未通过，回传修订",
    "Agent2 正在生成packet_sequence": "Agent2: 生成数据包序列中",
    "packet_sequence 已通过": "数据包序列审查通过",
    "Agent3 正在审查packet_sequence": "Agent3: 审查数据包序列中",
    "处理场景": None,  # dynamic
    "Agent4 正在生成": None,
    "Agent5 正在审查": None,
    "Agent6 正在生成": None,
    "Oracle预测已生成": None,
    "实体失败": None,
    "Agent1 需要补充信息": "Agent1: 需要补充信息（自动使用默认值）",
    "输出解析失败": None,
    "意图拆解": "意图特征拆解中",
    "候选规范生成": "候选 P4LTL 规范生成中",
    "语法校验": "语法校验中",
    "上下文校验": "上下文校验中",
    "语义评审": "语义评审中",
    "Agno memory": "初始化 Agent 记忆",
}


def parse_progress_line(raw_line: str) -> Optional[str]:
    """Extract a user-friendly progress message from a raw output line."""
    m = _SAGEFUZZ_PROGRESS.search(raw_line)
    if m:
        msg = m.group(1).strip()
        for keyword in _STAGE_KEYWORDS:
            if keyword in msg:
                friendly = _STAGE_KEYWORDS[keyword]
                if friendly:
                    return friendly
                return msg
        return msg

    if "WARNING" in raw_line and ("Failed to parse" in raw_line or "Failed to convert" in raw_line):
        return "LLM 输出解析重试中..."

    return None
