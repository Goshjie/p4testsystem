#!/usr/bin/env python3
"""Intent-driven P4 testing system – CLI entry point.

Guides the user through the full pipeline:
  1. Select a P4 program / test case (or input custom intent)
  2. Generate P4LTL specification from natural language intent
  3. Generate test cases from the specification context

Each stage displays its output in a structured, screenshot-friendly format.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# Ensure the project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---- Pretty-print helpers -------------------------------------------------

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
SEPARATOR = "─" * 72


def _header(title: str, step: str = "") -> None:
    print()
    print(f"{BOLD}{CYAN}{SEPARATOR}{RESET}")
    if step:
        print(f"{BOLD}{CYAN}  [{step}] {title}{RESET}")
    else:
        print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{SEPARATOR}{RESET}")


def _kv(key: str, value: str, indent: int = 2) -> None:
    pad = " " * indent
    wrapped = textwrap.fill(
        value, width=68, initial_indent="", subsequent_indent=pad + " " * (len(key) + 2)
    )
    print(f"{pad}{BOLD}{key}{RESET}: {wrapped}")


def _success(msg: str) -> None:
    print(f"  {GREEN}✓ {msg}{RESET}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}⚠ {msg}{RESET}")


def _error(msg: str) -> None:
    print(f"  {RED}✗ {msg}{RESET}")


def _json_block(data: dict | list, max_lines: int = 30) -> None:
    """Print a truncated JSON block."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    lines = text.splitlines()
    for line in lines[:max_lines]:
        print(f"    {DIM}{line}{RESET}")
    if len(lines) > max_lines:
        print(f"    {DIM}... ({len(lines) - max_lines} more lines){RESET}")


# ---- Case selection -------------------------------------------------------

def _select_case():
    from system.programs.program_registry import get_all_cases

    cases = get_all_cases()
    _header("意图驱动的 P4 测试系统", "START")
    print()
    print(f"  {BOLD}可选的测试用例：{RESET}")
    print()
    for i, c in enumerate(cases, 1):
        short_intent = c.intent[:60] + ("..." if len(c.intent) > 60 else "")
        print(f"    {BOLD}{i:>2}.{RESET} [{c.program_name}] {short_intent}")
    print()

    while True:
        try:
            choice = input(f"  请选择测试用例编号 (1-{len(cases)})，或输入 q 退出: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if choice.lower() == "q":
            sys.exit(0)
        try:
            idx = int(choice)
            if 1 <= idx <= len(cases):
                return cases[idx - 1]
        except ValueError:
            pass
        print(f"  {RED}无效输入，请输入 1-{len(cases)} 之间的数字{RESET}")


# ---- Display helpers -------------------------------------------------------

def _display_spec_result(task) -> None:
    """Display Stage 1 result: intent vs generated spec."""
    _header("规范生成结果", "Stage 1")
    print()
    _kv("原始意图", task.natural_language_intent)
    print()

    if task.spec_generation_ok:
        _success("规范生成成功")
    elif task.spec_generation_ok is False:
        _warn("规范生成未完全通过校验（但仍产出了候选规范）")
    else:
        _error("规范生成失败")

    print()
    _kv("P4LTL 规范", task.ltl_spec_text or "(无)")
    print()

    detail = task.spec_generation_detail or {}
    attempts = detail.get("attempts", [])
    if attempts:
        last = attempts[-1]
        syn = last.get("syntax_validation", {})
        ctx = last.get("context_validation", {})
        sem = last.get("semantic_review", {})
        print(f"  {DIM}校验详情 (最后一轮):{RESET}")
        print(f"    语法校验: {'✓ 通过' if syn.get('valid') else '✗ 未通过'}")
        print(f"    上下文校验: {'✓ 通过' if ctx.get('valid') else '✗ 未通过'}")
        print(f"    语义评审: {sem.get('semantic_verdict', 'N/A')}")


def _display_testcases(task) -> None:
    """Display Stage 2 result: test cases."""
    _header("测试用例生成结果", "Stage 2")
    print()

    if not task.testcases:
        _error("未生成任何测试用例")
        return

    _success(f"共生成 {len(task.testcases)} 个测试场景")
    print()

    for i, tc in enumerate(task.testcases, 1):
        scenario = tc.get("meta", {}).get("scenario", tc.get("task_id", f"scenario_{i}"))
        kind = tc.get("meta", {}).get("scenario_kind", "")
        packets = tc.get("packet_sequence", [])
        entities = tc.get("entities", [])
        oracle = tc.get("oracle_prediction", {})

        print(f"  {BOLD}场景 {i}: {scenario}{RESET}" + (f" ({kind})" if kind else ""))
        print(f"    数据包数量: {len(packets)}")
        print(f"    控制平面规则: {len(entities)} 条")

        if packets:
            print(f"    {DIM}数据包序列:{RESET}")
            for pkt in packets:
                pid = pkt.get("packet_id", "?")
                tx = pkt.get("tx_host", "?")
                fields = pkt.get("fields", {})
                proto_stack = " / ".join(pkt.get("protocol_stack", []))
                dst_ip = fields.get("IPv4.dst", "")
                flags = fields.get("TCP.flags", "")
                desc_parts = [f"#{pid}", tx, "→", proto_stack]
                if dst_ip:
                    desc_parts.append(f"dst={dst_ip}")
                if flags:
                    desc_parts.append(f"flags={flags}")
                print(f"      {' '.join(desc_parts)}")

        if entities:
            print(f"    {DIM}控制平面规则:{RESET}")
            for ent in entities[:5]:
                table = ent.get("table_name", "?")
                action = ent.get("action_name", "?")
                match_keys = ent.get("match_keys", {})
                match_str = ", ".join(f"{k}={v}" for k, v in match_keys.items())
                print(f"      {table} [{match_str}] → {action}")
            if len(entities) > 5:
                print(f"      ... 还有 {len(entities) - 5} 条")

        preds = oracle.get("packet_predictions", [])
        if preds:
            print(f"    {DIM}Oracle 预测:{RESET}")
            for pred in preds:
                pid = pred.get("packet_id", "?")
                outcome = pred.get("expected_outcome", "?")
                rx = pred.get("expected_rx_host", "?")
                print(f"      Packet #{pid}: {outcome} → {rx}")

        print()


def _display_auto_test_step(step) -> None:
    """Display a single agent step in real time."""
    phase_icon = {"prepare": "🔧", "execute": "▶", "observe": "👁", "complete": "✓"}.get(step.phase, "·")
    action_desc = step.action or step.thought[:60]
    status_color = GREEN if step.status == "done" else (RED if step.status == "error" else YELLOW)
    print(f"  {DIM}[{step.step_id:>2}]{RESET} {phase_icon} {action_desc:<50} {status_color}{step.status}{RESET}")


def _display_verdict(task) -> None:
    """Display Stage 3+4 result: test execution and verdict."""
    _header("自动测试结果", "Stage 3+4")
    print()

    if task.test_execution_status == "completed" and task.test_verdict:
        verdict = task.test_verdict
        color = GREEN if verdict.overall == "PASS" else (RED if verdict.overall == "FAIL" else YELLOW)
        print(f"  {BOLD}测试结论: {color}{verdict.overall}{RESET}")
        print()

        if verdict.per_packet:
            print(f"  {DIM}逐包判定:{RESET}")
            for pv in verdict.per_packet:
                match_icon = f"{GREEN}✓{RESET}" if pv.match else f"{RED}✗{RESET}"
                print(f"    Packet #{pv.packet_id}: 预期 {pv.expected_outcome} → 实际 {pv.actual_outcome}  {match_icon}")
            print()

        if verdict.reasoning:
            _kv("判定推理", verdict.reasoning)
            print()

        if verdict.evidence:
            print(f"  {DIM}关键证据:{RESET}")
            for ev in verdict.evidence[:5]:
                print(f"    • {ev}")
            print()
    elif task.test_execution_status == "failed":
        _error("自动测试执行失败")
    else:
        _warn(f"测试状态: {task.test_execution_status or 'unknown'}")


def _display_summary(task) -> None:
    """Final summary tying everything together."""
    _header("任务总览", "Summary")
    print()
    _kv("Task ID", task.task_id)
    _kv("P4 程序", task.p4_program_name)
    _kv("自然语言意图", task.natural_language_intent)
    print()
    _kv("P4LTL 规范", task.ltl_spec_text or "(未生成)")
    print()
    tc_count = len(task.testcases) if task.testcases else 0
    _kv("测试用例", f"{tc_count} 个场景")

    if task.test_verdict:
        verdict = task.test_verdict
        color = GREEN if verdict.overall == "PASS" else (RED if verdict.overall == "FAIL" else YELLOW)
        print()
        _kv("测试结论", f"{color}{verdict.overall}{RESET}")

    print()
    print(f"{BOLD}{CYAN}{SEPARATOR}{RESET}")


# ---- Main -----------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="意图驱动的 P4 测试系统 CLI")
    ap.add_argument("--case-id", default=None, help="直接指定 case_id，跳过交互选择")
    ap.add_argument("--spec-only", action="store_true", help="仅生成规范，不生成测试用例")
    ap.add_argument("--auto-test", action="store_true", help="生成测试用例后自动执行测试")
    ap.add_argument("--no-confirm", action="store_true", help="跳过确认提示，自动继续")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()

    # Step 0: Select case
    if args.case_id:
        from system.programs.program_registry import get_case_by_id
        case = get_case_by_id(args.case_id)
        if case is None:
            _error(f"未找到 case_id={args.case_id}")
            return 1
    else:
        case = _select_case()

    # Show selected case
    _header(f"已选择: {case.program_name}", "Config")
    print()
    _kv("Case ID", case.case_id)
    _kv("P4 程序", case.program_name)
    _kv("意图", case.intent)
    print()

    # Create task
    from system.orchestrator import SessionOrchestrator
    orch = SessionOrchestrator()
    task = orch.create_task(case)

    _kv("Task ID", task.task_id)
    print()

    # Stage 1: Generate spec
    _header("正在生成 P4LTL 规范...", "Stage 1")
    print(f"  {DIM}调用 P4LTL_LLM IntentToP4LTLPipeline ...{RESET}")
    print()

    task = orch.generate_spec(task, case)
    _display_spec_result(task)

    if args.spec_only:
        _display_summary(task)
        return 0

    # Confirm before Stage 2
    if not args.no_confirm:
        print()
        try:
            answer = input("  是否继续生成测试用例？[Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            _display_summary(task)
            return 0
        if answer and answer != "y":
            _display_summary(task)
            return 0

    # Stage 2: Generate test cases
    _header("正在生成测试用例...", "Stage 2")
    print(f"  {DIM}调用 SageFuzz run_packet_sequence_generation ...{RESET}")
    print()

    try:
        task = orch.generate_testcases(task, case)
        _display_testcases(task)
    except Exception as exc:
        _error(f"测试用例生成失败: {exc}")
        import traceback
        traceback.print_exc()
        _display_summary(task)
        return 1

    # Stage 3+4: Auto test (optional)
    run_test = args.auto_test
    if not run_test and not args.no_confirm and task.testcases:
        print()
        try:
            answer = input("  是否执行自动测试？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            _display_summary(task)
            return 0
        run_test = answer == "y"

    if run_test and task.testcases:
        _header("正在执行自动测试...", "Stage 3+4")
        print(f"  {DIM}LLM Test Agent 正在远程环境中执行测试用例 ...{RESET}")
        print()

        try:
            task = orch.run_auto_test(task, on_step=_display_auto_test_step)
            _display_verdict(task)
        except Exception as exc:
            _error(f"自动测试失败: {exc}")
            import traceback
            traceback.print_exc()

    _display_summary(task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
