# agent/skills/trip_planner.py
"""
Trip & Vacation Planning skill.

Provides LLM-backed trip planning with:
- Multi-day itinerary generation
- Packing list suggestions
- Budget estimates
- Travel tips for destinations
- Integration with the navigation skill for route information

Natural-language triggers
--------------------------
  "plan a trip to Paris for 5 days"
  "I want to visit Tokyo next month for 2 weeks"
  "help me plan a vacation to Bali"
  "what should I pack for a beach trip?"
  "give me a travel itinerary for Rome"
  "budget trip to Barcelona for 3 days"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_TRIP_KEYWORDS = re.compile(
    r"\b(plan|planning|trip|vacation|holiday|travel|visit|itinerary|journey|tour|"
    r"getaway|weekend away|road trip|backpack|bucket list)\b",
    re.IGNORECASE,
)

# Maximum character length of a destination name to match (prevents overly greedy captures)
_MAX_DESTINATION_CHARS = 40

_DESTINATION_PATTERN = re.compile(
    r"\b(?:to|in|for|visit|visiting)\s+([A-Za-z][a-zA-Z\s,]{2," + str(_MAX_DESTINATION_CHARS) + r"}?)(?=\s+for|\s+in|\s*[,.]|$)",
    re.IGNORECASE,
)

_DURATION_PATTERN = re.compile(
    r"\b(?P<amount>\d+)\s*(?P<unit>day|days|night|nights|week|weeks)\b",
    re.IGNORECASE,
)

_BUDGET_KEYWORDS = re.compile(
    r"\b(budget|cheap|affordable|luxury|expensive|splurge|backpacker|backpacking)\b",
    re.IGNORECASE,
)

_PACKING_KEYWORDS = re.compile(
    r"\b(pack|packing|bring|take with|what to bring|luggage|suitcase|bag|checklist)\b",
    re.IGNORECASE,
)


def is_trip_query(text: str) -> bool:
    """Return True if the text looks like a trip/travel planning intent."""
    return bool(_TRIP_KEYWORDS.search(text))


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------

def extract_trip_params(text: str) -> dict:
    """
    Extract trip parameters from natural-language text.
    Returns a dict with keys: destination, duration_days, budget_tier, packing_focus.
    """
    params: dict = {
        "destination": None,
        "duration_days": None,
        "budget_tier": "moderate",
        "packing_focus": False,
        "raw_text": text,
    }

    # Destination
    dest_match = _DESTINATION_PATTERN.search(text)
    if dest_match:
        params["destination"] = dest_match.group(1).strip().rstrip(",.")

    # Duration
    dur_match = _DURATION_PATTERN.search(text)
    if dur_match:
        amount = int(dur_match.group("amount"))
        unit = dur_match.group("unit").lower()
        if unit in ("week", "weeks"):
            amount *= 7
        params["duration_days"] = amount

    # Budget tier
    budget_match = _BUDGET_KEYWORDS.search(text)
    if budget_match:
        word = budget_match.group(1).lower()
        if word in ("budget", "cheap", "affordable", "backpacker", "backpacking"):
            params["budget_tier"] = "budget"
        elif word in ("luxury", "expensive", "splurge"):
            params["budget_tier"] = "luxury"

    # Packing focus
    if _PACKING_KEYWORDS.search(text):
        params["packing_focus"] = True

    return params


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_ITINERARY_PROMPT = """\
You are an experienced travel planner. Create a practical, day-by-day itinerary for the trip described below.

Trip details:
- Destination: {destination}
- Duration: {duration}
- Budget tier: {budget_tier}

Please include:
1. A brief introduction about the destination (2–3 sentences).
2. A day-by-day itinerary with morning, afternoon, and evening activities.
3. Top 3 must-try local foods or restaurants.
4. 3–5 practical travel tips (transport, safety, cultural etiquette).
5. Rough daily budget estimate in USD for the {budget_tier} tier.

Keep the tone friendly and conversational, like advice from a well-travelled friend.
Do not include disclaimers or meta-commentary. Just give the plan.
"""

_PACKING_PROMPT = """\
You are a helpful travel advisor. Create a practical packing list for:
- Destination / trip type: {destination}
- Duration: {duration}
- Budget tier: {budget_tier}

Organise the list into categories:
1. Clothing & footwear
2. Electronics & accessories
3. Toiletries & health
4. Documents & money
5. Miscellaneous / nice-to-haves

Keep it concise and practical. No fluff or disclaimers.
"""

_GENERAL_TRAVEL_PROMPT = """\
You are a knowledgeable travel assistant. Answer the following travel-related question clearly and helpfully, as a well-travelled friend would:

{question}

Be concise, practical, and specific. No disclaimers.
"""


# ---------------------------------------------------------------------------
# Response formatting helpers
# ---------------------------------------------------------------------------

_UNKNOWN_DESTINATION = "your chosen destination"
_UNKNOWN_DURATION = "your trip"


def _build_itinerary_prompt(params: dict) -> str:
    destination = params.get("destination") or _UNKNOWN_DESTINATION
    days = params.get("duration_days")
    duration = f"{days} day{'s' if days != 1 else ''}" if days else _UNKNOWN_DURATION
    budget_tier = params.get("budget_tier", "moderate")
    return _ITINERARY_PROMPT.format(
        destination=destination,
        duration=duration,
        budget_tier=budget_tier,
    )


def _build_packing_prompt(params: dict) -> str:
    destination = params.get("destination") or "general travel"
    days = params.get("duration_days")
    duration = f"{days} day{'s' if days != 1 else ''}" if days else "a trip"
    budget_tier = params.get("budget_tier", "moderate")
    return _PACKING_PROMPT.format(
        destination=destination,
        duration=duration,
        budget_tier=budget_tier,
    )


# ---------------------------------------------------------------------------
# Main skill handler
# ---------------------------------------------------------------------------

async def handle_trip_query(
    text: str,
    internal_id: str = "unknown",
) -> Optional[str]:
    """
    Handle a trip/vacation planning query.
    Returns a formatted response string, or None if not a trip query.
    """
    if not is_trip_query(text):
        return None

    params = extract_trip_params(text)

    # Choose prompt type
    if params["packing_focus"]:
        prompt = _build_packing_prompt(params)
    elif params["destination"] or params["duration_days"]:
        prompt = _build_itinerary_prompt(params)
    else:
        # General travel question — pass through as-is
        prompt = _GENERAL_TRAVEL_PROMPT.format(question=text)

    # Use multi-provider LLM (prefers cloud if available, falls back to local)
    response: Optional[str] = None
    try:
        from llm.providers import ask_best_provider  # noqa: PLC0415
        response = ask_best_provider(prompt, temperature=0.75, max_tokens=1024)
    except Exception as exc:
        logger.warning("providers.ask_best_provider failed: %s", exc)

    # Fallback to local manager
    if response is None:
        try:
            from llm import manager as llm_manager  # noqa: PLC0415
            response = llm_manager.ask_llm(prompt, temperature=0.75, max_tokens=1024)
        except Exception as exc:
            logger.error("Local LLM also failed for trip query: %s", exc)
            return (
                "Sorry, I couldn't generate a trip plan right now. "
                "Please try again in a moment! ✈️"
            )

    if not response or response.startswith("[Error"):
        return (
            "Sorry, I couldn't generate a trip plan right now. "
            "Please try again in a moment! ✈️"
        )

    # Prepend a friendly header if destination was detected
    destination = params.get("destination")
    if destination and not params["packing_focus"]:
        header = f"✈️ **Trip Plan: {destination}**\n\n"
        response = header + response
    elif params["packing_focus"]:
        dest_label = destination or "your trip"
        response = f"🧳 **Packing List for {dest_label}**\n\n" + response

    return response
