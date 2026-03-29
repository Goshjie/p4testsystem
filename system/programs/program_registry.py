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
        root / "p4app.json",
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
    # ---- Firewall ----
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
                "正向场景：h1 主动发起到 h3 的 TCP 连接，应允许通信；至少验证 SYN 能转发到 h3，且 h3 返回的 SYN-ACK 能转发到 h1。\n"
                "负向场景：h3 主动向 h1 发起 TCP 连接，应被拒绝；只需验证外部发起的 SYN 被丢弃。\n"
                "端口选择：使用任意有效端口即可（例如 dport=80）。观测方式只关注包的到达/丢弃。"
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
        sagefuzz_intent={
            "intent_text": (
                "验证有状态防火墙功能（TCP）。h1 和 h2 是内部主机（internal），h3 是外部主机（external）。\n"
                "本次只需覆盖 h1 与 h3。\n"
                "正向场景：h1 先发起到 h3 的 TCP 连接（SYN），然后 h3 的返回流量（SYN-ACK）应被允许通过。\n"
                "观测方式只关注包的到达/丢弃。"
            ),
            "feature_under_test": "policy_validation",
            "topology_zone_mapping": "h1 和 h2 是 internal，h3 是 external。",
            "role_policy": "仅允许 internal 主动向 external 发起 TCP 连接；external 只能回复已建立连接。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    # ---- Heavy Hitter Detector ----
    _sagefuzz_case(
        case_id="sagefuzz:heavy-hitter:block-heavy-flow",
        root_name="Heavy_Hitter_Detector",
        intent=(
            "验证 heavy hitter 检测的一部分核心意图：当某个 TCP 流的计数超过阈值后，程序应阻断或丢弃该流。"
        ),
        admin_description=(
            "This program uses a counting bloom filter and a threshold to detect and block heavy hitter TCP flows."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证 heavy hitter 检测功能（阈值型有状态逻辑）。\n"
                "测试前提：允许人工在测试开始前将交换机阈值临时调低到 10（例如 PACKET_THRESHOLD=10）。\n"
                "场景1（正向/阈值以下）：h1→h2 发送同一条五元组 TCP 流，重复 10 个包，期望全部转发。\n"
                "场景2（负向/阈值跨越）：继续发送相同五元组的后续包（至少再 2 个），期望第 11 个及之后被丢弃。\n"
                "不要求完整三次握手；可用简单 TCP SYN 或无 payload 的 TCP 包即可。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "测试 h1 到 h2 的 TCP 流量行为。",
            "operator_constraints": "测试前允许人工将阈值临时调低到 10。",
            "traffic_pattern": "发送一条重复五元组 TCP 流以触发阈值。",
            "include_negative_case": True,
            "test_objective": "data_plane_behavior",
        },
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
        sagefuzz_intent={
            "intent_text": (
                "验证 heavy hitter 检测功能。\n"
                "场景（正向/新流隔离）：生成一条不同五元组的新 TCP 流（可改变 sport 或 dport），期望不受前一条 heavy hitter 状态影响并能正常转发。\n"
                "不要求完整三次握手。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "测试 h1 到 h2 的 TCP 流量行为，比较不同五元组的差异。",
            "traffic_pattern": "发送一条不同五元组的新 TCP 流。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    # ---- Fast Reroute ----
    _sagefuzz_case(
        case_id="sagefuzz:fast-reroute:failover-to-lfa",
        root_name="Fast-Reroute",
        intent=(
            "验证快速重路由的一部分核心意图：当主下一跳对应链路故障时，交换机应立即选择无环备用下一跳转发流量。"
        ),
        admin_description=(
            "This program stores primary and backup next hops and reads local link state "
            "to reroute traffic immediately after adjacent link failure."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证 fast reroute 功能（多阶段转发路径切换）。\n"
                "基线阶段：h2→h4 持续发送 IPv4 流量（5~10 个包）。期望包按主路径到达 h4。\n"
                "故障阶段：测试过程中人工将 s1-s2 链路断开（manual_link_event），然后继续发送同一流量。期望立即切换到备份路径，包仍能到达 h4。\n"
                "只需要正向场景，不需要单独负向场景。观测以 h4 是否收到包为准。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "测试 h2 到 h4 的转发路径，重点关注 s1-s2 链路失效前后的路径切换。",
            "operator_constraints": "测试过程中人工执行断开 s1-s2 链路操作。",
            "traffic_pattern": "持续发送 h2 到 h4 的 IPv4 流量，覆盖链路失效前后。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    _sagefuzz_case(
        case_id="sagefuzz:fast-reroute:use-primary-when-healthy",
        root_name="Fast-Reroute",
        intent=(
            "验证快速重路由的另一部分核心意图：当主链路正常时，程序应继续使用主下一跳，而不是无条件切到备用路径。"
        ),
        admin_description=(
            "The backup next hop should only be used when the primary link is down; "
            "otherwise the primary path remains active."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证 fast reroute 功能。\n"
                "基线阶段：h2→h4 发送 IPv4 流量（5~10 个包）。期望包按主路径（非备份路径）到达 h4。\n"
                "所有链路正常，不进行任何故障注入。只需正向场景。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "测试 h2 到 h4 的转发路径。",
            "traffic_pattern": "发送 h2 到 h4 的 IPv4 流量。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    # ---- Congestion Aware Load Balancing ----
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:add-telemetry-in-network",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的一部分核心意图：当 TCP 包在网络内部传输时，"
            "程序应在网络内部维护 telemetry 信息以携带路径上的队列深度。"
        ),
        admin_description=(
            "This program adds a telemetry header inside the network and updates it "
            "with queue information as packets traverse switches."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证拥塞感知负载均衡功能。\n"
                "基线阶段：h1→h5 发送多条不同五元组的 TCP 流（至少 3 条流），期望这些流分散到不同路径。\n"
                "观测重点：对比基线阶段的路径/反馈行为。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "关注 h1 到 h5 的多路径转发。",
            "observation_target": "不同路径上的负载分布",
            "expected_observation": "多条流分散到不同路径。",
            "traffic_pattern": "发送多条 TCP 流覆盖正常分流阶段。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:remove-telemetry-before-host",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的另一部分核心意图：带 telemetry 的包在离开网络到达主机前，"
            "应去掉 telemetry 头并恢复正常以太网类型。"
        ),
        admin_description=(
            "Telemetry is only for in-network switches and should be removed "
            "before packets leave towards hosts."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证拥塞感知负载均衡功能。\n"
                "h1→h5 发送 TCP 流，期望 h5 收到的包不含 telemetry 头，以太网类型恢复正常。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "关注 h1 到 h5 的转发路径。",
            "traffic_pattern": "发送 TCP 流。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
    ),
    _sagefuzz_case(
        case_id="sagefuzz:load-balancing:reroute-congested-flow",
        root_name="Congestion_Aware_Load_Balancing",
        intent=(
            "验证拥塞感知负载均衡的核心闭环意图：当出口检测到流经历拥塞并触发通知后，"
            "入口交换机最终应把该流迁移到其他路径。"
        ),
        admin_description=(
            "This program uses congestion notifications and flow re-hashing "
            "so that congested flows eventually move to another path."
        ),
        sagefuzz_intent={
            "intent_text": (
                "验证拥塞感知负载均衡功能（多流分散 + 拥塞后切换）。\n"
                "基线阶段：h1→h5 发送多条不同五元组的 TCP 流（至少 3 条流），期望分散到不同路径。\n"
                "拥塞阶段：允许通过额外高流量背景流制造某条路径拥塞；然后发送一条新的后续 TCP 流，期望其选择其他可用路径。"
            ),
            "feature_under_test": "forwarding_behavior",
            "topology_mapping": "关注 h1 到 h5 的多路径转发与拥塞后的路径切换。",
            "observation_target": "不同路径上的负载分布和拥塞后的后续路径选择",
            "expected_observation": "在基线阶段多条流分散到不同路径；发生拥塞后，后续流量切换到其他可用路径。",
            "traffic_pattern": "发送多条 TCP 流，覆盖正常分流和拥塞后重分流两个阶段。",
            "include_negative_case": False,
            "test_objective": "data_plane_behavior",
        },
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
