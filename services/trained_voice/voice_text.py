"""Bounded speech chunks; no model imports in the web process."""

import re


def speech_chunks(
    text: str, limit: int = 240, sentence_only: bool = False
) -> list[str]:
    text = text.replace("’", "'").replace("‘", "'")
    # A tiny standalone French acknowledgment plus an English sentence can
    # trigger long, poorly intelligible output in Nano. Join the aside into
    # its English clause; preserve every word and keep French-only text intact.
    text = re.sub(
        r"^(D['’]accord|Voilà|Bien sûr|Je comprends)\.\s+(Let['’]s|We|You|It|That|This|I)\b",
        lambda m: m[1] + ", " + ("I" if m[2].casefold() == "i" else m[2].lower()),
        text.strip(),
        flags=re.I,
    )
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#`•]", "", text)
    if sentence_only:
        # Reports contain headings and bullets without terminal punctuation.
        # Flattening them joins unrelated warnings/projects into one utterance.
        lines = []
        for line in text.splitlines():
            line = line.strip().replace(" · ", ", ").replace(" — ", ", ")
            if line:
                lines.append(line if line[-1] in ".!?:" else line + ".")
        text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("No speakable text")
    chunks, current, sentences, prefix = [], "", [], ""
    for part in re.split(r"(?<=[.!?])\s+", text):
        combined = prefix + part
        if re.search(r"\b(?:Dr|Mr|Mrs|Ms|Prof|St|e\.g|i\.e|U\.S)\.$", combined, re.I):
            prefix = combined + " "
            continue
        sentences.append(combined)
        prefix = ""
    if prefix:
        sentences.append(prefix.strip())
    for sentence in sentences:
        for word in sentence.split():
            if len(word) > limit:
                raise ValueError("Speech contains an overlong word")
            if current and len(current) + len(word) + 1 > limit:
                # Prefer a clause ending to a hard word-count cut. Keep a
                # meaningful lead-in so a short French aside is not isolated.
                boundaries = list(re.finditer(r"[,;:]\s+", current))
                boundary = next(
                    (m for m in reversed(boundaries) if m.end() >= limit // 3), None
                )
                if boundary is not None:
                    chunks.append(current[: boundary.start() + 1])
                    current = current[boundary.end() :]
                else:
                    chunks.append(current)
                    current = ""
                # A single large word can still exceed the remaining capacity.
                if current and len(current) + len(word) + 1 > limit:
                    chunks.append(current)
                    current = ""
            current = (current + " " + word).strip()
        if current and (
            (sentence_only and len(current.split()) >= 3) or len(current) >= limit // 2
        ):
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks
