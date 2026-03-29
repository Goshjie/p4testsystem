"""Registry of known P4 programs and their pre-configured test intents.

Provides a self-contained case catalogue that mirrors the benchmark cases in
P4LTL_LLM/benchmarks/benchmark_specs.py and SageFuzz/scripts/, but without
importing from either project.  The CLI uses this to let users pick a case
by number instead of typing paths manually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SAGEFUZZ_P4_ROOT = Path("/home/gosh/SageFuzz/P4")
CASE_STUDY_ROOT = Path(
    "/home/gosh/P4LTL/Artifact/benchmark/Temporal Verification/Case Study"
)
P4LTL_GUIDE_PATH = "/home/gosh/P4LTL/P4LTL_LLM/docs/P4LTL_user_guide"


@dataclass(frozen=True)
class ProgramCase:
    case_id: str
    suite: str
    program_name: str
    intent: str
    admin_description: str = ""
    root_dir: str = ""
    p4_program_paths: list[str] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    topology_path: str = ""
    extra_constraints: list[str] = field(default_factory=list)
    guide_path: str = P4LTL_GUIDE_PATH
    # SageFuzz-specific structured intent fields
    sagefuzz_intent: Optional[dict] = None


def _discover_files(root: Path, suffixes: set[str]) -> list[str]:
    """Recursively find files with given suffixes under *root*."""
    if not root.exists():
        return []
    return sorted(
        str(p) for p in root.rglob("*") if p.is_file() and p.suffix in suffixes
    )


def _discover_p4(root: Path) -> list[str]:
    return _discover_files(root, {".p4"})


def _discover_artifacts(root: Path) -> list[str]:
    return _discover_files(root, {".json", ".txtpb", ".txt", ".p4info"})


def _find_topology(root: Path) -> str:
    for candidate in [
        root / "pod-topo" / "topology.json",
        root / "topology.json",
    ]:
        if candidate.exists():
            return str(candidate)
    return ""


# ---- SageFuzz cases -------------------------------------------------------

def _sagefuzz_case(
    case_id: str,
    root_name: str,
    intent: str,
    admin_description: str,
    sagefuzz_intent: Optional[dict] = None,
) -> ProgramCase:
    root = SAGEFUZZ_P4_ROOT / root_name
    return ProgramCase(
        case_id=case_id,
        suite="sagefuzz",
        program_name=root_name,
        intent=intent,
        admin_description=admin_description,
        root_dir=str(root),
        p4_program_paths=_discover_p4(root),
        artifact_paths=_discover_artifacts(root),
        topology_path=_find_topology(root),
        sagefuzz_intent=sagefuzz_intent,
    )


SAGEFUZZ_CASES: list[ProgramCase] = [
    _sagefuzz_case(
        case_id="sagefuzz:firewall:block-new-external",
        root_name="firewall",
        intent=(
            "验证状态防火墙的一部分核心意图：外部主机不能主动发起到内部网络的新 TCP 连接；"
            "当一个外部到内部的 TCP SYN 试图建立新连接时，程序应阻断或丢弃这类包。"
        ),
        admin_description=(
            "This is a stateful firewall implemented with a bloom filter in the data plane. "
            "Hosts h1 and h2 are internal; h3 and h4 are external. "
            "This case focuses only on blocking new external-to-internal connection attempts."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证有状态防火墙功能（TCP）。h1 和 h2 是内部主机（internal），h3 是外部主机（external）。\n"
                "本次只需覆盖 h1 与 h3（不需要覆盖 h2）。\n"
                "正向场景：h1 主动发起到 h3 的 TCP 连接，应允许通信；至少验证 SYN 能转发到 h3，且 h3 返回的 SYN-ACK 能转发到 h1（不强制要求第三次 ACK）。\n"
                "负向场景：h3 主动向 h1 发起 TCP 连接，应被拒绝；只需验证外部发起的 SYN 在到达 h1 之前被丢弃且不会建立连接状态。\n"
                "端口选择：使用任意有效端口即可（例如 dport=80），不需要验证特定服务。观测方式只关注包的到达/丢弃，不需要读取寄存器或计数器。"
            ),
            "feature_under_test": "policy_validation",
            "topology_zone_mapping": "h1 和 h2 是 internal，h3 是 external。",
            "role_policy": "仅允许 internal 主动向 external 发起 TCP 连接；external 只能回复已建立连接，不能主动发起。",
            "include_negative_case": True,
            "test_objective": "data_plane_behavior",
        },
    ),
    _sagefuzz_case(
        case_id="sagefuzz:firewall:allow-return-traffic",
        root_name="firewall",
        intent=(
            "验证状态防火墙的另一部分核心意图：如果内部主机先建立了连接，"
            "那么外部主机的返回 TCP 流量应被允许通过，而不是一直被阻断。"
        ),
        admin_description=(
            "This is a stateful firewall with connection state tracked in the data plane. "
            "This case focuses on allowing reply traffic for already established connections."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:heavy-hitter:block-heavy-flow",
        root_name="Heavy_Hitter_Detector",
        intent=(
            "验证 heavy hitter 检测的一部分核心意图：当某个 TCP 流的计数超过阈值后，程序应阻断或丢弃该流。"
        ),
        admin_description=(
            "This program uses a counting bloom filter and a threshold to detect and block heavy hitter TCP flows."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:heavy-hitter:forward-normal-flow",
        root_name="Heavy_Hitter_Detector",
        intent=(
            "验证 heavy hitter 检测的另一部分核心意图：未超过阈值的正常 TCP 流应继续被正常转发，而不是被误丢弃。"
        ),
        admin_description=(
            "Flows below the heavy-hitter threshold should continue through the IPv4 forwarding path."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:fast-reroute:failover-to-lfa",
        root_name="Fast-Reroute",
        intent=(
            "验证快速重路由的一部分核心意图：当主下一跳对应链路故障时，交换机应立即选择无环备用下一跳转发流量。"
        ),
        admin_description=(
            "This program stores primary and backup next hops and reads local link state to reroute traffic immediately after adjacent link failure."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:fast-reroute:use-primary-when-healthy",
        root_name="Fast-Reroute",
        intent=(
            "验证快速重路由的另一部分核心意图：当主链路正常时，程序应继续使用主下一跳，而不是无条件切到备用路径。"
        ),
        admin_description=(
            "The backup next hop should only be used when the primary link is down; otherwise the primary path remains active."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:add-telemetry-in-network",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的一部分核心意图：当 TCP 包在网络内部传输时，程序应在网络内部维护 telemetry 信息以携带路径上的队列深度。"
        ),
        admin_description=(
            "This program adds a telemetry header inside the network and updates it with queue information as packets traverse switches."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:remove-telemetry-before-host",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的另一部分核心意图：带 telemetry 的包在离开网络到达主机前，应去掉 telemetry 头并恢复正常以太网类型。"
        ),
        admin_description=(
            "Telemetry is only for in-network switches and should be removed before packets leave towards hosts."
        ),
    ),
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:reroute-congested-flow",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的核心闭环意图：当出口检测到流经历拥塞并触发通知后，入口交换机最终应把该流迁移到其他路径，避免长期停留在拥塞路径上。"
        ),
        admin_description=(
            "This program uses congestion notifications and flow re-hashing so that congested flows eventually move to another path."
        ),
    ),
]


# ---- Public helpers -------------------------------------------------------

def get_all_cases() -> list[ProgramCase]:
    """Return all registered cases."""
    return list(SAGEFUZZ_CASES)


def get_case_by_id(case_id: str) -> Optional[ProgramCase]:
    for case in get_all_cases():
        if case.case_id == case_id:
            return case
    return None
