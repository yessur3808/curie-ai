"""Bounded speech chunks; no model imports in the web process."""

import re


def speech_chunks(
    text: str, limit: int = 240, sentence_only: bool = False
) -> list[str]:
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
