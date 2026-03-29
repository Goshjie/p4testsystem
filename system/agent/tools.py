"""Remote testing environment tools for the LLM Test Agent.

Each tool wraps an SSH operation against the remote BMv2/Mininet server and
returns a structured ``ToolResult``.  The agent calls these tools during its
ReAct loop to prepare, execute, and observe tests.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ToolResult:
    """Uniform return type for every agent tool invocation."""
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False


DEFAULT_REMOTE_HOST = "root@172.22.231.61"
DEFAULT_WORK_DIR = "/home/gsj/P4"
DEFAULT_TIMEOUT = 60
MAX_OUTPUT_LINES = 300


class RemoteTools:
    """Tools that operate on the remote P4 test environment via SSH."""

    def __init__(
        self,
        remote_host: str = DEFAULT_REMOTE_HOST,
        work_dir: str = DEFAULT_WORK_DIR,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.remote_host = remote_host
        self.work_dir = work_dir
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Core tools
    # ------------------------------------------------------------------

    def ssh_exec(self, command: str, *, timeout: Optional[int] = None, cwd: Optional[str] = None) -> ToolResult:
        """Execute a shell command on the remote server.

        Parameters
        ----------
        command : str
            Shell command to run remotely.
        timeout : int, optional
            Override the default timeout in seconds.
        cwd : str, optional
            Working directory on the remote server (defaults to ``self.work_dir``).
        """
        effective_cwd = cwd or self.work_dir
        wrapped = f"cd {effective_cwd} && {command}"
        return self._run_ssh(wrapped, timeout=timeout or self.timeout)

    def ssh_write_file(self, remote_path: str, content: str) -> ToolResult:
        """Write *content* to *remote_path* on the remote server."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            local_path = tmp.name

        try:
            result = self._run_scp(local_path, remote_path)
        finally:
            Path(local_path).unlink(missing_ok=True)

        return result

    def ssh_read_file(self, remote_path: str, *, max_lines: int = MAX_OUTPUT_LINES) -> ToolResult:
        """Read a file from the remote server and return its content."""
        cmd = f"head -n {max_lines} {remote_path}"
        result = self._run_ssh(cmd)
        if result.ok:
            lines = result.stdout.splitlines()
            truncated = len(lines) >= max_lines
            return ToolResult(
                ok=True,
                stdout=result.stdout,
                exit_code=0,
                truncated=truncated,
            )
        return result

    def parse_pcap(self, remote_pcap_path: str, *, max_packets: int = 50) -> ToolResult:
        """Parse a pcap file on the remote server using tshark or tcpdump.

        Returns a human-readable packet summary.
        """
        cmd = (
            f"tshark -r {remote_pcap_path} -c {max_packets} "
            f"-T fields -e frame.number -e eth.src -e eth.dst "
            f"-e ip.src -e ip.dst -e tcp.srcport -e tcp.dstport -e tcp.flags "
            f"-E header=y -E separator='|' 2>/dev/null "
            f"|| tcpdump -nn -r {remote_pcap_path} -c {max_packets} 2>/dev/null "
            f"|| echo 'No pcap parser available'"
        )
        return self._run_ssh(cmd)

    def list_pcaps(self, pcap_dir: str) -> ToolResult:
        """List available pcap files in a directory."""
        cmd = f"ls -la {pcap_dir}/*.pcap 2>/dev/null || echo 'No pcap files found'"
        return self._run_ssh(cmd)

    def cleanup_mininet(self) -> ToolResult:
        """Clean up any lingering Mininet state on the remote server."""
        return self._run_ssh("sudo mn -c 2>&1; echo 'cleanup done'", timeout=15)

    def ensure_dir(self, remote_dir: str) -> ToolResult:
        """Create a directory on the remote server (with parents)."""
        return self._run_ssh(f"mkdir -p {remote_dir}")

    # ------------------------------------------------------------------
    # Internal SSH helpers
    # ------------------------------------------------------------------

    def _run_ssh(self, command: str, *, timeout: Optional[int] = None) -> ToolResult:
        effective_timeout = timeout or self.timeout
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            self.remote_host,
            command,
        ]
        try:
            proc = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            stdout = proc.stdout
            truncated = False
            lines = stdout.splitlines()
            if len(lines) > MAX_OUTPUT_LINES:
                stdout = "\n".join(lines[:MAX_OUTPUT_LINES])
                truncated = True

            return ToolResult(
                ok=proc.returncode == 0,
                stdout=stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                truncated=truncated,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, stderr=f"Command timed out after {effective_timeout}s", exit_code=-1)
        except Exception as exc:
            return ToolResult(ok=False, stderr=str(exc), exit_code=-1)

    def _run_scp(self, local_path: str, remote_path: str) -> ToolResult:
        # Ensure parent directory exists
        parent = str(Path(remote_path).parent)
        self._run_ssh(f"mkdir -p {parent}")

        scp_cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            local_path,
            f"{self.remote_host}:{remote_path}",
        ]
        try:
            proc = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return ToolResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, stderr="SCP timed out", exit_code=-1)
        except Exception as exc:
            return ToolResult(ok=False, stderr=str(exc), exit_code=-1)


# ---------------------------------------------------------------------------
# Tool descriptors for the LLM agent (function-calling schema)
# ---------------------------------------------------------------------------

TOOL_DESCRIPTORS = [
    {
        "name": "ssh_exec",
        "description": (
            "Execute a shell command on the remote P4 test server (root@172.22.231.61). "
            "The command runs in /home/gsj/P4 by default. Use this for starting Mininet, "
            "sending packets, reading logs, etc."
        ),
        "parameters": {
            "command": {"type": "string", "description": "Shell command to execute remotely"},
            "cwd": {"type": "string", "description": "Working directory override (optional)"},
        },
        "required": ["command"],
    },
    {
        "name": "ssh_write_file",
        "description": (
            "Write content to a file on the remote test server. Use this to upload "
            "generated scripts (network.py, send_test.py, runtime JSON, etc.)."
        ),
        "parameters": {
            "remote_path": {"type": "string", "description": "Absolute path on remote server"},
            "content": {"type": "string", "description": "File content to write"},
        },
        "required": ["remote_path", "content"],
    },
    {
        "name": "ssh_read_file",
        "description": "Read a file from the remote test server. Use for checking logs, configs, etc.",
        "parameters": {
            "remote_path": {"type": "string", "description": "Absolute path on remote server"},
        },
        "required": ["remote_path"],
    },
    {
        "name": "parse_pcap",
        "description": (
            "Parse a pcap file on the remote server and return a packet summary table. "
            "Shows src/dst MAC, IP, TCP ports, and flags for each captured packet."
        ),
        "parameters": {
            "remote_pcap_path": {"type": "string", "description": "Absolute path to .pcap file"},
        },
        "required": ["remote_pcap_path"],
    },
    {
        "name": "cleanup_mininet",
        "description": "Clean up any lingering Mininet processes on the remote server. Always call this before starting a new test.",
        "parameters": {},
        "required": [],
    },
]
