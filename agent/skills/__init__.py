# agent/skills/__init__.py

"""
Agent skills module - Advanced coding capabilities
"""

# Import new skills - these have minimal dependencies
try:
    from agent.skills.pair_programming import get_pair_programming, PairProgramming

    _pair_programming_available = True
except ImportError:
    _pair_programming_available = False

try:
    from agent.skills.bug_detector import get_bug_detector, detect_bugs, BugDetector

    _bug_detector_available = True
except ImportError:
    _bug_detector_available = False

try:
    from agent.skills.performance_analyzer import (
        get_performance_analyzer,
        PerformanceAnalyzer,
    )

    _performance_analyzer_available = True
except ImportError:
    _performance_analyzer_available = False

# Import existing skills with error handling
try:
    from agent.skills.coding_assistant import handle_coding_query, get_coding_assistant

    _coding_assistant_available = True
except ImportError:
    _coding_assistant_available = False

try:
    from agent.skills.conversions import handle_conversion

    _conversions_available = True
except ImportError:
    _conversions_available = False

try:
    from agent.skills.find_info import find_info

    _find_info_available = True
except ImportError:
    _find_info_available = False

try:
    from agent.skills.network_analyzer import (
        get_network_analyzer,
        handle_network_analyzer_query,
        NetworkAnalyzer,
    )

    _network_analyzer_available = True
except ImportError:
    _network_analyzer_available = False

try:
    from agent.skills.network_scanner import (
        get_network_scanner,
        handle_network_scanner_query,
        NetworkScanner,
    )

    _network_scanner_available = True
except ImportError:
    _network_scanner_available = False

try:
    from agent.skills.http_interceptor import (
        get_http_interceptor,
        handle_http_interceptor_query,
        HttpInterceptor,
    )

    _http_interceptor_available = True
except ImportError:
    _http_interceptor_available = False

# Build __all__ dynamically based on what's available
__all__ = []

if _coding_assistant_available:
    __all__.extend(["handle_coding_query", "get_coding_assistant"])

if _conversions_available:
    __all__.append("handle_conversion")

if _find_info_available:
    __all__.append("find_info")

if _pair_programming_available:
    __all__.extend(["get_pair_programming", "PairProgramming"])

if _bug_detector_available:
    __all__.extend(["get_bug_detector", "detect_bugs", "BugDetector"])

if _performance_analyzer_available:
    __all__.extend(["get_performance_analyzer", "PerformanceAnalyzer"])

if _network_analyzer_available:
    __all__.extend(
        ["get_network_analyzer", "handle_network_analyzer_query", "NetworkAnalyzer"]
    )

if _network_scanner_available:
    __all__.extend(
        ["get_network_scanner", "handle_network_scanner_query", "NetworkScanner"]
    )

if _http_interceptor_available:
    __all__.extend(
        ["get_http_interceptor", "handle_http_interceptor_query", "HttpInterceptor"]
    )
