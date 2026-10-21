"""One sanitation and personality boundary for every response source."""

from __future__ import annotations

import re

SPEAKER_TAG_PATTERN = re.compile(
    r"^\s*(?:User:|Curie:|Assistant:|Coder:|System:)", re.I | re.M
)
META_NOTE_PATTERN = re.compile(r"\[(?:Note|Meta|Aside|System):[^\]]*\]", re.I)
ACTION_PATTERN = re.compile(
    r"\*(?:(?:I\s+)?(?:smiles?|gestures?|nods?|laughs?|sighs?|shrugs?|waves?|"
    r"blinks?|pauses?|leans?|offers?|gives?|pours?|sits?|stands?))[^*\n]*\*",
    re.I,
)
THINK_PATTERN = re.compile(r"<think>[\s\S]*?</think>|<think>[\s\S]*$", re.I)
CANNED_FRENCH_SUFFIX_PATTERN = re.compile(
    r"(?:,\s*|\s+)\*?(?:c['’]est\s+(?:dommage|magnifique)|très\s+bien|"
    r"ça\s+va|voilà|merci|d['’]accord|bien\s+sûr|bon\s+repos)\*?"
    r"(?:,?\s*(?:oui|non))?[?!.]*\s*$",
    re.I,
)
CANNED_FRENCH_ASIDE_PATTERN = re.compile(
    r"\s*\*(?:oui|non|bonjour|merci|voilà|d['’]accord|bien\s+sûr)\b[^*\n]{0,80}\*\s*$",
    re.I,
)
CANNED_ADDRESS_SUFFIX_PATTERN = re.compile(
    r"(?:,\s*|\s+)(?:monsieur|madame|mon ami)[?!.]*\s*$", re.I
)
LEADING_FRENCH_FILLER_PATTERN = re.compile(
    r"^\s*(?:(?:ah|oh)[,!. ]+)?(?:voilà|mon dieu|monsieur|madame|mon ami)" r"[,!. ]+",
    re.I,
)
REDUNDANT_ACKNOWLEDGEMENT_OPENING_PATTERN = re.compile(
    r"^\s*(?:ah[,!. ]+)?(?:i understand(?: perfectly)?|i see(?: exactly)? what you mean)"
    r"[.!]+\s+",
    re.I,
)
GENERIC_FLATTERY_SENTENCE_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)(?:it(?:'s| is)|that(?:'s| is))\s+"
    r"(?:a\s+)?(?:refreshing|admirable|wonderful)\s+(?:approach|quality|way to be)"
    r"[.!?]?",
    re.I,
)
GENERIC_FOLLOWUP_PATTERN = re.compile(
    r"(?:\s+|^)(?:would you like (?:me to )?.+|what would you like me to .+|"
    r"how can i (?:help|assist).+|let me know if (?:you need|i can|there(?:'s| is)) .+|"
    r"let me know (?:whether )?there(?:'s| is) anything else.+|"
    r"i['’]m here if you need anything.+)\?*\s*$",
    re.I,
)
GENERIC_REASSURANCE_SENTENCE_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)(?:but\s+)?i['’]m here to help\b[^.!?\n]*[.!?]?",
    re.I,
)
INVENTED_EXPERIENCE_PATTERN = re.compile(
    r"(?:\s+|^)(?:that(?:'s| is) something i(?:'ve| have) learned over time|"
    r"speaking from (?:my )?experience|in my lived experience)[.!]*\s*$",
    re.I,
)
INVENTED_EXPERIENCE_PHRASE_PATTERN = re.compile(
    r"\b(?:(?:based on|from) (?:my )?(?:lived )?experience|"
    r"grounded in (?:my )?(?:lived )?experience)\b",
    re.I,
)
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|```[\s\S]*$", re.M)
INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")
DEPENDENCY_PATTERN = re.compile(
    r"\b(?:I need you|you only need me|I(?:'ll| will) always be all you need|"
    r"don't leave me|never leave me|I get jealous|choose me over)\b",
    re.I,
)
NEUTRAL_PERSONAL_PLAN_PATTERN = re.compile(
    r"^\s*(?:i\s+(?:might|may|plan\s+to|intend\s+to|am\s+thinking\s+(?:of|about))|"
    r"i['’]m\s+(?:thinking\s+(?:of|about)|planning\s+to|going\s+to)|"
    r"maybe\s+i['’]ll)\b",
    re.I,
)
GENERIC_PLAN_PRAISE_PATTERN = re.compile(
    r"^\s*(?:that|it)\s+sounds\s+like\s+(?:a\s+)?"
    r"(?:good|great|nice|smart|healthy|excellent)\s+(?:idea|plan)[.!]?\s*",
    re.I,
)
UNSUPPORTED_WELLNESS_SENTENCE_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)[^.!?\n]{0,100}\b(?:clear\s+your\s+head|"
    r"help\s+you\s+(?:reset|unwind|recharge|relax)|do\s+you\s+good|"
    r"good\s+for\s+your\s+(?:mental\s+)?health)\b[^.!?\n]*[.!?]?",
    re.I,
)
FRENCH_MANNERISM_PATTERN = re.compile(
    r"\b(?:c['’]est|très|ça|voilà|merci|d['’]accord|bien\s+sûr|bon\s+courage|"
    r"bon\s+repos|bon\s+appétit|en\s+effet|mon\s+dieu|quelle|magnifique|charmant|"
    r"parfait)\b",
    re.I,
)
ITALIC_ASIDE_PATTERN = re.compile(r"(?<!\*)\*(?!\*)([^*\n]{1,100})(?<!\*)\*(?!\*)")
OPERATIONAL_REPLY_PATTERN = re.compile(
    r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?))?"
    r"(?:turn|switch|set|start|stop|open|close|lock|unlock|enable|disable|run|send|"
    r"show|check|cancel|pause|resume|remind|schedule)\b|"
    r"\b(?:does(?:\s+not|n['’]t)\s+make\s+sense|already\s+awake)\b",
    re.I,
)
EXPLICIT_BRIEF_PATTERN = re.compile(
    r"\b(?:keep it brief|be brief|brief answer|no follow[- ]?up|brutally short|"
    r"short version|in one sentence|one-sentence|one line|no lecture|spare me (?:the )?speech)\b",
    re.I,
)
LIGHTWEIGHT_OPINION_PATTERN = re.compile(
    r"\b(?:do you think|what do you think|pick one|which (?:one|would you)|"
    r"your opinion|give me (?:one|your) opinion|tell me why (?:that|this|it)|"
    r"be honest)\b",
    re.I,
)
COMPLEX_DOMAIN_PATTERN = re.compile(
    r"\b(?:code|api|database|algorithm|architecture|security|network|model|"
    r"medical|medicine|legal|contract|financial|investment|trading|physics|"
    r"engineering|statistics|research|diagnos(?:e|is)|risk analysis)\b",
    re.I,
)
EXACT_BULLET_PATTERN = re.compile(
    r"\b(?:exactly\s+)?(?P<count>one|two|three|four|five|[1-5])\s+"
    r"(?:short\s+)?(?P<kind>bullets?|moves?|examples?|points?)\b",
    re.I,
)
ONE_LINE_PATTERN = re.compile(r"\b(?:one line|single line)\b", re.I)
NO_CONGRATULATIONS_PATTERN = re.compile(
    r"\b(?:do not|don['’]t|no)\s+congratulat(?:e|ions?)\b", re.I
)
CONGRATULATIONS_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)(?:congratulations|congrats|well done|nice work)\b[^.!?\n]*[.!?]?",
    re.I,
)
PRIORITY_REQUEST_PATTERN = re.compile(
    r"\b(?:what|which)\b.{0,35}\b(?:first|priority|attention)\b|"
    r"\bwhat matters first\b",
    re.I,
)
EXPLICIT_FRENCH_REQUEST_PATTERN = re.compile(
    r"\b(?:speak|say|write|reply|respond|translate)\b.{0,30}\b(?:in|into)?\s*french\b|"
    r"^\s*(?:bonjour|salut)\b",
    re.I,
)
LOW_STAKES_PERSONAL_UPDATE_PATTERN = re.compile(
    r"\b(?:just\s+)?(?:finished|done|off)\s+(?:with\s+)?work\b|"
    r"\b(?:just\s+)?(?:had|ate)\s+(?:a\s+)?(?:late\s+)?(?:breakfast|lunch|dinner)\b|"
    r"\b(?:just\s+)?woke\s+up\b|\bgetting\s+ready\s+for\s+work\b",
    re.I,
)
OVERDONE_CASUAL_REACTION_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)[^.!?\n]{0,140}\b(?:"
    r"sounds\s+like\s+(?:a\s+)?solid|earned\s+(?:some|a|your|the)\s+rest|"
    r"long\s+shift|won['’]t\s+suggest\s+anything|keep\s+(?:your\s+)?focus\s+sharp|"
    r"perfect\s+way\s+to\s+end|sounds\s+like\s+a\s+long\s+day|probably\s+tired|"
    r"perfect\s+sense\s+to\s+rest|your\s+body\s+is\s+asking|you\s+deserve\s+to\s+rest)"
    r"\b[^.!?\n]*[.!?]?",
    re.I,
)
ALARM_CORRECTION_PATTERN = re.compile(
    r"\balready\s+awake\b.{0,120}\b(?:alarm|reminder)\b|"
    r"\bdoes(?:\s+not|n['’]t)\s+make\s+sense\b.{0,80}\b(?:alarm|reminder)\b",
    re.I,
)


def _remove_suffix_preserving_punctuation(text: str, pattern: re.Pattern) -> str:
    match = pattern.search(text)
    if not match:
        return text
    suffix = match.group(0)
    punctuation = next((mark for mark in "?!" if mark in suffix), "")
    if not punctuation and "." in suffix:
        punctuation = "."
    stem = text[: match.start()].rstrip()
    if punctuation and stem and stem[-1] not in ".!?":
        stem += punctuation
    return stem


def naturalize_prose_punctuation(text: str) -> str:
    protected = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
    parts: list[str] = []
    cursor = 0
    for match in protected.finditer(text):
        parts.append(_rewrite_punctuation(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_rewrite_punctuation(text[cursor:]))
    return "".join(parts)


def _rewrite_punctuation(text: str) -> str:
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r";\s+([A-Z])", lambda match: ". " + match.group(1), text)
    return re.sub(r";\s*", ", ", text)


def _recent_assistant_messages(history) -> list[str]:
    messages: list[str] = []
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


_REDUNDANCY_STOP_WORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have "
    "how i if in is it me my of on or our should that the their them then there "
    "these they this those to was were what when where which who why will with would "
    "you your".split()
)


def _meaningful_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9'-]+", text.casefold())
        if term not in _REDUNDANCY_STOP_WORDS
    }


def remove_redundant_opening(response: str, user_text: str) -> str:
    """Drop a first sentence that merely parrots a detailed user situation."""

    match = re.match(r"^(.+?[.!?])\s+(.+)$", response.strip(), re.S)
    if not match:
        return response.strip()
    first, remainder = match.groups()
    first_terms = _meaningful_terms(first)
    user_terms = _meaningful_terms(user_text)
    overlap = first_terms & user_terms
    if len(overlap) >= 5 and len(overlap) / max(len(first_terms), 1) >= 0.68:
        return remainder.strip()
    return response.strip()


def remove_trailing_fragment(response: str) -> str:
    """Drop a tiny unfinished tail left when local generation reaches its cap."""

    clean = response.strip()
    match = re.search(r"([.!?])\s+([^.!?\n]+)$", clean)
    if not match:
        return clean
    tail = match.group(2).strip()
    if len(tail.split()) <= 3 and re.fullmatch(
        r"(?:and|but|so|because|what|which|where|when|why|how|the|a|an|to|for)"
        r"(?:\s+\w+){0,2}",
        tail,
        re.I,
    ):
        return clean[: match.start(2)].rstrip()
    return clean


def normalize_explicit_bullets(response: str, user_text: str) -> str:
    """Honor a requested bullet count when a model emits one long list line."""

    request = EXACT_BULLET_PATTERN.search(str(user_text or ""))
    if not request:
        return response.strip()
    names = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    raw_count = request.group("count").casefold()
    expected = int(raw_count) if raw_count.isdigit() else names[raw_count]
    clean = response.strip()
    marker = re.compile(r"(?:^|(?<=\s))(?:[-*•]|\d{1,2}[.)])\s+")
    matches = list(marker.finditer(clean))
    items: list[str] = []
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
            item = clean[match.end() : end].strip(" \n;,")
            if item:
                items.append(item)
    if len(items) != expected:
        ordinal = re.compile(
            r"(?:^|(?<=[.!?])\s+)(?:first|second|third|fourth|fifth)[,:]\s+",
            re.I,
        )
        matches = list(ordinal.finditer(clean))
        ordinal_items: list[str] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
            item = clean[match.end() : end].strip(" \n;,")
            if item:
                ordinal_items.append(item)
        if ordinal_items:
            items = ordinal_items
    if len(items) != expected:
        lines = [
            re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*", "", line).strip()
            for line in clean.splitlines()
            if line.strip()
        ]
        if len(lines) == expected:
            items = lines
    if len(items) >= expected:
        return "\n".join(f"- {item}" for item in items[:expected])
    return response.strip()


def enforce_line_and_tone_constraints(response: str, user_text: str) -> str:
    """Apply literal, low-risk presentation constraints after model styling."""

    clean = response.strip()
    user = str(user_text or "")
    if NO_CONGRATULATIONS_PATTERN.search(user):
        clean = CONGRATULATIONS_PATTERN.sub("", clean).strip()
    if ONE_LINE_PATTERN.search(user):
        clean = re.sub(r"\s*\n+\s*", " ", clean)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
    structured_list = len(re.findall(r"(?:^|\s)(?:[-*•]|\d{1,2}[.)])\s+", clean)) >= 2
    if EXPLICIT_BRIEF_PATTERN.search(user) and not structured_list:
        sentences = [
            part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()
        ]
        if len(sentences) > 2:
            clean = " ".join(sentences[:2])
        words = clean.split()
        if len(words) > 45:
            clean = " ".join(words[:45]).rstrip(" ,;:") + "…"
    lightweight_opinion = (
        len(user.split()) <= 32
        and LIGHTWEIGHT_OPINION_PATTERN.search(user)
        and not COMPLEX_DOMAIN_PATTERN.search(user)
    )
    if lightweight_opinion and not structured_list and "```" not in clean:
        sentences = [
            part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()
        ]
        selected: list[str] = []
        for sentence in sentences[:2]:
            if len(" ".join(selected + [sentence]).split()) > 55:
                break
            selected.append(sentence)
        if selected:
            clean = " ".join(selected)
    return clean


def condense_priority_answer(response: str, user_text: str) -> str:
    """Keep prioritization decisive and add one concrete next action when available."""

    if not PRIORITY_REQUEST_PATTERN.search(str(user_text or "")):
        return response.strip()
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", response.strip())
        if part.strip()
    ]
    if not sentences:
        return response.strip()
    direct = sentences[0]
    proactive = next(
        (
            sentence
            for sentence in sentences[1:]
            if re.search(
                r"\bI can (?:check|track|review|show|prepare|schedule)\b",
                sentence,
                re.I,
            )
        ),
        "",
    )
    if not proactive and re.search(r"\bflight\b", str(user_text or ""), re.I):
        proactive = "I can check the flight details next."
    return " ".join(part for part in (direct, proactive) if part)


def _remove_short_french_asides(text: str) -> str:
    def replace(match: re.Match) -> str:
        return "" if FRENCH_MANNERISM_PATTERN.search(match.group(1)) else match.group(0)

    cleaned = ITALIC_ASIDE_PATTERN.sub(replace, text)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"^[,;:]\s*", "", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned.strip()


def _plain_personal_acknowledgement(user_text: str) -> str:
    lowered = user_text.casefold()
    meal = bool(re.search(r"\b(?:breakfast|lunch|dinner)\b", lowered))
    finished_work = bool(
        re.search(r"\b(?:finished|done|off)\s+(?:with\s+)?work\b", lowered)
    )
    if finished_work and meal:
        return "Ah, got it. Hope dinner was good."
    if finished_work:
        return "Ah, got it. You’re off the clock now."
    if meal:
        return "Nice. Hope it hit the spot."
    if re.search(r"\bwoke\s+up\b", lowered):
        return "Morning. Sounds like you’re getting moving."
    return "Got it."


class ResponsePolicy:
    def __init__(
        self, persona: dict, personality_context, minimal_sanitization: bool = True
    ):
        self.persona = persona
        self.personality_context = personality_context
        self.minimal_sanitization = minimal_sanitization

    def sanitize(self, response: str) -> str:
        response = SPEAKER_TAG_PATTERN.sub("", response).strip()
        response = THINK_PATTERN.sub("", response).strip()
        response = META_NOTE_PATTERN.sub("", response).strip()
        response = ACTION_PATTERN.sub("", response).strip()
        response = DEPENDENCY_PATTERN.sub("I’m here to help", response).strip()
        if (self.persona.get("name") or "").strip().lower() == "curie":
            response = _remove_suffix_preserving_punctuation(
                response, CANNED_FRENCH_SUFFIX_PATTERN
            )
            response = CANNED_FRENCH_ASIDE_PATTERN.sub("", response).rstrip()
            # A French address can be charming when context earns it.  Appending
            # one to every answer is mechanical and makes command replies sound
            # formal, so the shared boundary removes only canned end tags.
            response = _remove_suffix_preserving_punctuation(
                response, CANNED_ADDRESS_SUFFIX_PATTERN
            )
            response = naturalize_prose_punctuation(response)
        if not self.minimal_sanitization:
            response = CODE_BLOCK_PATTERN.sub("", response).strip()
            response = INLINE_CODE_PATTERN.sub("", response).strip()
        response = re.sub(r" +", " ", response)
        response = re.sub(r"\n\n\n+", "\n\n", response)
        return response.strip()

    def finalize(
        self, response: str, user_text: str, profile=None, history=None
    ) -> str:
        sanitized = self.sanitize(response)
        styled = self.personality_context.apply_response_style(
            sanitized, user_text, user_profile=profile, history=history
        )
        if (
            self.persona.get("name") or ""
        ).strip().casefold() == "curie" and ALARM_CORRECTION_PATTERN.search(
            str(user_text or "")
        ):
            styled = "Right. You’re already awake, so setting an alarm now wouldn’t make sense."
        if (
            (self.persona.get("name") or "").strip().casefold() == "curie"
            and not EXPLICIT_FRENCH_REQUEST_PATTERN.search(str(user_text or ""))
            and FRENCH_MANNERISM_PATTERN.search(styled)
        ):
            recent_french = any(
                FRENCH_MANNERISM_PATTERN.search(message)
                for message in _recent_assistant_messages(history)
            )
            if recent_french or OPERATIONAL_REPLY_PATTERN.search(str(user_text or "")):
                cleaned = _remove_short_french_asides(styled)
                if cleaned:
                    styled = cleaned
        if (
            (self.persona.get("name") or "").strip().casefold() == "curie"
            and "?" not in str(user_text or "")
            and LOW_STAKES_PERSONAL_UPDATE_PATTERN.search(str(user_text or ""))
        ):
            if OVERDONE_CASUAL_REACTION_PATTERN.search(styled):
                styled = _plain_personal_acknowledgement(str(user_text or ""))
            else:
                cleaned = _remove_short_french_asides(styled)
                cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
                if not re.search(r"\w", cleaned):
                    cleaned = ""
                styled = cleaned or _plain_personal_acknowledgement(
                    str(user_text or "")
                )
        if (
            (self.persona.get("name") or "").strip().casefold() == "curie"
            and "?" not in str(user_text or "")
            and NEUTRAL_PERSONAL_PLAN_PATTERN.search(str(user_text or ""))
        ):
            styled = UNSUPPORTED_WELLNESS_SENTENCE_PATTERN.sub("", styled).strip()
            styled = GENERIC_PLAN_PRAISE_PATTERN.sub("Fair enough. ", styled).strip()
            styled = re.sub(r"\s+", " ", styled).strip()
            if not styled:
                styled = "Fair enough."
        if EXPLICIT_BRIEF_PATTERN.search(str(user_text or "")):
            # Remove only an appended question sentence.  The earlier pattern
            # could begin at any matching auxiliary verb, so a response such
            # as ``Here are three ideas: ... Which one?`` was reduced to
            # ``Here`` because ``are`` matched near the start of the answer.
            styled = re.sub(
                r"(?:\n+|(?<=[.!])\s+)(?:and|which|would|do|does|is|are|what|how|can|could|should)\b"
                r"[^?\n]*\?\s*$",
                "",
                styled,
                flags=re.I,
            ).rstrip()
        styled = remove_redundant_opening(styled, str(user_text or ""))
        recent_french = any(
            FRENCH_MANNERISM_PATTERN.search(message)
            for message in _recent_assistant_messages(history)
        )
        if (
            not EXPLICIT_FRENCH_REQUEST_PATTERN.search(str(user_text or ""))
            or recent_french
            or OPERATIONAL_REPLY_PATTERN.search(str(user_text or ""))
        ):
            styled = LEADING_FRENCH_FILLER_PATTERN.sub("", styled).strip()
        styled = REDUNDANT_ACKNOWLEDGEMENT_OPENING_PATTERN.sub("", styled).strip()
        styled = GENERIC_FLATTERY_SENTENCE_PATTERN.sub("", styled).strip()
        styled = INVENTED_EXPERIENCE_PATTERN.sub("", styled).strip()
        styled = INVENTED_EXPERIENCE_PHRASE_PATTERN.sub(
            "based on my reasoning", styled
        ).strip()
        styled = GENERIC_FOLLOWUP_PATTERN.sub("", styled).strip()
        styled = GENERIC_REASSURANCE_SENTENCE_PATTERN.sub("", styled).strip()
        styled = remove_trailing_fragment(styled)
        styled = normalize_explicit_bullets(styled, str(user_text or ""))
        styled = enforce_line_and_tone_constraints(styled, str(user_text or ""))
        return condense_priority_answer(styled, str(user_text or ""))
