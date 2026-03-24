# agent/skills/network_analyzer.py
"""
Network Protocol Analyzer skill.

Helps users capture, inspect, and analyze network traffic for:
- Protocol analysis and packet inspection
- Network troubleshooting
- Incident investigation
- Identifying suspicious or malicious communications

Natural-language triggers
--------------------------
  "analyze this packet capture"
  "inspect my network traffic"
  "what protocol is this?"
  "help me troubleshoot my network"
  "analyze this wireshark output"
  "identify suspicious traffic"
  "interpret this pcap data"
  "explain this network packet"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_ANALYZER_KEYWORDS = re.compile(
    r"\b(packet\s*capture|pcap|wireshark|tcpdump|network\s*traffic|network\s*trace|"
    r"packet\s*analysis|protocol\s*analysis|inspect.*traffic|capture.*traffic|"
    r"network\s*packet|sniff.*traffic|traffic\s*analysis|network\s*log|"
    r"suspicious\s*traffic|malicious\s*traffic|network\s*forensic|"
    r"incident\s*investigation|network\s*troubleshoot|packet\s*inspect|"
    r"analyze.*packet|analyse.*packet|network\s*intercept)\b",
    re.IGNORECASE,
)

_PROTOCOL_KEYWORDS = re.compile(
    r"\b(tcp|udp|icmp|http|https|dns|arp|ftp|ssh|smtp|pop3|imap|snmp|"
    r"dhcp|ospf|bgp|tls|ssl|ipv4|ipv6|ethernet|802\.11)\b",
    re.IGNORECASE,
)


def is_network_analyzer_query(text: str) -> bool:
    """Return True if the text looks like a network protocol analysis intent."""
    return bool(_ANALYZER_KEYWORDS.search(text))


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------


def extract_analyzer_params(text: str) -> dict:
    """
    Extract analysis parameters from natural-language text.
    Returns a dict with keys: has_packet_data, protocols_mentioned, analysis_type.
    """
    params: dict = {
        "has_packet_data": False,
        "protocols_mentioned": [],
        "analysis_type": "general",
        "raw_text": text,
    }

    # Detect if packet/log data is embedded in the text (hex, IP addresses, flags)
    _has_hex = bool(re.search(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){3,}\b", text))
    _has_ip = bool(
        re.search(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            text,
        )
    )
    _has_flags = bool(re.search(r"\b(SYN|ACK|FIN|RST|PSH|URG|ECE|CWR)\b", text))
    params["has_packet_data"] = _has_hex or _has_ip or _has_flags

    # Protocols mentioned
    for match in _PROTOCOL_KEYWORDS.finditer(text):
        proto = match.group(0).upper()
        if proto not in params["protocols_mentioned"]:
            params["protocols_mentioned"].append(proto)

    # Analysis type
    if re.search(r"\b(suspicious|malicious|attack|threat|intrusion|anomal)\b", text, re.IGNORECASE):
        params["analysis_type"] = "security"
    elif re.search(r"\b(troubleshoot|slow|latency|timeout|unreachable|drop)\b", text, re.IGNORECASE):
        params["analysis_type"] = "troubleshooting"
    elif re.search(r"\b(forensic|incident|investigation|breach|compromise)\b", text, re.IGNORECASE):
        params["analysis_type"] = "forensic"

    return params


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """\
You are an expert network security analyst and protocol specialist. A user has asked for help analyzing network traffic or packet data.

User query:
{query}

Analysis context:
- Analysis type: {analysis_type}
- Protocols mentioned: {protocols}
- Contains packet data: {has_data}

Please provide:
1. A clear analysis of what the network traffic or data shows.
2. Identification of protocols, flows, and notable patterns.
3. Any security concerns, anomalies, or suspicious behavior observed.
4. Recommended actions or next investigation steps.
5. Relevant tools or commands (e.g., Wireshark filters, tcpdump commands) that could help.

Be specific, technical where appropriate, and practical. Do not add disclaimers or meta-commentary.
"""

_ANALYSIS_PROMPT_COMPACT = """\
You are a network security expert. Analyze the following network traffic query and provide concise findings.

Query: {query}
Type: {analysis_type} | Protocols: {protocols}

Provide: protocol/flow analysis, security concerns, and recommended next steps.
"""

_SECURITY_ANALYSIS_PROMPT = """\
You are a network security incident responder. The user has provided network traffic data that may contain suspicious or malicious activity.

Query/Data:
{query}

Please analyze for:
1. Indicators of Compromise (IoCs) — suspicious IPs, domains, ports, or patterns.
2. Known attack signatures — port scans, C2 beaconing, data exfiltration, lateral movement.
3. Protocol anomalies — unexpected flags, malformed packets, tunneling.
4. Recommended immediate response actions.
5. Evidence preservation tips for incident investigation.

Be direct and actionable. No disclaimers.
"""

_SECURITY_ANALYSIS_PROMPT_COMPACT = """\
You are a network incident responder. Analyze this for IoCs, attack signatures, and protocol anomalies.

Query: {query}

Give: IoCs found, attack type, immediate response actions.
"""


def _build_analysis_prompt(params: dict, compact: bool = False) -> str:
    protocols = ", ".join(params["protocols_mentioned"]) if params["protocols_mentioned"] else "not specified"
    query = params["raw_text"]
    analysis_type = params["analysis_type"]
    has_data = "yes" if params["has_packet_data"] else "no"

    if analysis_type == "security":
        template = _SECURITY_ANALYSIS_PROMPT_COMPACT if compact else _SECURITY_ANALYSIS_PROMPT
        return template.format(query=query)

    template = _ANALYSIS_PROMPT_COMPACT if compact else _ANALYSIS_PROMPT
    return template.format(
        query=query,
        analysis_type=analysis_type,
        protocols=protocols,
        has_data=has_data,
    )


# ---------------------------------------------------------------------------
# Main skill handler
# ---------------------------------------------------------------------------


async def handle_network_analyzer_query(
    text: str,
    internal_id: str = "unknown",
) -> Optional[str]:
    """
    Handle a network protocol analysis query.
    Returns a formatted response string, or None if not a network analyzer query.
    """
    if not is_network_analyzer_query(text):
        return None

    params = extract_analyzer_params(text)

    try:
        from llm.providers import is_local_only  # noqa: PLC0415

        compact = is_local_only()
    except Exception:
        compact = False

    prompt = _build_analysis_prompt(params, compact=compact)

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
            logger.error("Local LLM also failed for network analyzer query: %s", exc)
            return (
                "Sorry, I couldn't analyze the network traffic right now. "
                "Please try again in a moment! 🔍"
            )

    if not response or response.startswith("[Error"):
        return (
            "Sorry, I couldn't analyze the network traffic right now. "
            "Please try again in a moment! 🔍"
        )

    header = "🔬 **Network Traffic Analysis**\n\n"
    return header + response
