# agent/skills/http_interceptor.py
"""
HTTP/S Traffic Interceptor & Web Vulnerability Analysis skill.

Helps users analyze HTTP/S traffic and identify web application vulnerabilities:
- Request/response inspection and manipulation guidance
- SQL injection detection and analysis
- Cross-site scripting (XSS) identification
- Authentication flaw analysis
- Insecure session handling detection
- Application crawling guidance
- OWASP Top 10 vulnerability assessment

Natural-language triggers
--------------------------
  "analyze this HTTP request for vulnerabilities"
  "check this web request for SQL injection"
  "help me find XSS vulnerabilities"
  "inspect this HTTP response"
  "analyze authentication in this request"
  "check for insecure session handling"
  "interpret this burp suite output"
  "test this API endpoint for vulnerabilities"
  "analyze web application traffic"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_INTERCEPTOR_KEYWORDS = re.compile(
    r"\b(http\s*intercept|https\s*intercept|intercept.*request|intercept.*traffic|"
    r"burp\s*suite|burp\s*proxy|web\s*proxy|proxy.*traffic|"
    r"http.*vuln|https.*vuln|web.*vuln|web\s*app.*security|"
    r"sql\s*inject|sqli|xss|cross.site.script|"
    r"auth.*flaw|auth.*bypass|session\s*hijack|csrf|"
    r"insecure\s*session|session\s*token|cookie.*security|"
    r"web\s*crawl|application\s*crawl|spider.*web|"
    r"owasp|pentest.*web|web.*pentest|api.*pentest|"
    r"http\s*request.*analyz|analyz.*http|inspect.*http|"
    r"web\s*traffic\s*analyz|analyz.*web\s*traffic|"
    r"request.*manipulation|response.*manipulation|"
    r"directory\s*traversal|path\s*traversal|idor|broken.*access|"
    r"jwt.*analyz|token.*analyz)\b",
    re.IGNORECASE,
)

_HTTP_DATA_PATTERN = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+/",
    re.IGNORECASE,
)

_VULN_KEYWORDS = re.compile(
    r"\b(sql\s*inject|sqli|xss|cross.site.script|csrf|ssrf|xxe|"
    r"auth.*bypass|broken\s*auth|session\s*fix|open\s*redirect|"
    r"command\s*inject|os\s*inject|rce|remote\s*code|lfi|rfi|"
    r"file\s*include|directory\s*traversal|path\s*traversal|"
    r"idor|insecure\s*direct|access\s*control|privilege\s*escalat|"
    r"sensitive\s*data|weak\s*crypto|security\s*misconfig)\b",
    re.IGNORECASE,
)


def is_http_interceptor_query(text: str) -> bool:
    """Return True if the text looks like an HTTP/S traffic analysis or web vulnerability intent."""
    return bool(_INTERCEPTOR_KEYWORDS.search(text))


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------


def extract_interceptor_params(text: str) -> dict:
    """
    Extract analysis parameters from natural-language text.
    Returns a dict with keys: has_http_data, vulnerability_types, analysis_focus.
    """
    params: dict = {
        "has_http_data": False,
        "vulnerability_types": [],
        "analysis_focus": "general",
        "raw_text": text,
    }

    # Detect if HTTP request/response data is embedded in the text
    params["has_http_data"] = bool(_HTTP_DATA_PATTERN.search(text))

    # Vulnerability types mentioned
    for match in _VULN_KEYWORDS.finditer(text):
        vuln = match.group(0).upper().replace(" ", "_")
        if vuln not in params["vulnerability_types"]:
            params["vulnerability_types"].append(vuln)

    # Analysis focus
    if re.search(r"\b(sql\s*inject|sqli|database)\b", text, re.IGNORECASE):
        params["analysis_focus"] = "sqli"
    elif re.search(r"\b(xss|cross.site.script)\b", text, re.IGNORECASE):
        params["analysis_focus"] = "xss"
    elif re.search(r"\b(auth|session|cookie|token|jwt|login|password)\b", text, re.IGNORECASE):
        params["analysis_focus"] = "authentication"
    elif re.search(r"\b(crawl|spider|map|discover|enumerate)\b", text, re.IGNORECASE):
        params["analysis_focus"] = "crawling"
    elif params["vulnerability_types"]:
        params["analysis_focus"] = "multi_vuln"

    return params


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_HTTP_ANALYSIS_PROMPT = """\
You are an expert web application security tester. A user needs help analyzing HTTP/S traffic or web application vulnerabilities.

User query:
{query}

Analysis context:
- Focus area: {focus}
- Vulnerabilities of interest: {vulns}
- Contains HTTP data: {has_data}

Please provide:
1. Analysis of the provided HTTP data or request pattern.
2. Vulnerability assessment — check for injection flaws, authentication issues, insecure headers.
3. Specific test payloads or Burp Suite techniques relevant to the findings.
4. Risk rating for any identified vulnerabilities.
5. Remediation recommendations.

Be technical, specific, and actionable. Assume this is an authorized penetration test.
"""

_HTTP_ANALYSIS_PROMPT_COMPACT = """\
You are a web app pentester. Analyze this HTTP/S traffic or vulnerability query.

Query: {query}
Focus: {focus} | Vulnerabilities: {vulns}

Provide: vulnerability findings, test payloads, and remediation steps.
"""

_SQLI_PROMPT = """\
You are a web application security expert specializing in SQL injection. Analyze the following for SQL injection vulnerabilities.

Input:
{query}

Please provide:
1. SQL injection vulnerability assessment — identify injectable parameters.
2. Recommended test payloads (error-based, blind, time-based).
3. Evidence of existing vulnerabilities if present in the data.
4. Database type identification if possible.
5. Remediation: parameterized queries, ORM usage, and input validation.

Be specific with payloads and findings. Assume authorized testing.
"""

_SQLI_PROMPT_COMPACT = """\
SQL injection expert. Analyze for SQLi vulnerabilities.

Input: {query}

Give: injectable parameters, test payloads, and remediation.
"""

_XSS_PROMPT = """\
You are a web application security expert specializing in Cross-Site Scripting (XSS). Analyze the following for XSS vulnerabilities.

Input:
{query}

Please provide:
1. XSS vulnerability assessment — reflected, stored, or DOM-based.
2. Recommended test payloads for each XSS type.
3. Context analysis — HTML, JavaScript, attribute, or URL context.
4. Evidence of existing vulnerabilities if present.
5. Remediation: output encoding, Content Security Policy (CSP), and sanitization.

Be specific and technical. Assume authorized testing.
"""

_XSS_PROMPT_COMPACT = """\
XSS expert. Analyze for cross-site scripting vulnerabilities.

Input: {query}

Give: XSS type, test payloads, context, and remediation.
"""

_AUTH_PROMPT = """\
You are a web application security expert specializing in authentication and session management. Analyze the following for authentication flaws and insecure session handling.

Input:
{query}

Please provide:
1. Authentication mechanism assessment — identify weaknesses.
2. Session management analysis — token entropy, expiry, secure/httponly flags.
3. Common attack vectors — brute force, credential stuffing, session fixation.
4. JWT/token analysis if present — algorithm, signature validation, claims.
5. Remediation: secure session configuration, MFA, account lockout policies.

Be specific and actionable. Assume authorized testing.
"""

_AUTH_PROMPT_COMPACT = """\
Auth security expert. Analyze for authentication and session vulnerabilities.

Input: {query}

Give: auth weaknesses, session issues, and remediation steps.
"""

_CRAWL_PROMPT = """\
You are a web application security expert. Help with application crawling and attack surface mapping.

Query:
{query}

Please provide:
1. Recommended crawling strategy and tools (Burp Suite Spider, OWASP ZAP, ffuf, etc.).
2. Endpoints to prioritize for security testing.
3. Interesting parameters and entry points to enumerate.
4. Hidden functionality discovery techniques (directory brute-forcing, parameter fuzzing).
5. Documentation on how to map the full attack surface.

Be specific with commands and techniques. Assume authorized testing.
"""

_CRAWL_PROMPT_COMPACT = """\
Web security expert. Help with application crawling and attack surface mapping.

Query: {query}

Give: crawling approach, tools to use, and key endpoints to target.
"""


def _build_interceptor_prompt(params: dict, compact: bool = False) -> str:
    query = params["raw_text"]
    focus = params["analysis_focus"]
    vulns = ", ".join(params["vulnerability_types"]) if params["vulnerability_types"] else "general"
    has_data = "yes" if params["has_http_data"] else "no"

    if focus == "sqli":
        template = _SQLI_PROMPT_COMPACT if compact else _SQLI_PROMPT
        return template.format(query=query)
    elif focus == "xss":
        template = _XSS_PROMPT_COMPACT if compact else _XSS_PROMPT
        return template.format(query=query)
    elif focus == "authentication":
        template = _AUTH_PROMPT_COMPACT if compact else _AUTH_PROMPT
        return template.format(query=query)
    elif focus == "crawling":
        template = _CRAWL_PROMPT_COMPACT if compact else _CRAWL_PROMPT
        return template.format(query=query)
    else:
        template = _HTTP_ANALYSIS_PROMPT_COMPACT if compact else _HTTP_ANALYSIS_PROMPT
        return template.format(query=query, focus=focus, vulns=vulns, has_data=has_data)


# ---------------------------------------------------------------------------
# Main skill handler
# ---------------------------------------------------------------------------


async def handle_http_interceptor_query(
    text: str,
    internal_id: str = "unknown",
) -> Optional[str]:
    """
    Handle an HTTP/S traffic interception and web vulnerability analysis query.
    Returns a formatted response string, or None if not an HTTP interceptor query.
    """
    if not is_http_interceptor_query(text):
        return None

    params = extract_interceptor_params(text)

    try:
        from llm.providers import is_local_only  # noqa: PLC0415

        compact = is_local_only()
    except Exception:
        compact = False

    prompt = _build_interceptor_prompt(params, compact=compact)

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
            logger.error("Local LLM also failed for HTTP interceptor query: %s", exc)
            return (
                "Sorry, I couldn't analyze the HTTP traffic right now. "
                "Please try again in a moment! 🕷️"
            )

    if not response or response.startswith("[Error"):
        return (
            "Sorry, I couldn't analyze the HTTP traffic right now. "
            "Please try again in a moment! 🕷️"
        )

    focus = params["analysis_focus"]
    headers = {
        "sqli": "💉 **SQL Injection Analysis**",
        "xss": "⚡ **XSS Vulnerability Analysis**",
        "authentication": "🔐 **Authentication & Session Analysis**",
        "crawling": "🕷️ **Application Crawling & Attack Surface**",
        "multi_vuln": "🔍 **Web Vulnerability Analysis**",
        "general": "🌐 **HTTP/S Traffic Analysis**",
    }
    header = headers.get(focus, "🌐 **HTTP/S Traffic Analysis**") + "\n\n"
    return header + response
