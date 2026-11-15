"""Small, context-aware French surface touches; no inference or factual rewriting."""

import re

FRENCH_TOUCH = re.compile(
    r"\b(?:bonjour|bonsoir|salut|merci|voilà|d['’]accord|bien\s+sûr|"
    r"avec\s+plaisir|je\s+comprends|c['’]est|très|ça|bon\s+courage|"
    r"bon\s+repos|bon\s+appétit|en\s+effet|mon\s+dieu|quelle|magnifique|"
    r"charmant|parfait|monsieur|madame|mon\s+ami)\b",
    re.I,
)
LANGUAGE_OR_EXACT_REQUEST = re.compile(
    r"\b(?:english[ -]only|only (?:in )?english|no french|without french|"
    r"verbatim|exact(?:ly)?|only|translate|translation|quote|quotation|"
    r"draft|write|rewrite|rephrase|proofread|format|json|code|csv|xml|yaml)\b",
    re.I,
)


def recent_assistant_messages(history) -> list[str]:
    messages = []
    for row in history or ():
        if isinstance(row, dict):
            if "assistant" in row:
                role, content = "assistant", row.get("assistant")
            else:
                role, content = row.get("role"), row.get("content")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            role, content = row[0], row[1]
        else:
            continue
        if str(role).casefold() == "assistant":
            messages.append(str(content or ""))
    return messages[-3:]


def french_touches(text: str) -> set[str]:
    return {m.group().casefold().replace("’", "'") for m in FRENCH_TOUCH.finditer(text)}


def repeats_french(text: str, history=None) -> bool:
    """Avoid adjacent touches and the same expression across three replies.

    An older, different expression must not silence French for three turns.
    History belongs to the current conversation, never global process state.
    """
    recent = recent_assistant_messages(history)
    return bool(
        recent
        and (
            french_touches(recent[-1])
            or french_touches(text) & french_touches(" ".join(recent))
        )
    )


def constrain_french_opening(
    response: str, user_text: str, persona: dict, history=None
) -> str:
    """Enforce language/repetition rules on known greetings, not arbitrary French.

    A model can ignore the turn guidance. Translate only a recognized standalone
    speech act, preserving all following content and avoiding another model call.
    """
    config = persona.get("language_profile", {}).get("french_integration", {})
    if (
        str(persona.get("name", "")).casefold() != "curie"
        or not config.get("contextual_surface_fallback")
        or not config.get("enabled")
    ):
        return response
    english_only = bool(
        re.search(
            r"\b(?:english[ -]only|only (?:in )?english|no french|without french)\b",
            user_text,
            re.I,
        )
    )
    explicit_french = bool(
        re.search(
            r"\b(?:speak|say|write|reply|respond|translate)\b.{0,30}\bfrench\b",
            user_text,
            re.I,
        )
    )
    if not explicit_french and re.fullmatch(
        r"\s*(?:(?:thanks|thank you|merci)[,.! ]+)?(?:good\s*night|night)[.! ]*",
        user_text,
        re.I,
    ):
        # Do not turn a farewell into "Hello. Sleep well." when suppressing
        # repeated French. Keep the actual farewell and its personal callback.
        response = re.sub(
            r"^(?:bonjour|bonsoir|salut)[.!]\s+(?=\S)", "", response, flags=re.I
        )
    from agent.personality_adapter import PersonalityAdapter

    context = PersonalityAdapter().infer_context(user_text)
    restrained = (
        context["mode"] in {"urgent", "professional"}
        or context["user_emotion"] == "distress"
    )
    if not english_only and (
        explicit_french or (not restrained and not repeats_french(response, history))
    ):
        return response
    openings = (
        (r"bonjour|salut", "Hello"),
        (r"bonsoir", "Good evening"),
        (r"d['’]accord", "All right"),
        (r"avec plaisir", "My pleasure"),
        (r"merci", "Thanks"),
        (r"je comprends", "I understand"),
        (r"c['’]est intéressant", "That's interesting"),
        (r"voilà", "There we go"),
        (r"bien sûr", "Of course"),
    )
    for source, replacement in openings:
        match = re.match(r"^(?:" + source + r")([.!])(?=\s|$)", response, re.I)
        if match:
            if (
                restrained
                and source in {r"bonjour|salut", r"bonsoir"}
                and response[match.end() :].strip()
            ):
                return response[match.end() :].lstrip()
            return replacement + match.group(1) + response[match.end() :]
    return response


def contextual_touch(response: str, user_text: str, persona: dict, history=None) -> str:
    """Replace only an existing standalone social phrase, never prepend filler.

    The answer following that phrase is retained byte-for-byte. Anything not
    confidently classified is left to the original model's voice.
    """
    config = persona.get("language_profile", {}).get("french_integration", {})
    if (
        str(persona.get("name", "")).casefold() != "curie"
        or not config.get("enabled")
        or not config.get("contextual_surface_fallback")
        or FRENCH_TOUCH.search(response)
        or LANGUAGE_OR_EXACT_REQUEST.search(user_text)
        or re.search(
            r"\b(?:reply|answer|respond|speak)\b.{0,30}\bin\b", user_text, re.I
        )
        or any(char in response for char in ("`", "\n", "{", "}", "[", "]", '"', "*"))
        or re.search(r"https?://", response)
    ):
        return response

    from agent.personality_adapter import PersonalityAdapter

    context = PersonalityAdapter().infer_context(user_text)
    if (
        context["mode"] != "casual"
        or context["user_emotion"] in {"distress", "technical"}
        or context["interaction_kind"] in {"command", "correction"}
        or re.search(
            r"\b(?:medical|health|legal|financial|injury|danger|suicid\w*|"
            r"death|died|chest pain|bleeding|overdose)\b",
            user_text + " " + response,
            re.I,
        )
    ):
        return response

    # These are equivalent speech acts, not random decorations or inferred facts.
    substitutions = [
        (r"(?:You're welcome|You’re welcome|My pleasure|Anytime)", "Avec plaisir"),
        (r"(?:Understood|Got it|All right)", "D’accord"),
        (r"(?:There we go|There it is)", "Voilà"),
        (r"(?:Interesting|That's interesting|That’s interesting)", "C’est intéressant"),
    ]
    if re.fullmatch(
        r"(?:hi|hello|hey|bonjour|good morning)(?:[, ]+curie)?[.!? ]*",
        user_text.strip(),
        re.I,
    ):
        substitutions.insert(0, (r"(?:Hi|Hello|Hey|Good morning|Morning)", "Bonjour"))
    for source, replacement in substitutions:
        match = re.match(r"^(?:" + source + r")([.!])(?=\s|$)", response, re.I)
        if match and not repeats_french(replacement, history):
            return replacement + match.group(1) + response[match.end() :]
    return response
