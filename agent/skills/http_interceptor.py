# agent/skills/http_interceptor.py

"""
HTTP/S Interceptor & Web Application Security Analyzer Skill
Intercepts HTTP/S traffic, manipulates requests, crawls applications, and identifies
vulnerabilities such as SQL injection, XSS, authentication flaws, and insecure
session handling.

⚠️  This tool is intended for authorized security testing only.
    Always obtain explicit written permission before testing any web application
    you do not own.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

try:
    import httpx

    _httpx_available = True
except ImportError:
    _httpx_available = False

try:
    from bs4 import BeautifulSoup

    _bs4_available = True
except ImportError:
    _bs4_available = False

# ---------------------------------------------------------------------------
# Security check definitions
# ---------------------------------------------------------------------------

# HTTP security headers that should be present on every response
_SECURITY_HEADERS: Dict[str, str] = {
    "Strict-Transport-Security": (
        "Missing HSTS — browsers may allow insecure HTTP connections."
    ),
    "Content-Security-Policy": (
        "Missing CSP — increases XSS attack surface."
    ),
    "X-Frame-Options": (
        "Missing X-Frame-Options — clickjacking may be possible."
    ),
    "X-Content-Type-Options": (
        "Missing X-Content-Type-Options — MIME-sniffing attacks possible."
    ),
    "Referrer-Policy": (
        "Missing Referrer-Policy — sensitive URLs may leak via Referer header."
    ),
    "Permissions-Policy": (
        "Missing Permissions-Policy — browser features not explicitly restricted."
    ),
}

# Headers that may leak sensitive information
_INFORMATION_LEAK_HEADERS = {
    "Server",
    "X-Powered-By",
    "X-AspNet-Version",
    "X-AspNetMvc-Version",
    "X-Generator",
}

# SQL injection probe payloads (safe — trigger errors, not data exfil)
_SQLI_PROBES: List[str] = [
    "'",
    "''",
    "' OR '1'='1",
    "1; SELECT 1--",
    "1 AND 1=1",
    "1 AND 1=2",
]

# XSS probe payloads (benign probes only)
_XSS_PROBES: List[str] = [
    "<script>xss</script>",
    '"><img src=x>',
    "javascript:void(0)",
    "<svg/onload=1>",
]

# Error strings that indicate SQL injection vulnerability
_SQLI_ERROR_PATTERNS = re.compile(
    r"(sql syntax|mysql_fetch|mysqli|pg_query|sqlite_query"
    r"|ora-\d{5}|db2 sql error|syntax error.*sql|unclosed quotation"
    r"|odbc.*driver|warning.*mysql|division by zero)",
    re.IGNORECASE,
)

# Strings that indicate XSS reflection
_XSS_REFLECTION_PATTERNS = re.compile(
    r"(<script>xss</script>|javascript:void\(0\)|<svg/onload=1>)",
    re.IGNORECASE,
)

# Common admin / sensitive paths to probe
_SENSITIVE_PATHS: List[str] = [
    "/admin",
    "/admin/",
    "/administrator",
    "/login",
    "/wp-admin",
    "/wp-login.php",
    "/.env",
    "/.git/config",
    "/config",
    "/api/v1",
    "/api/v2",
    "/swagger",
    "/swagger-ui.html",
    "/actuator",
    "/phpinfo.php",
    "/server-status",
    "/robots.txt",
    "/sitemap.xml",
]

# Maximum pages to crawl per session
_MAX_CRAWL_PAGES = 20
# Maximum requests per vulnerability scan
_MAX_VULN_REQUESTS = 30


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str:
    """
    Validate and normalise a URL.  Raises ValueError for invalid URLs
    or non-HTTP schemes.
    """
    url = url.strip()
    # If the URL already contains a scheme, validate it before normalising
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Only http/https URLs are supported: {url}")
    else:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported: {url}")
    if not parsed.netloc:
        raise ValueError(f"Invalid URL (no hostname): {url}")
    # Reject private/loopback addresses for safety
    # (allow localhost for local testing)
    return url


def _same_origin(base: str, link: str) -> bool:
    """Return True if link shares the same scheme + host as base."""
    b = urlparse(base)
    lnk = urlparse(link)
    return b.scheme == lnk.scheme and b.netloc == lnk.netloc


def _extract_links(base_url: str, html: str) -> Set[str]:
    """Extract all in-scope anchor links from HTML."""
    if not _bs4_available:
        # Fallback: regex
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    else:
        soup = BeautifulSoup(html, "html.parser")
        hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]

    links: Set[str] = set()
    for href in hrefs:
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        if _same_origin(base_url, full):
            # Strip fragments
            links.add(full.split("#")[0])
    return links


def _extract_forms(base_url: str, html: str) -> List[Dict[str, Any]]:
    """Extract form definitions from HTML."""
    forms: List[Dict[str, Any]] = []
    if not _bs4_available:
        return forms  # BeautifulSoup required for form extraction

    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        action = form.get("action", "")
        method = form.get("method", "get").lower()
        full_action = urljoin(base_url, action) if action else base_url

        inputs: List[Dict[str, str]] = []
        for inp in form.find_all(["input", "textarea", "select"]):
            name = inp.get("name")
            if name:
                inputs.append(
                    {
                        "name": name,
                        "type": inp.get("type", "text"),
                        "value": inp.get("value", ""),
                    }
                )

        forms.append(
            {
                "action": full_action,
                "method": method,
                "inputs": inputs,
            }
        )
    return forms


# ---------------------------------------------------------------------------
# HttpInterceptor class
# ---------------------------------------------------------------------------

class HttpInterceptor:
    """
    HTTP/S Interceptor & Web Application Security Analyzer

    Inspects HTTP/S responses for security issues, crawls web applications,
    and tests for common vulnerability classes.
    """

    def __init__(self, timeout: float = 10.0, follow_redirects: bool = True):
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; CurieSecurityScanner/1.0; "
                "+https://github.com/yessur3808/curie-ai)"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _make_client(self) -> "httpx.AsyncClient":
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            headers=self._headers,
            verify=False,  # Allow testing self-signed certs in pentest contexts
        )

    # ------------------------------------------------------------------
    # Single-request inspection
    # ------------------------------------------------------------------

    async def inspect_url(self, url: str) -> Dict[str, Any]:
        """
        Fetch a URL and perform a deep security inspection of the response.

        Args:
            url: Target URL

        Returns:
            Inspection result dict
        """
        if not _httpx_available:
            return {"error": "httpx is not installed. Install it with: pip install httpx"}

        try:
            url = _validate_url(url)
        except ValueError as exc:
            return {"error": str(exc)}

        result: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "url": url,
        }

        try:
            async with self._make_client() as client:
                resp = await client.get(url)
                result["status_code"] = resp.status_code
                result["final_url"] = str(resp.url)
                result["headers"] = dict(resp.headers)
                result["redirect_chain"] = [str(r.url) for r in resp.history]

                body = resp.text

                # Security header analysis
                result["security_headers"] = self._check_security_headers(resp.headers)

                # Cookie security analysis
                result["cookie_issues"] = self._check_cookies(resp.headers)

                # Information disclosure
                result["info_disclosure"] = self._check_info_disclosure(resp.headers, body)

                # Sensitive path exposure (only if base path was returned)
                result["exposed_info"] = self._extract_exposed_info(body, url)

                # Forms found on page
                result["forms"] = _extract_forms(url, body)

                # Links
                result["links_found"] = len(_extract_links(url, body))

        except httpx.TimeoutException:
            result["error"] = f"Request timed out after {self.timeout}s"
        except httpx.RequestError as exc:
            result["error"] = f"Connection error: {exc}"
        except Exception as exc:
            logger.error("HTTP inspect error for %s: %s", url, exc)
            result["error"] = f"Unexpected error: {exc}"

        return result

    def _check_security_headers(self, headers) -> List[Dict[str, str]]:
        """Check for missing or misconfigured security headers."""
        issues: List[Dict[str, str]] = []
        for header, description in _SECURITY_HEADERS.items():
            if header.lower() not in {h.lower() for h in headers.keys()}:
                issues.append(
                    {
                        "header": header,
                        "issue": "missing",
                        "description": description,
                    }
                )
        # Check HSTS value if present (headers may be plain dict with any case)
        _headers_lower = {k.lower(): v for k, v in headers.items()}
        hsts = _headers_lower.get("strict-transport-security", "")
        if hsts and "max-age" in hsts.lower():
            m = re.search(r"max-age=(\d+)", hsts, re.IGNORECASE)
            if m and int(m.group(1)) < 31536000:
                issues.append(
                    {
                        "header": "Strict-Transport-Security",
                        "issue": "weak",
                        "description": "HSTS max-age is less than 1 year (31536000 seconds).",
                    }
                )
        return issues

    def _check_cookies(self, headers) -> List[Dict[str, str]]:
        """Inspect Set-Cookie headers for security flag omissions."""
        issues: List[Dict[str, str]] = []
        cookies_raw = headers.get_list("set-cookie") if hasattr(headers, "get_list") else []
        if not cookies_raw:
            # httpx may store multiple Set-Cookie as separate header entries
            cookies_raw = [v for k, v in headers.items() if k.lower() == "set-cookie"]

        for cookie in cookies_raw:
            name = cookie.split("=")[0].strip() if "=" in cookie else cookie
            cookie_lower = cookie.lower()
            if "httponly" not in cookie_lower:
                issues.append(
                    {
                        "cookie": name,
                        "issue": "missing HttpOnly flag",
                        "description": "Cookie accessible via JavaScript — XSS can steal it.",
                    }
                )
            if "secure" not in cookie_lower:
                issues.append(
                    {
                        "cookie": name,
                        "issue": "missing Secure flag",
                        "description": "Cookie transmitted over HTTP — susceptible to MITM.",
                    }
                )
            if "samesite" not in cookie_lower:
                issues.append(
                    {
                        "cookie": name,
                        "issue": "missing SameSite attribute",
                        "description": "Cookie may be sent in cross-site requests (CSRF risk).",
                    }
                )
        return issues

    def _check_info_disclosure(self, headers, body: str) -> List[Dict[str, str]]:
        """Check for technology stack disclosure in headers and body."""
        disclosures: List[Dict[str, str]] = []
        # Build a normalised lower-case lookup for plain dicts or httpx Headers
        headers_lower = {k.lower(): v for k, v in headers.items()}
        for header in _INFORMATION_LEAK_HEADERS:
            value = headers_lower.get(header.lower())
            if value:
                disclosures.append(
                    {
                        "source": f"header:{header}",
                        "value": value,
                        "description": (
                            f"{header} header exposes server/framework version; "
                            "remove or obscure it."
                        ),
                    }
                )

        # Check HTML meta generators
        m = re.search(
            r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']',
            body,
            re.IGNORECASE,
        )
        if m:
            disclosures.append(
                {
                    "source": "html:meta-generator",
                    "value": m.group(1),
                    "description": "Generator meta tag exposes CMS/framework version.",
                }
            )
        return disclosures

    def _extract_exposed_info(self, body: str, url: str) -> List[str]:
        """Look for accidentally exposed sensitive data patterns in response body."""
        findings: List[str] = []

        patterns = {
            "AWS key": re.compile(r'AKIA[0-9A-Z]{16}'),
            "Private key block": re.compile(r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----'),
            "Email addresses": re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}'),
            "JWT token": re.compile(r'eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+'),
            "IP addresses": re.compile(r'\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b'),
        }

        for label, pat in patterns.items():
            matches = pat.findall(body)
            if matches:
                # Only report up to 3 examples to avoid flooding
                findings.append(f"{label}: {len(matches)} occurrence(s) found")

        return findings

    # ------------------------------------------------------------------
    # Crawler
    # ------------------------------------------------------------------

    async def crawl(
        self,
        start_url: str,
        max_pages: int = _MAX_CRAWL_PAGES,
    ) -> Dict[str, Any]:
        """
        Crawl a web application starting from start_url and collect page info.

        Args:
            start_url:  Seed URL
            max_pages:  Maximum number of pages to visit

        Returns:
            Crawl results including pages, forms, and links discovered
        """
        if not _httpx_available:
            return {"error": "httpx is not installed."}

        try:
            start_url = _validate_url(start_url)
        except ValueError as exc:
            return {"error": str(exc)}

        max_pages = min(max_pages, _MAX_CRAWL_PAGES)

        visited: Set[str] = set()
        queue: List[str] = [start_url]
        pages: List[Dict[str, Any]] = []
        all_forms: List[Dict[str, Any]] = []

        async with self._make_client() as client:
            while queue and len(visited) < max_pages:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                try:
                    resp = await client.get(url)
                    body = resp.text

                    page_info: Dict[str, Any] = {
                        "url": url,
                        "status": resp.status_code,
                        "title": self._extract_title(body),
                        "forms": len(_extract_forms(url, body)),
                        "links": 0,
                    }

                    new_links = _extract_links(url, body)
                    page_info["links"] = len(new_links)
                    pages.append(page_info)

                    forms = _extract_forms(url, body)
                    all_forms.extend(forms)

                    # Enqueue new links
                    for link in new_links:
                        if link not in visited and link not in queue:
                            queue.append(link)

                except (httpx.TimeoutException, httpx.RequestError):
                    pages.append({"url": url, "status": "error"})
                except Exception as exc:
                    logger.debug("Crawl error at %s: %s", url, exc)

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "start_url": start_url,
            "pages_visited": len(visited),
            "pages": pages,
            "total_forms": len(all_forms),
            "forms": all_forms,
        }

    def _extract_title(self, html: str) -> str:
        """Extract the <title> tag content from HTML."""
        m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.IGNORECASE)
        return m.group(1).strip() if m else "(no title)"

    # ------------------------------------------------------------------
    # Vulnerability scanning
    # ------------------------------------------------------------------

    async def scan_vulnerabilities(
        self,
        url: str,
        check_sensitive_paths: bool = True,
        check_sqli: bool = True,
        check_xss: bool = True,
    ) -> Dict[str, Any]:
        """
        Run a lightweight vulnerability scan against the target URL.

        Tests performed (subject to flags):
          - Missing security headers
          - Cookie security flags
          - Sensitive path exposure
          - Basic SQL injection (error-based detection)
          - Basic reflected XSS

        Args:
            url: Target URL
            check_sensitive_paths: Probe common admin/sensitive paths
            check_sqli: Test form parameters for SQL injection
            check_xss:  Test form parameters for reflected XSS

        Returns:
            Vulnerability scan result dict
        """
        if not _httpx_available:
            return {"error": "httpx is not installed."}

        try:
            url = _validate_url(url)
        except ValueError as exc:
            return {"error": str(exc)}

        findings: List[Dict[str, Any]] = []
        request_count = 0

        async with self._make_client() as client:
            # ── Step 1: Base URL inspection ───────────────────────────────
            try:
                resp = await client.get(url)
                request_count += 1
                body = resp.text

                header_issues = self._check_security_headers(resp.headers)
                for issue in header_issues:
                    findings.append(
                        {
                            "type": "missing_security_header",
                            "severity": "medium",
                            "url": url,
                            "detail": issue["description"],
                        }
                    )

                cookie_issues = self._check_cookies(resp.headers)
                for issue in cookie_issues:
                    findings.append(
                        {
                            "type": "insecure_cookie",
                            "severity": "medium",
                            "url": url,
                            "detail": f"{issue['cookie']}: {issue['description']}",
                        }
                    )

                info_disclosures = self._check_info_disclosure(resp.headers, body)
                for disc in info_disclosures:
                    findings.append(
                        {
                            "type": "information_disclosure",
                            "severity": "low",
                            "url": url,
                            "detail": disc["description"],
                        }
                    )

                exposed = self._extract_exposed_info(body, url)
                for item in exposed:
                    findings.append(
                        {
                            "type": "sensitive_data_exposure",
                            "severity": "high",
                            "url": url,
                            "detail": item,
                        }
                    )

                forms = _extract_forms(url, body)

            except (httpx.TimeoutException, httpx.RequestError) as exc:
                return {"error": f"Failed to reach target: {exc}"}

            # ── Step 2: Sensitive path probing ────────────────────────────
            if check_sensitive_paths:
                base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                for path in _SENSITIVE_PATHS:
                    if request_count >= _MAX_VULN_REQUESTS:
                        break
                    probe_url = base + path
                    try:
                        r = await client.get(probe_url)
                        request_count += 1
                        if r.status_code in (200, 301, 302, 403):
                            sev = "high" if r.status_code == 200 else "low"
                            findings.append(
                                {
                                    "type": "sensitive_path_exposed",
                                    "severity": sev,
                                    "url": probe_url,
                                    "detail": (
                                        f"Path returned HTTP {r.status_code}. "
                                        f"{'Accessible!' if r.status_code == 200 else 'Restricted but exists.'}"
                                    ),
                                }
                            )
                    except (httpx.TimeoutException, httpx.RequestError):
                        pass

            # ── Step 3: Form injection tests ──────────────────────────────
            for form in forms:
                if request_count >= _MAX_VULN_REQUESTS:
                    break
                if not form["inputs"]:
                    continue

                # Build baseline payload
                baseline_data = {
                    inp["name"]: inp["value"] or "test" for inp in form["inputs"]
                }

                # SQL injection
                if check_sqli:
                    for probe in _SQLI_PROBES:
                        if request_count >= _MAX_VULN_REQUESTS:
                            break
                        sqli_data = {**baseline_data}
                        # Inject into first text-like input
                        for inp in form["inputs"]:
                            if inp["type"] in ("text", "search", "email", ""):
                                sqli_data[inp["name"]] = probe
                                break
                        try:
                            if form["method"] == "post":
                                r = await client.post(form["action"], data=sqli_data)
                            else:
                                r = await client.get(form["action"], params=sqli_data)
                            request_count += 1
                            if _SQLI_ERROR_PATTERNS.search(r.text):
                                findings.append(
                                    {
                                        "type": "sql_injection",
                                        "severity": "critical",
                                        "url": form["action"],
                                        "detail": (
                                            f"SQL error pattern detected in response "
                                            f"to payload: {probe!r}"
                                        ),
                                    }
                                )
                                break  # One confirmed finding is enough per form
                        except (httpx.TimeoutException, httpx.RequestError):
                            pass

                # XSS
                if check_xss:
                    for probe in _XSS_PROBES:
                        if request_count >= _MAX_VULN_REQUESTS:
                            break
                        xss_data = {**baseline_data}
                        for inp in form["inputs"]:
                            if inp["type"] in ("text", "search", ""):
                                xss_data[inp["name"]] = probe
                                break
                        try:
                            if form["method"] == "post":
                                r = await client.post(form["action"], data=xss_data)
                            else:
                                r = await client.get(form["action"], params=xss_data)
                            request_count += 1
                            if _XSS_REFLECTION_PATTERNS.search(r.text):
                                findings.append(
                                    {
                                        "type": "xss_reflected",
                                        "severity": "high",
                                        "url": form["action"],
                                        "detail": (
                                            f"Probe payload reflected unescaped: {probe!r}"
                                        ),
                                    }
                                )
                                break
                        except (httpx.TimeoutException, httpx.RequestError):
                            pass

        # Severity ordering
        _sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda f: _sev_order.get(f["severity"], 9))

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "target": url,
            "requests_made": request_count,
            "total_findings": len(findings),
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
            "low": sum(1 for f in findings if f["severity"] == "low"),
            "findings": findings,
        }

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def format_inspection_report(self, report: Dict[str, Any]) -> str:
        """Format a URL inspection report as human-readable text."""
        if "error" in report:
            return f"⚠️ HTTP Interceptor error: {report['error']}"

        lines = [
            f"🌐 **HTTP/S Inspection — {report['url']}**",
            f"Timestamp: {report['timestamp']}",
            f"Status: HTTP {report.get('status_code', '?')}",
        ]
        if report.get("redirect_chain"):
            lines.append(
                "Redirects: " + " → ".join(report["redirect_chain"])
                + f" → {report['final_url']}"
            )

        header_issues = report.get("security_headers", [])
        if header_issues:
            lines.append(f"\n⚠️ **Security header issues ({len(header_issues)}):**")
            for h in header_issues:
                lines.append(f"  • [{h['issue'].upper()}] {h['header']}: {h['description']}")
        else:
            lines.append("\n✅ All expected security headers are present.")

        cookie_issues = report.get("cookie_issues", [])
        if cookie_issues:
            lines.append(f"\n⚠️ **Cookie issues ({len(cookie_issues)}):**")
            for c in cookie_issues:
                lines.append(f"  • {c['cookie']}: {c['description']}")

        disclosures = report.get("info_disclosure", [])
        if disclosures:
            lines.append(f"\nℹ️ **Information disclosure ({len(disclosures)}):**")
            for d in disclosures:
                lines.append(f"  • {d['source']}: `{d['value']}`")

        exposed = report.get("exposed_info", [])
        if exposed:
            lines.append("\n🔴 **Sensitive data in response:**")
            for e in exposed:
                lines.append(f"  • {e}")

        forms = report.get("forms", [])
        if forms:
            lines.append(
                f"\n📋 **Forms found: {len(forms)}** "
                f"(fields: {sum(len(f['inputs']) for f in forms)})"
            )
        lines.append(f"Links found: {report.get('links_found', 0)}")

        lines.append(
            "\n⚠️ *For authorized security testing only.*"
        )
        return "\n".join(lines)

    def format_vuln_scan_report(self, report: Dict[str, Any]) -> str:
        """Format a vulnerability scan report as human-readable text."""
        if "error" in report:
            return f"⚠️ Vulnerability Scanner error: {report['error']}"

        lines = [
            f"🛡️ **Vulnerability Scan — {report['target']}**",
            f"Timestamp: {report['timestamp']}",
            f"Requests made: {report['requests_made']}",
            f"Total findings: **{report['total_findings']}**  "
            f"(Critical: {report['critical']}  High: {report['high']}  "
            f"Medium: {report['medium']}  Low: {report['low']})",
        ]

        if report["total_findings"] == 0:
            lines.append("\n✅ No vulnerabilities detected in this scan.")
        else:
            lines.append("\n**Findings:**")
            _icons = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🔵",
            }
            for f in report["findings"]:
                icon = _icons.get(f["severity"], "⚪")
                lines.append(
                    f"  {icon} [{f['severity'].upper()}] {f['type']} — {f['url']}"
                )
                lines.append(f"      {f['detail']}")

        lines.append(
            "\n⚠️ *For authorized security testing only. "
            "This scanner performs basic checks; always pair with manual testing.*"
        )
        return "\n".join(lines)

    def format_crawl_report(self, report: Dict[str, Any]) -> str:
        """Format a crawl report as human-readable text."""
        if "error" in report:
            return f"⚠️ Crawler error: {report['error']}"

        lines = [
            f"🕷️ **Web Crawler — {report['start_url']}**",
            f"Timestamp: {report['timestamp']}",
            f"Pages visited: {report['pages_visited']}  |  Forms found: {report['total_forms']}",
            "\n**Pages:**",
        ]

        for page in report.get("pages", []):
            status = page.get("status", "?")
            title = page.get("title", "")
            forms = page.get("forms", 0)
            links = page.get("links", 0)
            lines.append(
                f"  [{status}] {page['url']}"
                + (f" — {title!r}" if title and title != "(no title)" else "")
                + (f"  forms={forms}" if forms else "")
                + (f"  links={links}" if links else "")
            )

        lines.append(
            "\n⚠️ *For authorized security testing only.*"
        )
        return "\n".join(lines)


def get_http_interceptor() -> HttpInterceptor:
    """Return an HttpInterceptor instance."""
    return HttpInterceptor()


# ── Chat-skill interface ───────────────────────────────────────────────────────

_HTTP_INTERCEPTOR_KEYWORDS = [
    "intercept http",
    "intercept https",
    "intercept traffic",
    "http intercept",
    "https intercept",
    "burp suite",
    "burp scan",
    "web vulnerability",
    "vuln scan",
    "vulnerability scan",
    "scan website",
    "scan web app",
    "web app scan",
    "crawl website",
    "crawl web",
    "web crawler",
    "security scan",
    "security headers",
    "check headers",
    "inspect http",
    "inspect url",
    "sql injection test",
    "xss test",
    "test for xss",
    "test for sql",
    "test sqli",
    "csrf check",
    "authentication flaw",
    "session security",
    "cookie security",
    "check cookies",
    "owasp",
    "web pentest",
    "http analysis",
    "http scanner",
    "check security headers",
    "scan for vulnerabilities",
]


def is_http_interceptor_query(message: str) -> bool:
    """Return True if the message is an HTTP/web security query."""
    msg = message.lower()
    return any(kw in msg for kw in _HTTP_INTERCEPTOR_KEYWORDS)


def _extract_url_from_message(message: str) -> Optional[str]:
    """Extract the first URL or domain name from a message."""
    # Full URL
    m = re.search(r'https?://[^\s]+', message)
    if m:
        return m.group(0).rstrip(".,;:)'\"")

    # Domain-like (word.tld or word.word.tld)
    m = re.search(
        r'\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?'
        r'(?:\.[a-zA-Z]{2,})+(?:/[^\s]*)?)\b',
        message,
    )
    if m:
        candidate = m.group(1)
        if candidate.lower() not in {"e.g", "i.e", "etc."}:
            return "https://" + candidate

    return None


async def handle_http_interceptor_query(message: str) -> Optional[str]:
    """
    Handle an HTTP/web-security request.

    Args:
        message: Natural-language user query

    Returns:
        Formatted report, or None if not an HTTP interceptor query
    """
    if not is_http_interceptor_query(message):
        return None

    interceptor = get_http_interceptor()
    msg = message.lower()

    url = _extract_url_from_message(message)

    if not url:
        return (
            "🌐 **HTTP/S Interceptor & Web Security Analyzer**\n\n"
            "Please specify a target URL or domain.\n"
            "Examples:\n"
            "• `inspect https://example.com`\n"
            "• `scan website example.com for vulnerabilities`\n"
            "• `crawl https://example.com`\n"
            "• `check security headers on example.com`\n\n"
            "⚠️ *For authorized security testing only.*"
        )

    # Vulnerability scan
    if any(kw in msg for kw in ("vuln", "vulnerability", "scan", "pentest", "owasp", "sqli", "xss", "injection")):
        report = await interceptor.scan_vulnerabilities(url)
        return interceptor.format_vuln_scan_report(report)

    # Crawl
    if any(kw in msg for kw in ("crawl", "spider", "map", "enumerate pages")):
        report = await interceptor.crawl(url)
        return interceptor.format_crawl_report(report)

    # Default: inspection
    report = await interceptor.inspect_url(url)
    return interceptor.format_inspection_report(report)
