# agent/skills/network_analyzer.py

"""
Network Protocol Analyzer Skill
Captures and inspects live network traffic, active connections, and interface statistics.
Useful for troubleshooting, packet analysis, incident investigation, and identifying
suspicious or malicious communications across a network.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Optional heavy deps — degrade gracefully when absent
try:
    import psutil

    _psutil_available = True
except ImportError:
    _psutil_available = False

try:
    import scapy.all as scapy  # type: ignore

    _scapy_available = True
except Exception:
    _scapy_available = False

# Well-known service-port mapping used for annotation
_WELL_KNOWN_PORTS: Dict[int, str] = {
    20: "FTP-data",
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    67: "DHCP-server",
    68: "DHCP-client",
    80: "HTTP",
    110: "POP3",
    119: "NNTP",
    123: "NTP",
    143: "IMAP",
    161: "SNMP",
    194: "IRC",
    389: "LDAP",
    443: "HTTPS",
    445: "SMB",
    465: "SMTPS",
    514: "Syslog",
    587: "SMTP-submission",
    636: "LDAPS",
    993: "IMAPS",
    995: "POP3S",
    1080: "SOCKS",
    1433: "MSSQL",
    1521: "Oracle-DB",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    6881: "BitTorrent",
    8080: "HTTP-alt",
    8443: "HTTPS-alt",
    9200: "Elasticsearch",
    27017: "MongoDB",
}

# Ports commonly associated with malicious activity or unusual usage
_SUSPICIOUS_PORTS = {
    4444,   # Metasploit default
    1337,   # Hacker slang / malware
    31337,  # Elite / back-orifice
    12345,  # NetBus
    54321,  # Back-orifice 2000
    6667,   # IRC (often C2)
    6666,   # IRC (often C2)
    7777,   # Common RAT
    8888,   # Common RAT / proxy
    9001,   # Tor
    9050,   # Tor SOCKS
    1194,   # OpenVPN (can be tunnelled for C2)
}


def _port_service(port: int) -> str:
    """Return service name for a well-known port, or 'unknown'."""
    return _WELL_KNOWN_PORTS.get(port, "unknown")


def _is_suspicious_connection(conn) -> bool:
    """Heuristically flag a psutil connection as suspicious."""
    if not conn.raddr:
        return False
    rport = conn.raddr.port
    if rport in _SUSPICIOUS_PORTS or conn.laddr.port in _SUSPICIOUS_PORTS:
        return True
    # Flag plaintext protocols on non-standard ports tunnelling encrypted data
    return False


class NetworkAnalyzer:
    """
    Network Protocol Analyzer

    Provides live network traffic inspection using psutil for connection / IO
    statistics and optional scapy-based packet capture.
    """

    def __init__(self):
        self.capture_running: bool = False
        self._captured_packets: List[Dict] = []

    # ------------------------------------------------------------------
    # Active connection analysis
    # ------------------------------------------------------------------

    def get_active_connections(
        self, kind: str = "inet", include_processes: bool = True
    ) -> Dict[str, Any]:
        """
        Return a structured report of all active network connections.

        Args:
            kind: Connection kind ('inet', 'inet4', 'inet6', 'tcp', 'udp', 'all')
            include_processes: Attach process name / PID to each connection

        Returns:
            Dict with connection list, summary counters, and suspicious flags
        """
        if not _psutil_available:
            return {
                "error": "psutil is not available — install it to enable connection analysis."
            }

        try:
            raw_conns = psutil.net_connections(kind=kind)
        except (psutil.AccessDenied, PermissionError):
            return {
                "error": (
                    "Permission denied: root/admin privileges may be required "
                    "to list all network connections."
                )
            }

        connections = []
        suspicious = []

        # Build a PID→name map once (may require elevated perms — best effort)
        pid_name: Dict[int, str] = {}
        if include_processes:
            try:
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        pid_name[proc.pid] = proc.info["name"] or "unknown"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass

        status_counts: Dict[str, int] = {}
        for conn in raw_conns:
            laddr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-"
            raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "-"
            status = conn.status or "NONE"
            status_counts[status] = status_counts.get(status, 0) + 1

            entry: Dict[str, Any] = {
                "protocol": conn.type.name if hasattr(conn.type, "name") else str(conn.type),
                "local_address": laddr,
                "remote_address": raddr,
                "status": status,
                "pid": conn.pid,
                "process": pid_name.get(conn.pid or -1, "unknown"),
            }

            # Annotate service names
            if conn.laddr:
                entry["local_service"] = _port_service(conn.laddr.port)
            if conn.raddr:
                entry["remote_service"] = _port_service(conn.raddr.port)

            connections.append(entry)

            # Flag suspicious
            if _is_suspicious_connection(conn):
                suspicious.append(entry)

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "total_connections": len(connections),
            "status_breakdown": status_counts,
            "suspicious_count": len(suspicious),
            "suspicious_connections": suspicious,
            "connections": connections,
        }

    # ------------------------------------------------------------------
    # Interface and I/O statistics
    # ------------------------------------------------------------------

    def get_interface_stats(self) -> Dict[str, Any]:
        """
        Return per-interface and aggregate I/O statistics.

        Returns:
            Dict with per-interface bytes/packets sent and received
        """
        if not _psutil_available:
            return {"error": "psutil is not available."}

        stats: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "interfaces": {},
        }

        try:
            io_counters = psutil.net_io_counters(pernic=True)
            for iface, counters in io_counters.items():
                stats["interfaces"][iface] = {
                    "bytes_sent": counters.bytes_sent,
                    "bytes_recv": counters.bytes_recv,
                    "packets_sent": counters.packets_sent,
                    "packets_recv": counters.packets_recv,
                    "errors_in": counters.errin,
                    "errors_out": counters.errout,
                    "drops_in": counters.dropin,
                    "drops_out": counters.dropout,
                }
        except (psutil.AccessDenied, PermissionError) as exc:
            stats["error"] = f"Access denied: {exc}"

        try:
            addrs = psutil.net_if_addrs()
            for iface, addr_list in addrs.items():
                if iface not in stats["interfaces"]:
                    stats["interfaces"][iface] = {}
                stats["interfaces"][iface]["addresses"] = [
                    {
                        "family": a.family.name if hasattr(a.family, "name") else str(a.family),
                        "address": a.address,
                        "netmask": a.netmask,
                    }
                    for a in addr_list
                    if a.address
                ]
        except Exception:
            pass

        return stats

    # ------------------------------------------------------------------
    # Packet capture (requires scapy + root)
    # ------------------------------------------------------------------

    def capture_packets(
        self,
        count: int = 20,
        iface: Optional[str] = None,
        bpf_filter: str = "",
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        Capture live packets using scapy (requires scapy and root privileges).

        Args:
            count:      Number of packets to capture
            iface:      Network interface (None = scapy default)
            bpf_filter: BPF filter string (e.g. 'tcp port 80')
            timeout:    Capture timeout in seconds

        Returns:
            Dict with captured packet summaries
        """
        if not _scapy_available:
            return {
                "error": (
                    "scapy is not installed. Install it with `pip install scapy` and "
                    "run with root privileges to enable live packet capture."
                )
            }

        # Sanitise inputs
        count = max(1, min(count, 200))
        timeout = max(1, min(timeout, 60))

        # Validate iface name (alphanumerics, underscores, hyphens only — no dots/slashes)
        if iface and not re.match(r'^[a-zA-Z0-9_\-]{1,20}$', iface):
            return {"error": f"Invalid interface name: {iface!r}"}

        # BPF filter basic sanity — reject shell metacharacters
        if bpf_filter and re.search(r'[;&|`$<>]', bpf_filter):
            return {"error": "Invalid characters in BPF filter."}

        try:
            kwargs: Dict[str, Any] = {"count": count, "timeout": timeout, "store": True}
            if iface:
                kwargs["iface"] = iface
            if bpf_filter:
                kwargs["filter"] = bpf_filter

            packets = scapy.sniff(**kwargs)
            summaries = []
            for pkt in packets:
                summary: Dict[str, Any] = {
                    "time": float(pkt.time),
                    "summary": pkt.summary(),
                    "length": len(pkt),
                }
                # Extract IP layer details if present
                if pkt.haslayer(scapy.IP):
                    ip = pkt[scapy.IP]
                    summary["src_ip"] = ip.src
                    summary["dst_ip"] = ip.dst
                    summary["protocol"] = ip.proto
                if pkt.haslayer(scapy.TCP):
                    tcp = pkt[scapy.TCP]
                    summary["src_port"] = tcp.sport
                    summary["dst_port"] = tcp.dport
                    summary["tcp_flags"] = str(tcp.flags)
                elif pkt.haslayer(scapy.UDP):
                    udp = pkt[scapy.UDP]
                    summary["src_port"] = udp.sport
                    summary["dst_port"] = udp.dport
                summaries.append(summary)

            return {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "packet_count": len(summaries),
                "interface": iface or "default",
                "filter": bpf_filter or "none",
                "packets": summaries,
            }
        except PermissionError:
            return {
                "error": (
                    "Permission denied: root/admin privileges are required for "
                    "live packet capture."
                )
            }
        except Exception as exc:
            logger.error("Packet capture error: %s", exc)
            return {"error": f"Packet capture failed: {exc}"}

    # ------------------------------------------------------------------
    # Formatted report helpers
    # ------------------------------------------------------------------

    def format_connections_report(self, report: Dict[str, Any]) -> str:
        """Format active-connection report as human-readable text."""
        if "error" in report:
            return f"⚠️ Network Analyzer error: {report['error']}"

        lines = [
            f"🔍 **Network Connection Analysis** — {report['timestamp']}",
            f"Total connections: **{report['total_connections']}**",
        ]

        breakdown = report.get("status_breakdown", {})
        if breakdown:
            status_str = ", ".join(
                f"{s}: {n}" for s, n in sorted(breakdown.items())
            )
            lines.append(f"Status breakdown: {status_str}")

        suspicious = report.get("suspicious_connections", [])
        if suspicious:
            lines.append(
                f"\n⚠️ **Suspicious connections detected ({len(suspicious)}):**"
            )
            for c in suspicious[:10]:
                lines.append(
                    f"  • {c['protocol']} {c['local_address']} → {c['remote_address']}"
                    f"  [{c.get('remote_service', 'unknown')}] pid={c['pid']} ({c['process']})"
                )
        else:
            lines.append("✅ No obviously suspicious connections detected.")

        # Show up to 20 established connections
        established = [
            c for c in report.get("connections", []) if c["status"] == "ESTABLISHED"
        ]
        if established:
            lines.append(f"\n**Established connections ({len(established)}):**")
            for c in established[:20]:
                svc = c.get("remote_service", "")
                svc_str = f" [{svc}]" if svc and svc != "unknown" else ""
                lines.append(
                    f"  {c['protocol']} {c['local_address']} ↔ "
                    f"{c['remote_address']}{svc_str}"
                    f"  pid={c['pid']} ({c['process']})"
                )
            if len(established) > 20:
                lines.append(f"  … and {len(established) - 20} more")

        return "\n".join(lines)

    def format_interface_report(self, report: Dict[str, Any]) -> str:
        """Format interface statistics as human-readable text."""
        if "error" in report and not report.get("interfaces"):
            return f"⚠️ Network Analyzer error: {report['error']}"

        lines = [f"📡 **Network Interface Statistics** — {report['timestamp']}"]
        for iface, data in report.get("interfaces", {}).items():
            lines.append(f"\n**{iface}**")
            if "bytes_sent" in data:
                lines.append(f"  ↑ Sent:     {data['bytes_sent']:,} B  ({data['packets_sent']:,} pkts)")
                lines.append(f"  ↓ Received: {data['bytes_recv']:,} B  ({data['packets_recv']:,} pkts)")
                if data.get("errors_in") or data.get("errors_out"):
                    lines.append(
                        f"  Errors: in={data['errors_in']} out={data['errors_out']}  "
                        f"Drops: in={data['drops_in']} out={data['drops_out']}"
                    )
            for addr in data.get("addresses", []):
                lines.append(f"  {addr['family']}: {addr['address']}")
        return "\n".join(lines)

    def format_packet_report(self, report: Dict[str, Any]) -> str:
        """Format packet capture report as human-readable text."""
        if "error" in report:
            return f"⚠️ Packet Capture error: {report['error']}"

        lines = [
            f"📦 **Packet Capture** — {report['timestamp']}",
            f"Interface: {report['interface']}  Filter: {report['filter']}",
            f"Captured: {report['packet_count']} packets",
        ]
        for pkt in report.get("packets", []):
            src = f"{pkt.get('src_ip', '?')}:{pkt.get('src_port', '?')}"
            dst = f"{pkt.get('dst_ip', '?')}:{pkt.get('dst_port', '?')}"
            lines.append(f"  {src} → {dst}  {pkt.get('summary', '')[:80]}")
        return "\n".join(lines)


def get_network_analyzer() -> NetworkAnalyzer:
    """Return a NetworkAnalyzer instance."""
    return NetworkAnalyzer()


# ── Chat-skill interface ───────────────────────────────────────────────────────

_NETWORK_ANALYZER_KEYWORDS = [
    "network traffic",
    "network connections",
    "active connections",
    "packet capture",
    "capture packets",
    "wireshark",
    "tcpdump",
    "sniff packets",
    "network sniff",
    "network interface",
    "interface stats",
    "network stats",
    "network monitor",
    "network analysis",
    "traffic analysis",
    "suspicious connections",
    "malicious traffic",
    "network incidents",
    "packet inspection",
    "protocol analyzer",
    "capture traffic",
    "inspect traffic",
    "network activity",
    # Natural language / conversational phrases
    "what is connected",
    "what's connected",
    "who is connected",
    "who's connected",
    "show connections",
    "check connections",
    "list connections",
    "show my connections",
    "what devices are connected",
    "network health",
    "network usage",
    "show my traffic",
    "what's using my network",
    "whats using my network",
    "open connections",
    "established connections",
    "connection list",
    "who is on my network",
    "who's on my network",
    "check my network",
    "network bandwidth",
    "bytes sent",
    "bytes received",
]


def is_network_analyzer_query(message: str) -> bool:
    """Return True if the message is asking for network protocol analysis."""
    msg = message.lower()
    return any(kw in msg for kw in _NETWORK_ANALYZER_KEYWORDS)


async def handle_network_analyzer_query(message: str) -> Optional[str]:
    """
    Handle a network analysis request.

    Args:
        message: Natural-language user query

    Returns:
        Formatted analysis report, or None if not a network analysis query
    """
    if not is_network_analyzer_query(message):
        return None

    analyzer = get_network_analyzer()
    msg = message.lower()

    # Packet capture request
    if any(kw in msg for kw in ["capture", "sniff", "tcpdump", "wireshark"]):
        count = 20
        m = re.search(r'(\d+)\s*packets?', msg)
        if m:
            count = min(int(m.group(1)), 200)
        bpf = ""
        m2 = re.search(r'filter[:\s]+([a-z0-9 .]+)', msg)
        if m2:
            bpf = m2.group(1).strip()
        report = analyzer.capture_packets(count=count, bpf_filter=bpf)
        return analyzer.format_packet_report(report)

    # Interface stats request
    if any(kw in msg for kw in ["interface", "stats", "io counter", "bytes sent", "bytes received"]):
        report = analyzer.get_interface_stats()
        return analyzer.format_interface_report(report)

    # Default: show active connections
    report = analyzer.get_active_connections()
    return analyzer.format_connections_report(report)
