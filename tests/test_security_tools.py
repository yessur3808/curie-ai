# tests/test_security_tools.py

"""
Tests for the three security skill modules:
  - network_analyzer  (NetworkAnalyzer)
  - network_scanner   (NetworkScanner)
  - http_interceptor  (HttpInterceptor)
"""

import asyncio
import pytest
import socket
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# NetworkAnalyzer tests
# ---------------------------------------------------------------------------


class TestNetworkAnalyzer:
    """Tests for agent/skills/network_analyzer.py"""

    @pytest.fixture
    def analyzer(self):
        from agent.skills.network_analyzer import NetworkAnalyzer

        return NetworkAnalyzer()

    # -- get_active_connections ------------------------------------------------

    def test_get_active_connections_without_psutil(self, analyzer):
        """Returns a helpful error when psutil is unavailable."""
        with patch("agent.skills.network_analyzer._psutil_available", False):
            result = analyzer.get_active_connections()
        assert "error" in result

    def test_get_active_connections_with_psutil(self, analyzer):
        """Returns expected keys when psutil is present."""
        try:
            import psutil
        except ImportError:
            pytest.skip("psutil not installed")

        # Use a mock connection object
        FakeAddr = type("Addr", (), {"ip": "127.0.0.1", "port": 80})
        FakeConn = type(
            "Conn",
            (),
            {
                "type": MagicMock(name="SOCK_STREAM"),
                "laddr": FakeAddr(),
                "raddr": FakeAddr(),
                "status": "ESTABLISHED",
                "pid": 1,
            },
        )

        with patch("psutil.net_connections", return_value=[FakeConn()]):
            with patch("psutil.process_iter", return_value=[]):
                result = analyzer.get_active_connections()

        assert "total_connections" in result
        assert "connections" in result
        assert result["total_connections"] == 1

    def test_get_active_connections_permission_denied(self, analyzer):
        """Returns error dict on PermissionError."""
        try:
            import psutil
        except ImportError:
            pytest.skip("psutil not installed")

        with patch("psutil.net_connections", side_effect=psutil.AccessDenied(0)):
            result = analyzer.get_active_connections()
        assert "error" in result

    # -- get_interface_stats ---------------------------------------------------

    def test_get_interface_stats_without_psutil(self, analyzer):
        with patch("agent.skills.network_analyzer._psutil_available", False):
            result = analyzer.get_interface_stats()
        assert "error" in result

    def test_get_interface_stats_with_psutil(self, analyzer):
        try:
            import psutil  # noqa: F401
        except ImportError:
            pytest.skip("psutil not installed")

        FakeCounter = MagicMock(
            bytes_sent=100,
            bytes_recv=200,
            packets_sent=5,
            packets_recv=10,
            errin=0,
            errout=0,
            dropin=0,
            dropout=0,
        )

        with patch("psutil.net_io_counters", return_value={"eth0": FakeCounter}):
            with patch("psutil.net_if_addrs", return_value={}):
                result = analyzer.get_interface_stats()

        assert "interfaces" in result
        assert "eth0" in result["interfaces"]
        assert result["interfaces"]["eth0"]["bytes_sent"] == 100

    # -- capture_packets -------------------------------------------------------

    def test_capture_packets_without_scapy(self, analyzer):
        with patch("agent.skills.network_analyzer._scapy_available", False):
            result = analyzer.capture_packets()
        assert "error" in result

    def test_capture_packets_bad_iface(self, analyzer):
        with patch("agent.skills.network_analyzer._scapy_available", True):
            result = analyzer.capture_packets(iface="../etc/bad; rm -rf /")
        assert "error" in result

    def test_capture_packets_bad_bpf_filter(self, analyzer):
        with patch("agent.skills.network_analyzer._scapy_available", True):
            result = analyzer.capture_packets(bpf_filter="tcp; rm -rf /")
        assert "error" in result

    def test_capture_packets_permission_error(self, analyzer):
        mock_scapy = MagicMock()
        mock_scapy.sniff = MagicMock(side_effect=PermissionError("root required"))
        mock_scapy.IP = MagicMock
        mock_scapy.TCP = MagicMock
        mock_scapy.UDP = MagicMock
        with patch("agent.skills.network_analyzer._scapy_available", True):
            with patch("agent.skills.network_analyzer.scapy", mock_scapy, create=True):
                result = analyzer.capture_packets()
        assert "error" in result

    # -- format helpers --------------------------------------------------------

    def test_format_connections_report_error(self, analyzer):
        report = {"error": "some error"}
        text = analyzer.format_connections_report(report)
        assert "error" in text.lower() or "⚠️" in text

    def test_format_connections_report_ok(self, analyzer):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "total_connections": 2,
            "status_breakdown": {"ESTABLISHED": 2},
            "suspicious_count": 0,
            "suspicious_connections": [],
            "connections": [
                {
                    "protocol": "TCP",
                    "local_address": "127.0.0.1:8080",
                    "remote_address": "8.8.8.8:443",
                    "status": "ESTABLISHED",
                    "pid": 42,
                    "process": "python",
                    "remote_service": "HTTPS",
                }
            ],
        }
        text = analyzer.format_connections_report(report)
        assert "2" in text
        assert "ESTABLISHED" in text

    def test_format_interface_report_ok(self, analyzer):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "interfaces": {
                "lo": {
                    "bytes_sent": 1000,
                    "bytes_recv": 2000,
                    "packets_sent": 10,
                    "packets_recv": 20,
                    "errors_in": 0,
                    "errors_out": 0,
                    "drops_in": 0,
                    "drops_out": 0,
                }
            },
        }
        text = analyzer.format_interface_report(report)
        assert "lo" in text
        assert "1,000" in text or "1000" in text

    # -- is_network_analyzer_query / handle_network_analyzer_query ------------

    def test_is_network_analyzer_query_positive(self):
        from agent.skills.network_analyzer import is_network_analyzer_query

        assert is_network_analyzer_query("show me network connections")
        assert is_network_analyzer_query("capture packets on eth0")
        assert is_network_analyzer_query("analyze network traffic")
        assert is_network_analyzer_query("are there any suspicious connections?")
        # New natural language phrases
        assert is_network_analyzer_query("what is connected to my machine?")
        assert is_network_analyzer_query("who's connected?")
        assert is_network_analyzer_query("show my connections")
        assert is_network_analyzer_query("check network health")
        assert is_network_analyzer_query("what's using my network bandwidth?")
        assert is_network_analyzer_query("list open connections")
        assert is_network_analyzer_query("show established connections")

    def test_is_network_analyzer_query_negative(self):
        from agent.skills.network_analyzer import is_network_analyzer_query

        assert not is_network_analyzer_query("what is the weather today?")
        assert not is_network_analyzer_query("remind me to call Alice at 5pm")

    def test_handle_network_analyzer_query_not_matching(self):
        from agent.skills.network_analyzer import handle_network_analyzer_query

        result = asyncio.run(
            handle_network_analyzer_query("what is the weather today?")
        )
        assert result is None

    def test_handle_network_analyzer_query_connections(self, analyzer):
        from agent.skills.network_analyzer import handle_network_analyzer_query

        try:
            import psutil  # noqa: F401
        except ImportError:
            pytest.skip("psutil not installed")

        with patch("psutil.net_connections", return_value=[]):
            with patch("psutil.process_iter", return_value=[]):
                result = asyncio.run(
                    handle_network_analyzer_query("show network connections")
                )
        assert result is not None
        assert isinstance(result, str)

    def test_handle_network_analyzer_query_interface(self, analyzer):
        from agent.skills.network_analyzer import handle_network_analyzer_query

        try:
            import psutil  # noqa: F401
        except ImportError:
            pytest.skip("psutil not installed")

        FakeCounter = MagicMock(
            bytes_sent=0,
            bytes_recv=0,
            packets_sent=0,
            packets_recv=0,
            errin=0,
            errout=0,
            dropin=0,
            dropout=0,
        )
        with patch("psutil.net_io_counters", return_value={"lo": FakeCounter}):
            with patch("psutil.net_if_addrs", return_value={}):
                result = asyncio.run(
                    handle_network_analyzer_query("show network interface stats")
                )
        assert result is not None


# ---------------------------------------------------------------------------
# NetworkScanner tests
# ---------------------------------------------------------------------------


class TestNetworkScanner:
    """Tests for agent/skills/network_scanner.py"""

    @pytest.fixture
    def scanner(self):
        from agent.skills.network_scanner import NetworkScanner

        return NetworkScanner(connect_timeout=0.1)

    # -- _parse_target ---------------------------------------------------------

    def test_parse_target_single_ip(self):
        from agent.skills.network_scanner import _parse_target

        ips = _parse_target("127.0.0.1")
        assert ips == ["127.0.0.1"]

    def test_parse_target_cidr(self):
        from agent.skills.network_scanner import _parse_target

        ips = _parse_target("192.168.1.0/30")
        assert len(ips) == 2  # /30 has 2 usable hosts

    def test_parse_target_cidr_too_large(self):
        from agent.skills.network_scanner import _parse_target

        with pytest.raises(ValueError):
            _parse_target("10.0.0.0/8")

    def test_parse_target_invalid(self):
        from agent.skills.network_scanner import _parse_target

        with pytest.raises(ValueError):
            _parse_target("not_a_host_!@#$")

    # -- _parse_port_range -----------------------------------------------------

    def test_parse_port_range_single(self):
        from agent.skills.network_scanner import _parse_port_range

        assert _parse_port_range("80") == [80]

    def test_parse_port_range_range(self):
        from agent.skills.network_scanner import _parse_port_range

        ports = _parse_port_range("80-82")
        assert ports == [80, 81, 82]

    def test_parse_port_range_csv(self):
        from agent.skills.network_scanner import _parse_port_range

        ports = _parse_port_range("22,80,443")
        assert sorted(ports) == [22, 80, 443]

    def test_parse_port_range_top100(self):
        from agent.skills.network_scanner import _parse_port_range, _TOP_100_PORTS

        ports = _parse_port_range("top100")
        assert ports == list(_TOP_100_PORTS)

    def test_parse_port_range_invalid(self):
        from agent.skills.network_scanner import _parse_port_range

        with pytest.raises(ValueError):
            _parse_port_range("0")
        with pytest.raises(ValueError):
            _parse_port_range("65536")

    # -- scan_host -------------------------------------------------------------

    def test_scan_host_closed_ports(self, scanner):
        """Scanning a host where all ports refuse connections returns 0 open ports."""

        async def _run():
            # All connections refused
            with patch(
                "agent.skills.network_scanner._tcp_connect",
                return_value=(False, None),
            ):
                return await scanner.scan_host("127.0.0.1", port_spec="80,443")

        result = asyncio.run(_run())
        assert result["open_count"] == 0
        assert result["ports_scanned"] == 2

    def test_scan_host_open_port(self, scanner):
        """Scanning a host with one open port reports it correctly."""

        async def _run():
            async def fake_tcp(host, port, timeout=1.0):
                if port == 80:
                    return True, "HTTP/1.1 200 OK"
                return False, None

            with patch(
                "agent.skills.network_scanner._tcp_connect", side_effect=fake_tcp
            ):
                return await scanner.scan_host("127.0.0.1", port_spec="80,443")

        result = asyncio.run(_run())
        assert result["open_count"] == 1
        assert result["open_ports"][0]["port"] == 80
        assert result["open_ports"][0]["service"] == "HTTP"

    def test_scan_host_invalid_target(self, scanner):
        async def _run():
            return await scanner.scan_host("not_a_host_!@#$%")

        result = asyncio.run(_run())
        assert "error" in result

    def test_scan_host_port_range_too_large(self, scanner):
        async def _run():
            return await scanner.scan_host("127.0.0.1", port_spec="1-65535")

        result = asyncio.run(_run())
        # 65535 ports > 10000 limit
        assert "error" in result

    # -- format helpers --------------------------------------------------------

    def test_format_host_scan_report_error(self, scanner):
        report = {"error": "cannot reach host"}
        text = scanner.format_host_scan_report(report)
        assert "error" in text.lower() or "⚠️" in text

    def test_format_host_scan_report_ok(self, scanner):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "target": "192.168.1.1",
            "resolved_ip": "192.168.1.1",
            "ports_scanned": 100,
            "elapsed_seconds": 0.5,
            "open_count": 1,
            "open_ports": [{"port": 22, "state": "open", "service": "SSH"}],
        }
        text = scanner.format_host_scan_report(report)
        assert "22" in text
        assert "SSH" in text
        assert "authorized" in text.lower()

    def test_format_network_scan_report_ok(self, scanner):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "cidr": "192.168.1.0/30",
            "hosts_checked": 2,
            "live_hosts": 1,
            "elapsed_seconds": 1.0,
            "hosts": [
                {
                    "target": "192.168.1.1",
                    "resolved_ip": "192.168.1.1",
                    "ports_scanned": 47,
                    "elapsed_seconds": 0.9,
                    "open_count": 2,
                    "open_ports": [
                        {"port": 22, "state": "open", "service": "SSH"},
                        {"port": 80, "state": "open", "service": "HTTP"},
                    ],
                }
            ],
        }
        text = scanner.format_network_scan_report(report)
        assert "192.168.1.1" in text
        assert "SSH" in text

    # -- is_network_scanner_query / handle_network_scanner_query --------------

    def test_is_network_scanner_query_positive(self):
        from agent.skills.network_scanner import is_network_scanner_query

        assert is_network_scanner_query("scan ports on 192.168.1.1")
        assert is_network_scanner_query("find open ports on example.com")
        assert is_network_scanner_query("nmap 10.0.0.1")
        assert is_network_scanner_query("discover live hosts on 192.168.1.0/24")
        # New natural language phrases
        assert is_network_scanner_query("scan my network")
        assert is_network_scanner_query("what devices are on my network?")
        assert is_network_scanner_query("what's on my network")
        assert is_network_scanner_query("scan my local network")
        assert is_network_scanner_query("scan surrounding network")
        assert is_network_scanner_query("what connected devices are there?")
        assert is_network_scanner_query("network security scan")

    def test_is_network_scanner_query_negative(self):
        from agent.skills.network_scanner import is_network_scanner_query

        assert not is_network_scanner_query("convert 100 usd to eur")
        assert not is_network_scanner_query("remind me to water the plants")
        # "security scan" alone no longer triggers the scanner (needs network context)
        assert not is_network_scanner_query("do a security scan")

    def test_handle_network_scanner_query_no_target(self):
        from agent.skills.network_scanner import handle_network_scanner_query

        result = asyncio.run(handle_network_scanner_query("scan ports please"))
        assert result is not None
        assert "target" in result.lower() or "example" in result.lower()

    def test_handle_network_scanner_query_not_matching(self):
        from agent.skills.network_scanner import handle_network_scanner_query

        result = asyncio.run(handle_network_scanner_query("what's the weather?"))
        assert result is None

    def test_handle_network_scanner_query_with_target(self, scanner):
        from agent.skills.network_scanner import handle_network_scanner_query

        async def _run():
            with patch(
                "agent.skills.network_scanner._tcp_connect",
                return_value=(False, None),
            ):
                return await handle_network_scanner_query(
                    "scan ports on 127.0.0.1 ports 80,443"
                )

        result = asyncio.run(_run())
        assert result is not None
        assert isinstance(result, str)

    def test_extract_target_ip(self):
        from agent.skills.network_scanner import _extract_target

        assert _extract_target("scan 192.168.1.1") == "192.168.1.1"

    def test_extract_target_cidr(self):
        from agent.skills.network_scanner import _extract_target

        assert _extract_target("scan 10.0.0.0/24") == "10.0.0.0/24"

    def test_extract_target_hostname(self):
        from agent.skills.network_scanner import _extract_target

        result = _extract_target("scan example.com")
        assert result is not None
        assert "example.com" in result

    def test_extract_target_none(self):
        from agent.skills.network_scanner import _extract_target

        assert _extract_target("just a generic query with no host") is None

    # -- Hardware-aware concurrency ------------------------------------------

    def test_get_hardware_concurrency_scales_with_cpu(self):
        from agent.skills.network_scanner import _get_hardware_concurrency

        with patch("os.cpu_count", return_value=8):
            result = _get_hardware_concurrency()
        assert result >= 256  # minimum floor
        assert result <= 1024  # maximum cap

    def test_get_hardware_concurrency_min_floor(self):
        from agent.skills.network_scanner import _get_hardware_concurrency

        with patch("os.cpu_count", return_value=1):
            result = _get_hardware_concurrency()
        assert result == 256  # floor is 256

    def test_get_hardware_concurrency_max_cap(self):
        from agent.skills.network_scanner import _get_hardware_concurrency

        with patch("os.cpu_count", return_value=32):
            result = _get_hardware_concurrency()
        assert result == 1024  # cap is 1024

    def test_get_hardware_concurrency_null_cpu(self):
        from agent.skills.network_scanner import _get_hardware_concurrency

        with patch("os.cpu_count", return_value=None):
            result = _get_hardware_concurrency()
        assert result >= 256

    def test_get_network_scanner_uses_hardware_concurrency(self):
        from agent.skills.network_scanner import (
            get_network_scanner,
            _get_hardware_concurrency,
        )

        sc = get_network_scanner()
        assert sc.max_concurrent == _get_hardware_concurrency()

    # -- Local network detection ---------------------------------------------

    def test_get_local_networks_without_psutil(self):
        from agent.skills.network_scanner import _get_local_networks

        with patch("agent.skills.network_scanner._psutil_available", False):
            result = _get_local_networks()
        assert result == []

    def test_get_local_networks_with_psutil(self):
        from agent.skills.network_scanner import _get_local_networks
        import socket as _socket

        FakeAddr = MagicMock()
        FakeAddr.family.value = _socket.AF_INET
        FakeAddr.address = "192.168.1.100"
        FakeAddr.netmask = "255.255.255.0"
        fake_psutil = MagicMock()
        fake_psutil.net_if_addrs.return_value = {"eth0": [FakeAddr]}
        with patch("agent.skills.network_scanner._psutil_available", True):
            with patch("agent.skills.network_scanner._psutil", fake_psutil):
                result = _get_local_networks()
        assert "192.168.1.0/24" in result

    def test_get_local_networks_skips_loopback(self):
        from agent.skills.network_scanner import _get_local_networks
        import socket as _socket

        FakeAddr = MagicMock()
        FakeAddr.family.value = _socket.AF_INET
        FakeAddr.address = "127.0.0.1"
        FakeAddr.netmask = "255.0.0.0"
        fake_psutil = MagicMock()
        fake_psutil.net_if_addrs.return_value = {"lo": [FakeAddr]}
        with patch("agent.skills.network_scanner._psutil_available", True):
            with patch("agent.skills.network_scanner._psutil", fake_psutil):
                result = _get_local_networks()
        assert result == []

    def test_get_local_networks_narrows_large_network(self):
        """Networks wider than /16 are narrowed to /24 of the host IP."""
        from agent.skills.network_scanner import _get_local_networks
        import socket as _socket

        FakeAddr = MagicMock()
        FakeAddr.family.value = _socket.AF_INET
        FakeAddr.address = "10.0.0.50"
        FakeAddr.netmask = "255.0.0.0"  # /8 — too wide
        fake_psutil = MagicMock()
        fake_psutil.net_if_addrs.return_value = {"eth0": [FakeAddr]}
        with patch("agent.skills.network_scanner._psutil_available", True):
            with patch("agent.skills.network_scanner._psutil", fake_psutil):
                result = _get_local_networks()
        assert "10.0.0.0/24" in result

    def test_get_local_networks_deduplicates(self):
        """Duplicate CIDRs from multiple interfaces are collapsed."""
        from agent.skills.network_scanner import _get_local_networks
        import socket as _socket

        def _addr(ip):
            a = MagicMock()
            a.family.value = _socket.AF_INET
            a.address = ip
            a.netmask = "255.255.255.0"
            return a

        fake_psutil = MagicMock()
        fake_psutil.net_if_addrs.return_value = {
            "eth0": [_addr("192.168.1.10")],
            "wlan0": [_addr("192.168.1.20")],  # same /24
        }
        with patch("agent.skills.network_scanner._psutil_available", True):
            with patch("agent.skills.network_scanner._psutil", fake_psutil):
                result = _get_local_networks()
        # Both IPs map to 192.168.1.0/24 — should appear only once
        assert result.count("192.168.1.0/24") == 1

    # -- scan_local_networks --------------------------------------------------

    def test_scan_local_networks_no_psutil(self, scanner):
        async def _run():
            with patch("agent.skills.network_scanner._psutil_available", False):
                return await scanner.scan_local_networks()

        result = asyncio.run(_run())
        assert "error" in result

    def test_scan_local_networks_with_results(self, scanner):
        async def _run():
            fake_networks = ["192.168.1.0/24"]
            mock_net_report = {
                "timestamp": "2024-01-01T00:00:00Z",
                "cidr": "192.168.1.0/24",
                "hosts_checked": 254,
                "live_hosts": 3,
                "elapsed_seconds": 2.0,
                "hosts": [
                    {
                        "target": "192.168.1.1",
                        "resolved_ip": "192.168.1.1",
                        "ports_scanned": 47,
                        "elapsed_seconds": 0.5,
                        "open_count": 1,
                        "open_ports": [
                            {"port": 80, "state": "open", "service": "HTTP"}
                        ],
                    }
                ],
            }
            with patch(
                "agent.skills.network_scanner._get_local_networks",
                return_value=fake_networks,
            ):
                with patch.object(
                    scanner, "scan_network", return_value=mock_net_report
                ):
                    return await scanner.scan_local_networks()

        result = asyncio.run(_run())
        assert "error" not in result
        assert result["networks_scanned"] == 1
        assert result["total_live_hosts"] == 3
        assert "192.168.1.0/24" in result["auto_detected_networks"]

    # -- format_local_network_report -----------------------------------------

    def test_format_local_network_report_error(self, scanner):
        report = {"error": "psutil not available"}
        text = scanner.format_local_network_report(report)
        assert "⚠️" in text or "error" in text.lower()

    def test_format_local_network_report_ok(self, scanner):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "auto_detected_networks": ["192.168.1.0/24"],
            "networks_scanned": 1,
            "total_hosts_checked": 254,
            "total_live_hosts": 2,
            "port_spec": "top100",
            "networks": [
                {
                    "cidr": "192.168.1.0/24",
                    "hosts_checked": 254,
                    "live_hosts": 2,
                    "elapsed_seconds": 1.5,
                    "hosts": [
                        {
                            "target": "192.168.1.1",
                            "open_count": 1,
                            "open_ports": [{"port": 80, "service": "HTTP"}],
                        },
                        {
                            "target": "192.168.1.50",
                            "open_count": 0,
                            "open_ports": [],
                        },
                    ],
                }
            ],
        }
        text = scanner.format_local_network_report(report)
        assert "192.168.1.0/24" in text
        assert "192.168.1.1" in text
        assert "HTTP" in text
        assert "authorized" in text.lower()

    def test_format_local_network_report_no_live_hosts(self, scanner):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "auto_detected_networks": ["192.168.1.0/24"],
            "networks_scanned": 1,
            "total_hosts_checked": 10,
            "total_live_hosts": 0,
            "port_spec": "top100",
            "networks": [
                {
                    "cidr": "192.168.1.0/24",
                    "hosts_checked": 10,
                    "live_hosts": 0,
                    "elapsed_seconds": 0.5,
                    "hosts": [],
                }
            ],
        }
        text = scanner.format_local_network_report(report)
        assert "No live hosts" in text

    # -- handle local network auto-scan in chat --------------------------------

    def test_handle_network_scanner_query_local_trigger(self):
        from agent.skills.network_scanner import handle_network_scanner_query

        fake_local_report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "auto_detected_networks": ["192.168.1.0/24"],
            "networks_scanned": 1,
            "total_hosts_checked": 254,
            "total_live_hosts": 0,
            "port_spec": "top100",
            "networks": [],
        }

        async def _run():
            # Patch the high-level scan_local_networks method directly
            with patch(
                "agent.skills.network_scanner.NetworkScanner.scan_local_networks",
                return_value=fake_local_report,
            ):
                return await handle_network_scanner_query("scan my local network")

        result = asyncio.run(_run())
        assert result is not None
        assert isinstance(result, str)
        # Should use the local network report format
        assert "Local Network" in result or "192.168.1.0/24" in result


# ---------------------------------------------------------------------------
# HttpInterceptor tests
# ---------------------------------------------------------------------------


class TestHttpInterceptor:
    """Tests for agent/skills/http_interceptor.py"""

    @pytest.fixture
    def interceptor(self):
        from agent.skills.http_interceptor import HttpInterceptor

        return HttpInterceptor(timeout=5.0)

    # -- _validate_url ---------------------------------------------------------

    def test_validate_url_adds_scheme(self):
        from agent.skills.http_interceptor import _validate_url

        url = _validate_url("example.com")
        assert url.startswith("https://")

    def test_validate_url_rejects_non_http(self):
        from agent.skills.http_interceptor import _validate_url

        with pytest.raises(ValueError):
            _validate_url("ftp://example.com")

    def test_validate_url_rejects_no_host(self):
        from agent.skills.http_interceptor import _validate_url

        with pytest.raises(ValueError):
            _validate_url("https://")

    # -- _check_security_headers -----------------------------------------------

    def test_check_security_headers_all_missing(self, interceptor):
        headers = {}
        issues = interceptor._check_security_headers(headers)
        # All 6 expected headers should be flagged
        assert len(issues) == 6

    def test_check_security_headers_all_present(self, interceptor):
        headers = {
            "strict-transport-security": "max-age=31536000; includeSubDomains",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "permissions-policy": "geolocation=()",
        }
        issues = interceptor._check_security_headers(headers)
        assert issues == []

    def test_check_security_headers_weak_hsts(self, interceptor):
        headers = {
            "strict-transport-security": "max-age=3600",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "permissions-policy": "geolocation=()",
        }
        issues = interceptor._check_security_headers(headers)
        weak_hsts = [i for i in issues if i["issue"] == "weak"]
        assert len(weak_hsts) == 1

    # -- _check_cookies --------------------------------------------------------

    def test_check_cookies_insecure(self, interceptor):
        # Simulate headers with an insecure cookie
        headers = {"set-cookie": "session=abc123; Path=/"}
        issues = interceptor._check_cookies(headers)
        issue_types = [i["issue"] for i in issues]
        assert "missing HttpOnly flag" in issue_types
        assert "missing Secure flag" in issue_types
        assert "missing SameSite attribute" in issue_types

    def test_check_cookies_secure(self, interceptor):
        headers = {
            "set-cookie": ("session=abc123; Path=/; HttpOnly; Secure; SameSite=Strict")
        }
        issues = interceptor._check_cookies(headers)
        assert issues == []

    # -- _check_info_disclosure ------------------------------------------------

    def test_check_info_disclosure_server_header(self, interceptor):
        headers = {"server": "Apache/2.4.1"}
        disclosures = interceptor._check_info_disclosure(headers, "")
        assert any("Server" in d["source"] for d in disclosures)

    def test_check_info_disclosure_meta_generator(self, interceptor):
        body = '<meta name="generator" content="WordPress 6.0">'
        disclosures = interceptor._check_info_disclosure({}, body)
        assert any("generator" in d["source"] for d in disclosures)

    # -- _extract_exposed_info -------------------------------------------------

    def test_extract_exposed_info_aws_key(self, interceptor):
        body = "key = AKIAIOSFODNN7EXAMPLE"
        findings = interceptor._extract_exposed_info(body, "https://example.com")
        assert any("AWS key" in f for f in findings)

    def test_extract_exposed_info_private_key(self, interceptor):
        body = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow..."
        findings = interceptor._extract_exposed_info(body, "https://example.com")
        assert any("Private key" in f for f in findings)

    def test_extract_exposed_info_clean(self, interceptor):
        body = "<html><body><p>Hello, world!</p></body></html>"
        findings = interceptor._extract_exposed_info(body, "https://example.com")
        # No emails / keys / JWTs in clean HTML
        assert len(findings) == 0

    # -- inspect_url -----------------------------------------------------------

    def test_inspect_url_without_httpx(self, interceptor):
        with patch("agent.skills.http_interceptor._httpx_available", False):
            result = asyncio.run(interceptor.inspect_url("https://example.com"))
        assert "error" in result

    def test_inspect_url_invalid_url(self, interceptor):
        result = asyncio.run(interceptor.inspect_url("ftp://bad.scheme"))
        assert "error" in result

    def test_inspect_url_timeout(self, interceptor):
        import httpx

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            result = asyncio.run(interceptor.inspect_url("https://example.com"))
        assert "error" in result

    # -- scan_vulnerabilities --------------------------------------------------

    def test_scan_vulnerabilities_without_httpx(self, interceptor):
        with patch("agent.skills.http_interceptor._httpx_available", False):
            result = asyncio.run(
                interceptor.scan_vulnerabilities("https://example.com")
            )
        assert "error" in result

    def test_scan_vulnerabilities_headers_checked(self, interceptor):
        """Scan reports missing security headers as findings."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.text = "<html><body></body></html>"
        mock_resp.history = []
        mock_resp.headers = {}  # No security headers

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)

            result = asyncio.run(
                interceptor.scan_vulnerabilities(
                    "https://example.com",
                    check_sensitive_paths=False,
                    check_sqli=False,
                    check_xss=False,
                )
            )

        assert result["total_findings"] >= 1
        assert any(f["type"] == "missing_security_header" for f in result["findings"])

    # -- crawl -----------------------------------------------------------------

    def test_crawl_without_httpx(self, interceptor):
        with patch("agent.skills.http_interceptor._httpx_available", False):
            result = asyncio.run(interceptor.crawl("https://example.com"))
        assert "error" in result

    def test_crawl_single_page(self, interceptor):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.text = (
            "<html><head><title>Test</title></head>"
            "<body><a href='/about'>About</a></body></html>"
        )
        mock_resp.history = []
        mock_resp.headers = {}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)

            result = asyncio.run(interceptor.crawl("https://example.com", max_pages=1))

        assert result["pages_visited"] >= 1

    # -- format helpers --------------------------------------------------------

    def test_format_inspection_report_error(self, interceptor):
        report = {"error": "connection refused"}
        text = interceptor.format_inspection_report(report)
        assert "error" in text.lower() or "⚠️" in text

    def test_format_inspection_report_ok(self, interceptor):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "url": "https://example.com",
            "status_code": 200,
            "final_url": "https://example.com",
            "redirect_chain": [],
            "security_headers": [
                {
                    "header": "Content-Security-Policy",
                    "issue": "missing",
                    "description": "Missing CSP",
                }
            ],
            "cookie_issues": [],
            "info_disclosure": [],
            "exposed_info": [],
            "forms": [],
            "links_found": 5,
        }
        text = interceptor.format_inspection_report(report)
        assert "Content-Security-Policy" in text
        assert "authorized" in text.lower()

    def test_format_vuln_scan_report_no_findings(self, interceptor):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "target": "https://example.com",
            "requests_made": 3,
            "total_findings": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "findings": [],
        }
        text = interceptor.format_vuln_scan_report(report)
        assert "No vulnerabilities" in text

    def test_format_vuln_scan_report_with_findings(self, interceptor):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "target": "https://example.com",
            "requests_made": 10,
            "total_findings": 1,
            "critical": 1,
            "high": 0,
            "medium": 0,
            "low": 0,
            "findings": [
                {
                    "type": "sql_injection",
                    "severity": "critical",
                    "url": "https://example.com/login",
                    "detail": "SQL error detected",
                }
            ],
        }
        text = interceptor.format_vuln_scan_report(report)
        assert "sql_injection" in text
        assert "CRITICAL" in text

    def test_format_crawl_report_ok(self, interceptor):
        report = {
            "timestamp": "2024-01-01T00:00:00Z",
            "start_url": "https://example.com",
            "pages_visited": 2,
            "total_forms": 1,
            "pages": [
                {
                    "url": "https://example.com",
                    "status": 200,
                    "title": "Home",
                    "forms": 1,
                    "links": 3,
                },
                {
                    "url": "https://example.com/about",
                    "status": 200,
                    "title": "About",
                    "forms": 0,
                    "links": 1,
                },
            ],
            "forms": [
                {"action": "https://example.com/login", "method": "post", "inputs": []}
            ],
        }
        text = interceptor.format_crawl_report(report)
        assert "example.com" in text
        assert "Home" in text

    # -- is_http_interceptor_query / handle_http_interceptor_query ------------

    def test_is_http_interceptor_query_positive(self):
        from agent.skills.http_interceptor import is_http_interceptor_query

        assert is_http_interceptor_query("scan website example.com for vulnerabilities")
        assert is_http_interceptor_query(
            "check security headers on https://example.com"
        )
        assert is_http_interceptor_query("test for xss on example.com")
        assert is_http_interceptor_query("burp suite equivalent check")
        assert is_http_interceptor_query("inspect http traffic on example.com")
        assert is_http_interceptor_query("web security scan of example.com")
        assert is_http_interceptor_query("website security scan")

    def test_is_http_interceptor_query_negative(self):
        from agent.skills.http_interceptor import is_http_interceptor_query

        assert not is_http_interceptor_query("what is the capital of France?")
        assert not is_http_interceptor_query("scan ports on 192.168.1.1")
        # "security scan" alone (without web context) should NOT match
        assert not is_http_interceptor_query("do a security scan")

    def test_handle_http_interceptor_query_no_url(self):
        from agent.skills.http_interceptor import handle_http_interceptor_query

        result = asyncio.run(
            handle_http_interceptor_query("scan website for vulnerabilities")
        )
        assert result is not None
        assert "url" in result.lower() or "domain" in result.lower()

    def test_handle_http_interceptor_query_not_matching(self):
        from agent.skills.http_interceptor import handle_http_interceptor_query

        result = asyncio.run(handle_http_interceptor_query("what is the weather?"))
        assert result is None

    def test_extract_url_from_message_https(self):
        from agent.skills.http_interceptor import _extract_url_from_message

        url = _extract_url_from_message("inspect https://example.com/path")
        assert url == "https://example.com/path"

    def test_extract_url_from_message_domain(self):
        from agent.skills.http_interceptor import _extract_url_from_message

        url = _extract_url_from_message("check security headers on example.com")
        assert url is not None
        assert "example.com" in url

    def test_extract_url_from_message_none(self):
        from agent.skills.http_interceptor import _extract_url_from_message

        url = _extract_url_from_message("scan something")
        assert url is None

    # -- _extract_links --------------------------------------------------------

    def test_extract_links_same_origin(self):
        from agent.skills.http_interceptor import _extract_links

        html = """
        <html><body>
        <a href="/page1">P1</a>
        <a href="https://example.com/page2">P2</a>
        <a href="https://other.com/page3">P3</a>
        <a href="mailto:test@example.com">Email</a>
        </body></html>
        """
        links = _extract_links("https://example.com", html)
        assert "https://example.com/page1" in links
        assert "https://example.com/page2" in links
        # Cross-origin and mailto should be excluded
        assert "https://other.com/page3" not in links
        assert not any("mailto:" in lnk for lnk in links)

    # -- _extract_forms --------------------------------------------------------

    def test_extract_forms(self):
        from agent.skills.http_interceptor import _extract_forms

        html = """
        <form action="/login" method="post">
          <input name="username" type="text"/>
          <input name="password" type="password"/>
          <input type="submit" value="Login"/>
        </form>
        """
        forms = _extract_forms("https://example.com", html)
        assert len(forms) == 1
        assert forms[0]["method"] == "post"
        names = [i["name"] for i in forms[0]["inputs"]]
        assert "username" in names
        assert "password" in names
