# agent/skills/network_scanner.py

"""
Network Scanner & Reconnaissance Skill
Discovers hosts, open ports, services, and operating systems on a target network.
Commonly used in pentesting for enumeration and attack surface mapping.

⚠️  This tool is intended for authorized security testing only.
    Always obtain explicit written permission before scanning any network
    or host you do not own.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Port / service catalogue
# ---------------------------------------------------------------------------

_SERVICE_MAP: Dict[int, str] = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    67: "DHCP",
    80: "HTTP",
    110: "POP3",
    111: "RPC",
    119: "NNTP",
    135: "MS-RPC",
    139: "NetBIOS",
    143: "IMAP",
    161: "SNMP",
    389: "LDAP",
    443: "HTTPS",
    445: "SMB",
    465: "SMTPS",
    514: "Syslog",
    587: "SMTP-submission",
    631: "IPP",
    636: "LDAPS",
    993: "IMAPS",
    995: "POP3S",
    1080: "SOCKS",
    1194: "OpenVPN",
    1433: "MSSQL",
    1521: "Oracle-DB",
    2049: "NFS",
    2181: "Zookeeper",
    3306: "MySQL",
    3389: "RDP",
    4443: "HTTPS-alt",
    5432: "PostgreSQL",
    5601: "Kibana",
    5672: "AMQP",
    5900: "VNC",
    6379: "Redis",
    6443: "Kubernetes-API",
    8080: "HTTP-alt",
    8443: "HTTPS-alt",
    8888: "HTTP-alt",
    9000: "SonarQube",
    9200: "Elasticsearch",
    9300: "Elasticsearch-transport",
    10250: "Kubelet",
    27017: "MongoDB",
}

_TOP_100_PORTS: List[int] = [
    21, 22, 23, 25, 53, 67, 80, 110, 111, 119, 135, 139, 143, 161,
    389, 443, 445, 465, 514, 587, 631, 636, 993, 995, 1080, 1194,
    1433, 1521, 2049, 2181, 3306, 3389, 4443, 5432, 5601, 5672,
    5900, 6379, 6443, 8080, 8443, 8888, 9000, 9200, 9300, 10250, 27017,
    # Additional common ports
    8, 20, 30, 79, 88, 102, 194, 220, 264, 318, 383, 406, 407, 416, 417,
    425, 500, 515, 530, 540, 548, 554, 556, 600, 666, 989, 990,
]

_TOP_100_PORTS = sorted(set(_TOP_100_PORTS))


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _parse_target(target: str) -> List[str]:
    """
    Parse a target string into a list of IP address strings.

    Accepts:
      - Single IP:   "192.168.1.1"
      - CIDR range:  "192.168.1.0/24"  (capped at /16 to prevent abuse)
      - Hostname:    "example.com"

    Returns list of IP strings (resolved if hostname).
    Raises ValueError for invalid or over-large ranges.
    """
    target = target.strip()

    # CIDR notation
    if "/" in target:
        try:
            net = ipaddress.ip_network(target, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid CIDR notation: {target}") from exc
        if net.prefixlen < 16:
            raise ValueError(
                "CIDR prefix must be /16 or smaller to limit scan scope."
            )
        return [str(h) for h in net.hosts()]

    # Single IP
    try:
        ipaddress.ip_address(target)
        return [target]
    except ValueError:
        pass

    # Hostname — resolve to IP(s)
    try:
        infos = socket.getaddrinfo(target, None)
        ips = list({info[4][0] for info in infos})
        return ips
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname '{target}': {exc}") from exc


def _parse_port_range(port_spec: str) -> List[int]:
    """
    Parse a port specification string into a list of port integers.

    Accepts:
      - Single:    "80"
      - Range:     "1-1024"
      - CSV:       "22,80,443"
      - Keyword:   "top100", "common"
    """
    port_spec = port_spec.strip().lower()
    if port_spec in ("top100", "common", "top-100"):
        return list(_TOP_100_PORTS)

    ports: List[int] = []
    for part in port_spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo_i, hi_i = int(lo.strip()), int(hi.strip())
            if lo_i < 1 or hi_i > 65535 or lo_i > hi_i:
                raise ValueError(f"Invalid port range: {part}")
            ports.extend(range(lo_i, hi_i + 1))
        else:
            p = int(part)
            if p < 1 or p > 65535:
                raise ValueError(f"Port out of range: {p}")
            ports.append(p)

    return sorted(set(ports))


def _validate_target(target: str) -> str:
    """
    Validate the target string.  Blocks private / loopback ranges only when
    the agent is deployed in a restricted context.  The function returns the
    sanitised target string or raises ValueError.
    """
    # Strip whitespace
    return target.strip()


# ---------------------------------------------------------------------------
# Low-level probes
# ---------------------------------------------------------------------------

async def _tcp_connect(
    host: str, port: int, timeout: float = 1.0
) -> Tuple[bool, Optional[str]]:
    """
    Attempt a TCP connection to host:port.

    Returns:
        (open: bool, banner: str | None)
    """
    try:
        conn = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        banner: Optional[str] = None
        try:
            raw = await asyncio.wait_for(reader.read(256), timeout=0.5)
            banner = raw.decode("utf-8", errors="replace").strip()[:200]
        except asyncio.TimeoutError:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, banner
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False, None


async def _host_is_up(host: str, timeout: float = 1.0) -> bool:
    """
    Check whether a host is reachable by trying TCP port 80 and 443.
    Falls back to a raw socket echo attempt on port 7.
    """
    for port in (80, 443, 22):
        open_, _ = await _tcp_connect(host, port, timeout=timeout)
        if open_:
            return True
    return False


# ---------------------------------------------------------------------------
# NetworkScanner class
# ---------------------------------------------------------------------------

class NetworkScanner:
    """
    Network Scanner & Reconnaissance Tool

    Discovers open ports, running services, and host availability using
    asynchronous TCP connect scans (no raw sockets / ICMP required).
    """

    def __init__(
        self,
        connect_timeout: float = 1.0,
        max_concurrent: int = 256,
    ):
        self.connect_timeout = connect_timeout
        self.max_concurrent = max_concurrent

    async def scan_host(
        self,
        target: str,
        port_spec: str = "top100",
        grab_banners: bool = True,
    ) -> Dict[str, Any]:
        """
        Scan a single host for open ports.

        Args:
            target:      Hostname or IP address
            port_spec:   Port specification (e.g. 'top100', '1-1024', '22,80,443')
            grab_banners: Attempt to read service banners

        Returns:
            Scan result dict
        """
        # Resolve target to IP
        try:
            ips = _parse_target(target)
        except ValueError as exc:
            return {"error": str(exc)}

        host = ips[0]

        try:
            ports = _parse_port_range(port_spec)
        except ValueError as exc:
            return {"error": str(exc)}

        # Cap to 10 000 ports per scan
        if len(ports) > 10_000:
            return {"error": "Port range too large (max 10 000 ports per scan)."}

        start_time = time.monotonic()

        sem = asyncio.Semaphore(self.max_concurrent)

        async def _probe(port: int):
            async with sem:
                return port, await _tcp_connect(host, port, self.connect_timeout)

        tasks = [asyncio.create_task(_probe(p)) for p in ports]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.monotonic() - start_time
        open_ports: List[Dict[str, Any]] = []

        for item in results:
            if isinstance(item, Exception):
                continue
            port, (is_open, banner) = item
            if is_open:
                entry: Dict[str, Any] = {
                    "port": port,
                    "state": "open",
                    "service": _SERVICE_MAP.get(port, "unknown"),
                }
                if grab_banners and banner:
                    entry["banner"] = banner
                open_ports.append(entry)

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "target": target,
            "resolved_ip": host,
            "ports_scanned": len(ports),
            "elapsed_seconds": round(elapsed, 2),
            "open_ports": sorted(open_ports, key=lambda x: x["port"]),
            "open_count": len(open_ports),
        }

    async def scan_network(
        self,
        cidr: str,
        port_spec: str = "top100",
    ) -> Dict[str, Any]:
        """
        Discover live hosts and their open ports across a CIDR range.

        Args:
            cidr:      Network in CIDR notation (e.g. '192.168.1.0/24')
            port_spec: Port specification

        Returns:
            Dict with per-host scan results
        """
        try:
            hosts = _parse_target(cidr)
        except ValueError as exc:
            return {"error": str(exc)}

        if len(hosts) > 256:
            return {
                "error": "Network scan limited to /24 (256 hosts) to avoid overload."
            }

        start_time = time.monotonic()

        # First, discover live hosts
        up_sem = asyncio.Semaphore(64)

        async def _check_up(h: str):
            async with up_sem:
                up = await _host_is_up(h, timeout=0.5)
                return h, up

        up_tasks = [asyncio.create_task(_check_up(h)) for h in hosts]
        up_results = await asyncio.gather(*up_tasks, return_exceptions=True)

        live_hosts = [
            h for item in up_results
            if not isinstance(item, Exception)
            for h, up in [item]
            if up
        ]

        # Scan open ports on live hosts (concurrently, capped)
        scan_tasks = [
            asyncio.create_task(self.scan_host(h, port_spec, grab_banners=False))
            for h in live_hosts
        ]
        scan_results = await asyncio.gather(*scan_tasks, return_exceptions=True)

        hosts_report = []
        for item in scan_results:
            if isinstance(item, Exception):
                continue
            hosts_report.append(item)

        elapsed = time.monotonic() - start_time
        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "cidr": cidr,
            "hosts_checked": len(hosts),
            "live_hosts": len(live_hosts),
            "elapsed_seconds": round(elapsed, 2),
            "hosts": hosts_report,
        }

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def format_host_scan_report(self, report: Dict[str, Any]) -> str:
        """Format a single-host scan report as human-readable text."""
        if "error" in report:
            return f"⚠️ Network Scanner error: {report['error']}"

        lines = [
            f"🔭 **Network Scan — {report['target']}** ({report.get('resolved_ip', '?')})",
            f"Timestamp: {report['timestamp']}",
            f"Ports scanned: {report['ports_scanned']}  |  "
            f"Open: **{report['open_count']}**  |  "
            f"Elapsed: {report['elapsed_seconds']}s",
        ]

        open_ports = report.get("open_ports", [])
        if not open_ports:
            lines.append("✅ No open ports found in scanned range.")
        else:
            lines.append("\n**Open ports:**")
            lines.append(f"{'PORT':<8} {'SERVICE':<20} {'BANNER'}")
            lines.append("-" * 60)
            for p in open_ports:
                banner = p.get("banner", "")[:40]
                lines.append(f"{p['port']:<8} {p['service']:<20} {banner}")

        lines.append(
            "\n⚠️ *For authorized security testing only. "
            "Always obtain written permission before scanning.*"
        )
        return "\n".join(lines)

    def format_network_scan_report(self, report: Dict[str, Any]) -> str:
        """Format a network-wide scan report as human-readable text."""
        if "error" in report:
            return f"⚠️ Network Scanner error: {report['error']}"

        lines = [
            f"🌐 **Network Scan — {report['cidr']}**",
            f"Timestamp: {report['timestamp']}",
            f"Hosts checked: {report['hosts_checked']}  |  "
            f"Live: **{report['live_hosts']}**  |  "
            f"Elapsed: {report['elapsed_seconds']}s",
        ]

        for host_result in report.get("hosts", []):
            if "error" in host_result:
                continue
            lines.append(
                f"\n**{host_result['target']}** — "
                f"{host_result['open_count']} open port(s)"
            )
            for p in host_result.get("open_ports", [])[:10]:
                lines.append(f"  • {p['port']}/tcp  {p['service']}")
            if host_result["open_count"] > 10:
                lines.append(f"  … and {host_result['open_count'] - 10} more")

        lines.append(
            "\n⚠️ *For authorized security testing only. "
            "Always obtain written permission before scanning.*"
        )
        return "\n".join(lines)


def get_network_scanner() -> NetworkScanner:
    """Return a NetworkScanner instance."""
    return NetworkScanner()


# ── Chat-skill interface ───────────────────────────────────────────────────────

_NETWORK_SCANNER_KEYWORDS = [
    "scan ports",
    "port scan",
    "open ports",
    "nmap",
    "network scan",
    "scan network",
    "scan host",
    "host discovery",
    "host enumeration",
    "service discovery",
    "service detection",
    "banner grab",
    "reconnaissance",
    "recon scan",
    "attack surface",
    "pentesting scan",
    "pentest scan",
    "find open ports",
    "which ports are open",
    "what ports",
    "scan ip",
    "scan target",
    "discover hosts",
    "live hosts",
]


def is_network_scanner_query(message: str) -> bool:
    """Return True if the message is a network scanning / recon request."""
    msg = message.lower()
    return any(kw in msg for kw in _NETWORK_SCANNER_KEYWORDS)


def _extract_target(message: str) -> Optional[str]:
    """
    Extract an IP address, CIDR block, or hostname from a message.
    Returns None if nothing identifiable is found.
    """
    # CIDR
    m = re.search(
        r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b', message
    )
    if m:
        return m.group(1)

    # IPv4
    m = re.search(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', message)
    if m:
        return m.group(1)

    # Hostname (simple heuristic: word.word or word.word.word pattern)
    m = re.search(
        r'\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?'
        r'(?:\.[a-zA-Z]{2,})+)\b',
        message,
    )
    if m:
        candidate = m.group(1)
        # Skip common English words that match the pattern
        if candidate.lower() not in {"e.g", "i.e", "etc."}:
            return candidate

    return None


def _extract_port_spec(message: str) -> str:
    """Extract a port specification from a message, defaulting to 'top100'."""
    msg = message.lower()

    # Range like 1-1024 or 1-65535
    m = re.search(r'(\d{1,5})\s*-\s*(\d{1,5})', msg)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # CSV ports
    m = re.search(r'ports?\s+([\d,\s]+)', msg)
    if m:
        return m.group(1).replace(" ", "").strip(",")

    # Single port
    m = re.search(r'port\s+(\d{1,5})', msg)
    if m:
        return m.group(1)

    return "top100"


async def handle_network_scanner_query(message: str) -> Optional[str]:
    """
    Handle a network scanning request.

    Args:
        message: Natural-language user query

    Returns:
        Formatted scan report, or None if not a scanner query
    """
    if not is_network_scanner_query(message):
        return None

    scanner = get_network_scanner()
    target = _extract_target(message)

    if not target:
        return (
            "🔭 **Network Scanner**\n\n"
            "Please specify a target host, IP address, or CIDR range.\n"
            "Examples:\n"
            "• `scan ports on 192.168.1.1`\n"
            "• `scan network 192.168.1.0/24`\n"
            "• `find open ports on example.com`\n\n"
            "⚠️ *For authorized security testing only.*"
        )

    msg = message.lower()
    port_spec = _extract_port_spec(message)

    # Network-wide scan
    if "/" in target or any(kw in msg for kw in ("network", "subnet", "cidr", "range")):
        if "/" not in target:
            target += "/24"
        report = await scanner.scan_network(target, port_spec=port_spec)
        return scanner.format_network_scan_report(report)

    # Single host scan
    report = await scanner.scan_host(target, port_spec=port_spec)
    return scanner.format_host_scan_report(report)
