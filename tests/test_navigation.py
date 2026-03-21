#!/usr/bin/env python3
"""
Tests for the Navigation & Traffic skill and utility functions.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.navigation import (  # noqa: E402
    format_duration,
    format_distance,
    extract_steps,
    generate_map_links,
)
from agent.skills.navigation import (  # noqa: E402
    is_navigation_query,
    extract_navigation_params,
    handle_navigation_query,
    _extract_mode,
    _clean_location,
)

# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45) == "45 sec"

    def test_minutes(self):
        assert format_duration(90) == "1 min"
        assert format_duration(3599) == "59 min"

    def test_hours_and_minutes(self):
        assert format_duration(3660) == "1 hr 1 min"
        assert format_duration(7200) == "2 hr"

    def test_zero(self):
        assert format_duration(0) == "0 sec"


# ---------------------------------------------------------------------------
# format_distance
# ---------------------------------------------------------------------------


class TestFormatDistance:
    def test_meters(self):
        assert format_distance(500) == "500 m"
        assert format_distance(999) == "999 m"

    def test_km_decimal(self):
        result = format_distance(1500)
        assert "1.5 km" == result

    def test_km_integer(self):
        result = format_distance(42000)
        assert "42 km" == result


# ---------------------------------------------------------------------------
# extract_steps
# ---------------------------------------------------------------------------


class TestExtractSteps:
    def _make_route(self, steps_data):
        return {"legs": [{"steps": steps_data}]}

    def test_depart_step(self):
        route = self._make_route(
            [
                {
                    "maneuver": {"type": "depart", "modifier": "north"},
                    "name": "Main St",
                    "distance": 500,
                },
            ]
        )
        steps = extract_steps(route)
        assert len(steps) == 1
        assert "Main St" in steps[0]
        assert "500 m" in steps[0]

    def test_arrive_step(self):
        route = self._make_route(
            [
                {"maneuver": {"type": "arrive"}, "name": "", "distance": 0},
            ]
        )
        steps = extract_steps(route)
        assert steps == ["Arrive at destination"]

    def test_turn_step(self):
        route = self._make_route(
            [
                {
                    "maneuver": {"type": "turn", "modifier": "right"},
                    "name": "Oak Ave",
                    "distance": 1200,
                },
            ]
        )
        steps = extract_steps(route)
        assert len(steps) == 1
        assert "right" in steps[0].lower()
        assert "Oak Ave" in steps[0]

    def test_max_steps_limit(self):
        many_steps = [
            {
                "maneuver": {"type": "turn", "modifier": "left"},
                "name": f"St {i}",
                "distance": 100,
            }
            for i in range(20)
        ]
        route = self._make_route(many_steps)
        steps = extract_steps(route, max_steps=5)
        assert len(steps) == 5


# ---------------------------------------------------------------------------
# is_navigation_query
# ---------------------------------------------------------------------------


class TestIsNavigationQuery:
    def test_positive_route_from_to(self):
        assert is_navigation_query("route from New York to Boston") is True

    def test_positive_directions(self):
        assert is_navigation_query("directions to the airport") is True

    def test_positive_how_do_i_get(self):
        assert is_navigation_query("how do I get from home to work?") is True

    def test_positive_traffic(self):
        assert is_navigation_query("what's the traffic on I-95?") is True

    def test_positive_travel_time(self):
        assert is_navigation_query("travel time from London to Paris") is True

    def test_positive_navigate(self):
        assert is_navigation_query("navigate to Central Park") is True

    def test_negative_weather(self):
        assert is_navigation_query("what's the weather today?") is False

    def test_negative_greeting(self):
        assert is_navigation_query("hello, how are you?") is False

    def test_negative_conversion(self):
        assert is_navigation_query("convert 100 USD to EUR") is False


# ---------------------------------------------------------------------------
# _extract_mode
# ---------------------------------------------------------------------------


class TestExtractMode:
    def test_default_drive(self):
        assert _extract_mode("route from A to B") == "drive"

    def test_walk(self):
        assert _extract_mode("walking directions to the park") == "walk"

    def test_bike(self):
        assert _extract_mode("cycling route to downtown") == "bike"

    def test_transit(self):
        assert _extract_mode("public transit from A to B") == "transit"

    def test_car_keyword(self):
        assert _extract_mode("car route to the office") == "drive"


# ---------------------------------------------------------------------------
# extract_navigation_params
# ---------------------------------------------------------------------------


class TestExtractNavigationParams:
    def test_from_to_pattern(self):
        params = extract_navigation_params("route from New York to Boston")
        assert params is not None
        assert "new york" in params["origin"].lower()
        assert "boston" in params["destination"].lower()
        assert params["mode"] == "drive"

    def test_from_to_with_walk_mode(self):
        params = extract_navigation_params(
            "walking directions from the hotel to the museum"
        )
        assert params is not None
        assert params["mode"] == "walk"

    def test_directions_to_without_origin(self):
        # When no starting point is given, the skill returns None so the LLM
        # can prompt the user to specify their origin.
        params = extract_navigation_params("directions to the Eiffel Tower")
        assert params is None

    def test_how_do_i_get_pattern(self):
        # Same: no origin provided → return None (LLM handles clarification)
        params = extract_navigation_params("how do I get to the train station")
        assert params is None

    def test_how_do_i_get_with_explicit_origin(self):
        params = extract_navigation_params(
            "how do I get to the train station from downtown"
        )
        assert params is not None
        assert "train station" in params["destination"].lower()
        assert "downtown" in params["origin"].lower()

    def test_traffic_only_pattern(self):
        params = extract_navigation_params("traffic on Highway 101")
        assert params is not None
        assert params.get("traffic_only") is True
        assert "highway 101" in params["origin"].lower()

    def test_returns_none_for_non_navigation(self):
        params = extract_navigation_params("convert 100 USD to EUR")
        assert params is None

    def test_bike_mode_extraction(self):
        params = extract_navigation_params(
            "bike route from Central Park to Brooklyn Bridge"
        )
        assert params is not None
        assert params["mode"] == "bike"


# ---------------------------------------------------------------------------
# _clean_location
# ---------------------------------------------------------------------------


class TestCleanLocation:
    def test_removes_trailing_please(self):
        assert _clean_location("Central Park please") == "Central Park"

    def test_removes_trailing_punctuation(self):
        assert _clean_location("London?") == "London"

    def test_no_change_on_clean_input(self):
        assert _clean_location("New York") == "New York"


# ---------------------------------------------------------------------------
# generate_map_links
# ---------------------------------------------------------------------------

_ORIGIN_COORDS = {"lat": 40.7128, "lon": -74.0060}
_DEST_COORDS = {"lat": 42.3601, "lon": -71.0589}


class TestGenerateMapLinks:
    def _links(self, mode="drive"):
        return generate_map_links(
            "New York", "Boston", _ORIGIN_COORDS, _DEST_COORDS, mode=mode
        )

    # --- Provider presence ---

    def test_google_maps_present(self):
        assert "Google Maps" in self._links()

    def test_apple_maps_present(self):
        assert "Apple Maps" in self._links()

    def test_bing_maps_present(self):
        assert "Bing Maps" in self._links()

    def test_openstreetmap_present(self):
        assert "OpenStreetMap" in self._links()

    def test_here_maps_present(self):
        assert "HERE Maps" in self._links()

    def test_naver_maps_present(self):
        assert "Naver Maps" in self._links()

    def test_kakao_maps_present(self):
        assert "Kakao Maps" in self._links()

    # --- Waze only for drive/transit ---

    def test_waze_present_for_drive(self):
        assert "Waze" in self._links(mode="drive")

    def test_waze_present_for_transit(self):
        assert "Waze" in self._links(mode="transit")

    def test_waze_absent_for_walk(self):
        assert "Waze" not in self._links(mode="walk")

    def test_waze_absent_for_bike(self):
        assert "Waze" not in self._links(mode="bike")

    # --- Mode mapping: Google Maps ---

    def test_google_driving_mode(self):
        url = self._links(mode="drive")["Google Maps"]
        assert "travelmode=driving" in url

    def test_google_walking_mode(self):
        url = self._links(mode="walk")["Google Maps"]
        assert "travelmode=walking" in url

    def test_google_bicycling_mode(self):
        url = self._links(mode="bike")["Google Maps"]
        assert "travelmode=bicycling" in url

    def test_google_transit_mode(self):
        url = self._links(mode="transit")["Google Maps"]
        assert "travelmode=transit" in url

    # --- Mode mapping: Apple Maps ---

    def test_apple_driving_flag(self):
        url = self._links(mode="drive")["Apple Maps"]
        assert "dirflg=d" in url

    def test_apple_walking_flag(self):
        url = self._links(mode="walk")["Apple Maps"]
        assert "dirflg=w" in url

    def test_apple_cycling_flag(self):
        url = self._links(mode="bike")["Apple Maps"]
        assert "dirflg=b" in url

    def test_apple_transit_flag(self):
        url = self._links(mode="transit")["Apple Maps"]
        assert "dirflg=r" in url

    # --- Coordinates in URLs ---

    def test_google_contains_origin_coords(self):
        url = self._links()["Google Maps"]
        assert "40.7128" in url and "-74.006" in url

    def test_google_contains_dest_coords(self):
        url = self._links()["Google Maps"]
        assert "42.3601" in url and "-71.0589" in url

    def test_openstreetmap_contains_coords(self):
        url = self._links()["OpenStreetMap"]
        assert "40.7128" in url and "42.3601" in url

    # --- Name encoding in HERE / Naver / Kakao ---

    def test_here_contains_names(self):
        url = self._links()["HERE Maps"]
        assert "New+York" in url

    def test_kakao_contains_destination(self):
        url = self._links()["Kakao Maps"]
        assert "boston" in url.lower()

    # --- All values are https URLs ---

    def test_all_links_are_https(self):
        for name, url in self._links().items():
            assert url.startswith("https://"), f"{name} URL is not HTTPS: {url}"


# ---------------------------------------------------------------------------
# handle_navigation_query (integration with mocked route())
# ---------------------------------------------------------------------------

MOCK_ROUTE_RESULT = {
    "origin_name": "New York",
    "destination_name": "Boston",
    "mode_label": "🚗 Driving",
    "routes": [
        {
            "distance_m": 346000,
            "duration_s": 13200,
            "steps": [
                "Head north on 5th Ave for 1.2 km",
                "Turn right onto I-95 (340 km)",
                "Arrive at destination",
            ],
        },
        {
            "distance_m": 360000,
            "duration_s": 14400,
            "steps": [],
        },
    ],
    "traffic": None,
    "map_links": {
        "Google Maps": (
            "https://www.google.com/maps/dir/?api=1"
            "&origin=40.7128,-74.006&destination=42.3601,-71.0589&travelmode=driving"
        ),
        "Apple Maps": (
            "https://maps.apple.com/?saddr=40.7128,-74.006&daddr=42.3601,-71.0589&dirflg=d"
        ),
        "Waze": "https://waze.com/ul?ll=42.3601,-71.0589&navigate=yes",
        "Bing Maps": (
            "https://bing.com/maps/default.aspx"
            "?rtp=pos.40.7128_-74.006~pos.42.3601_-71.0589&mode=D"
        ),
        "OpenStreetMap": (
            "https://www.openstreetmap.org/directions"
            "?from=40.7128,-74.006&to=42.3601,-71.0589"
        ),
        "HERE Maps": (
            "https://share.here.com/r/40.7128,-74.006,New+York"
            "/42.3601,-71.0589,Boston"
        ),
        "Naver Maps": (
            "https://map.naver.com/v5/directions/-/"
            "loc:-74.006,40.7128,New+York/-/loc:-71.0589,42.3601,Boston/-/car"
        ),
        "Kakao Maps": "https://map.kakao.com/link/to/Boston,42.3601,-71.0589",
    },
}


class TestHandleNavigationQuery:
    @pytest.mark.asyncio
    async def test_returns_none_for_non_nav_query(self):
        result = await handle_navigation_query("what is the weather today?")
        assert result is None

    @pytest.mark.asyncio
    async def test_route_response_with_mock(self):
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(return_value=MOCK_ROUTE_RESULT),
        ):
            result = await handle_navigation_query("route from New York to Boston")
            assert result is not None
            assert "New York" in result
            assert "Boston" in result
            assert "Driving" in result
            assert "km" in result

    @pytest.mark.asyncio
    async def test_includes_directions(self):
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(return_value=MOCK_ROUTE_RESULT),
        ):
            result = await handle_navigation_query("directions from New York to Boston")
            assert result is not None
            assert "Directions" in result or "I-95" in result

    @pytest.mark.asyncio
    async def test_includes_alternative_routes(self):
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(return_value=MOCK_ROUTE_RESULT),
        ):
            result = await handle_navigation_query("route from New York to Boston")
            assert result is not None
            # Should mention alternative route
            assert "Alternative" in result or "Route 2" in result

    @pytest.mark.asyncio
    async def test_error_result_shows_error_message(self):
        error_result = {"error": "Could not find location: XYZ123"}
        with patch(
            "agent.skills.navigation.route", new=AsyncMock(return_value=error_result)
        ):
            result = await handle_navigation_query("route from XYZ123 to Boston")
            assert result is not None
            assert "❌" in result or "Error" in result

    @pytest.mark.asyncio
    async def test_traffic_info_shown_when_available(self):
        result_with_traffic = {
            **MOCK_ROUTE_RESULT,
            "traffic": {
                "current_speed_kmh": 40,
                "free_flow_speed_kmh": 100,
                "current_travel_time_s": 600,
                "free_flow_travel_time_s": 250,
                "confidence": 0.9,
            },
        }
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(return_value=result_with_traffic),
        ):
            with patch("agent.skills.navigation.TOMTOM_API_KEY", "dummy_key"):
                result = await handle_navigation_query("route from New York to Boston")
                assert result is not None
                assert "Traffic" in result or "traffic" in result

    @pytest.mark.asyncio
    async def test_map_links_section_in_response(self):
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(return_value=MOCK_ROUTE_RESULT),
        ):
            result = await handle_navigation_query("route from New York to Boston")
            assert result is not None
            assert "Open in Maps" in result
            assert "Google Maps" in result
            assert "Apple Maps" in result

    @pytest.mark.asyncio
    async def test_exception_returns_error_message(self):
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(side_effect=Exception("timeout")),
        ):
            result = await handle_navigation_query("route from New York to Boston")
            assert result is not None
            assert "error" in result.lower() or "❌" in result

    @pytest.mark.asyncio
    async def test_traffic_only_query_without_api_key(self):
        # Without TomTom key, should return friendly message
        with patch(
            "agent.skills.navigation.route",
            new=AsyncMock(
                return_value={
                    "origin_name": "Highway 101",
                    "destination_name": "Highway 101",
                    "mode_label": "🚗 Driving",
                    "routes": [{"distance_m": 0, "duration_s": 0, "steps": []}],
                    "traffic": None,
                }
            ),
        ):
            with patch("agent.skills.navigation.TOMTOM_API_KEY", ""):
                result = await handle_navigation_query("traffic on Highway 101")
                # Should still return something (either route info or traffic unavailable msg)
                assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
