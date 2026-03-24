# agent/skills/network_scanner.py
"""
Network Scanner & Reconnaissance skill.

Helps users perform and interpret network scanning for:
- Host discovery and enumeration
- Open port detection
- Service and version identification
- OS fingerprinting
- Attack surface mapping for pentesting

Natural-language triggers
--------------------------
  "scan this network for open ports"
  "run an nmap scan on 192.168.1.0/24"
  "what ports are open on this host?"
  "enumerate services on this target"
  "help me with network reconnaissance"
  "interpret these nmap results"
  "find hosts on my network"
  "map the attack surface of this IP"
  "check for open ports on 10.0.0.1"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_SCANNER_KEYWORDS = re.compile(
    r"\b(nmap|network\s*scan|port\s*scan|host\s*discover|open\s*port|"
    r"service\s*enum|os\s*detect|os\s*fingerprint|recon(?:naissance)?|"
    r"enumerat.*network|enumerat.*host|enumerat.*service|enumerat.*port|"
    r"scan.*target|scan.*network|scan.*host|attack\s*surface|"
    r"network\s*map|network\s*sweep|ping\s*sweep|banner\s*grab|"
    r"vuln.*scan|vulnerability\s*scan|masscan|zenmap)\b",
    re.IGNORECASE,
)

_IP_CIDR_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"
)

_PORT_PATTERN = re.compile(
    r"\bport[s]?\s*(?::\s*)?(\d+(?:[,-]\d+)*)\b|\b(\d{1,5})/(?:tcp|udp)\b",
    re.IGNORECASE,
)

_SCAN_TYPE_KEYWORDS = re.compile(
    r"\b(stealth|syn|tcp\s*connect|udp\s*scan|os\s*detect|version\s*detect|"
    r"aggressive|comprehensive|quick|full\s*scan|service\s*version)\b",
    re.IGNORECASE,
)


def is_network_scanner_query(text: str) -> bool:
    """Return True if the text looks like a network scanning/reconnaissance intent."""
    return bool(_SCANNER_KEYWORDS.search(text))


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------


def extract_scanner_params(text: str) -> dict:
    """
    Extract scanning parameters from natural-language text.
    Returns a dict with keys: target, ports, scan_type, has_results.
    """
    params: dict = {
        "target": None,
        "ports": [],
        "scan_type": "general",
        "has_results": False,
        "raw_text": text,
    }

    # Target IP or CIDR
    ip_match = _IP_CIDR_PATTERN.search(text)
    if ip_match:
        params["target"] = ip_match.group(0)

    # Ports mentioned
    for match in _PORT_PATTERN.finditer(text):
        port_str = match.group(1) or match.group(2)
        if port_str and port_str not in params["ports"]:
            params["ports"].append(port_str)

    # Scan type
    scan_match = _SCAN_TYPE_KEYWORDS.search(text)
    if scan_match:
        params["scan_type"] = scan_match.group(0).lower()

    # Check if the user has pasted scan results to interpret
    _has_nmap_output = bool(
        re.search(
            r"(Nmap\s+scan\s+report|open\s+filtered|closed\s+filtered|\d+/tcp\s+open|\d+/udp\s+open)",
            text,
            re.IGNORECASE,
        )
    )
    params["has_results"] = _has_nmap_output

    return params


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SCAN_GUIDE_PROMPT = """\
You are an expert penetration tester and network security professional. A user needs help with network scanning and reconnaissance.

User query:
{query}

Scan parameters:
- Target: {target}
- Ports of interest: {ports}
- Scan type: {scan_type}

Please provide:
1. Recommended nmap or equivalent commands for this objective (with flags explained).
2. Expected output and how to interpret key findings.
3. Service/version identification tips for any discovered open ports.
4. OS fingerprinting guidance if applicable.
5. Security considerations and ethical use reminders.
6. Next steps for attack surface mapping or vulnerability assessment.

Be specific and technical. No disclaimers beyond necessary ethical context.
"""

_SCAN_GUIDE_PROMPT_COMPACT = """\
You are a pentesting expert. Help with this network scan request.

Query: {query}
Target: {target} | Ports: {ports} | Scan type: {scan_type}

Provide: nmap commands, result interpretation, and next enumeration steps.
"""

_RESULTS_INTERPRETATION_PROMPT = """\
You are an expert penetration tester analyzing network scan results. The user has provided scan output to interpret.

Scan results:
{query}

Please provide:
1. Summary of discovered hosts, open ports, and services.
2. Notable findings — unusual ports, outdated service versions, exposed services.
3. Risk assessment for each significant open port/service.
4. Recommended follow-up actions (further enumeration, exploitation vectors to research).
5. Any indicators of security misconfigurations.

Be direct and security-focused. Assume this is an authorized penetration test.
"""

_RESULTS_INTERPRETATION_PROMPT_COMPACT = """\
You are a pentester. Interpret these network scan results.

Results: {query}

Give: open ports/services summary, risk highlights, and recommended next steps.
"""


def _build_scanner_prompt(params: dict, compact: bool = False) -> str:
    query = params["raw_text"]
    target = params["target"] or "the specified target"
    ports = ", ".join(params["ports"]) if params["ports"] else "all common ports"
    scan_type = params["scan_type"]

    if params["has_results"]:
        template = _RESULTS_INTERPRETATION_PROMPT_COMPACT if compact else _RESULTS_INTERPRETATION_PROMPT
        return template.format(query=query)

    template = _SCAN_GUIDE_PROMPT_COMPACT if compact else _SCAN_GUIDE_PROMPT
    return template.format(
        query=query,
        target=target,
        ports=ports,
        scan_type=scan_type,
    )


# ---------------------------------------------------------------------------
# Main skill handler
# ---------------------------------------------------------------------------


async def handle_network_scanner_query(
    text: str,
    internal_id: str = "unknown",
) -> Optional[str]:
    """
    Handle a network scanning/reconnaissance query.
    Returns a formatted response string, or None if not a network scanner query.
    """
    if not is_network_scanner_query(text):
        return None

    params = extract_scanner_params(text)

    try:
        from llm.providers import is_local_only  # noqa: PLC0415

        compact = is_local_only()
    except Exception:
        compact = False

    prompt = _build_scanner_prompt(params, compact=compact)

    response: Optional[str] = None
    try:
        from llm.providers import ask_best_provider  # noqa: PLC0415

        response = ask_best_provider(prompt, temperature=0.3, max_tokens=None)
    except Exception as exc:
        logger.warning("providers.ask_best_provider failed: %s", exc)

    if response is None:
        try:
            from llm import manager as llm_manager  # noqa: PLC0415

            response = llm_manager.ask_llm(prompt, temperature=0.3, max_tokens=None)
        except Exception as exc:
            logger.error("Local LLM also failed for network scanner query: %s", exc)
            return (
                "Sorry, I couldn't process the network scan request right now. "
                "Please try again in a moment! 🔭"
            )

    if not response or response.startswith("[Error"):
        return (
            "Sorry, I couldn't process the network scan request right now. "
            "Please try again in a moment! 🔭"
        )

    if params["has_results"]:
        header = "📊 **Network Scan Results Analysis**\n\n"
    else:
        target_label = f" — Target: `{params['target']}`" if params["target"] else ""
        header = f"🔭 **Network Reconnaissance Guide{target_label}**\n\n"

    return header + response
