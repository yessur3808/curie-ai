"""Conservative English/French spans for an explicitly bilingual speech model."""

import re
import unicodedata

_PHRASES = re.compile(
    r"(?<!\w)(?:s'il vous plaît|c'est intéressant|merci beaucoup|je comprends|bien sûr|bon courage|mon amie?|d['’]accord|bonjour|bonsoir|voil[àa]|merci)(?!\w)",
    re.I,
)
_FR_WORDS = frozenset(
    "je tu nous vous le la les une des du est êtes suis avons avec votre notre pour très comprends pouvons prendre temps avez raison ai as avons ont c’est était elle il elles ils heureux heureuse".split()
)
_EN_WORDS = frozenset(
    "the you your we our this that these those is are was were can could will would what where when why how have has from with and but not does it".split()
)
_DETECTOR = None


def _french_sentence(sentence, words):
    global _DETECTOR
    if sum(w in _EN_WORDS for w in words) >= 1:
        return False
    try:
        from lingua import Language, LanguageDetectorBuilder

        if _DETECTOR is None:
            _DETECTOR = LanguageDetectorBuilder.from_languages(
                Language.ENGLISH, Language.FRENCH
            ).build()
        scores = _DETECTOR.compute_language_confidence_values(sentence)
        if (
            len(words) >= 3
            and scores[0].language == Language.FRENCH
            and scores[0].value >= 0.9
        ):
            return True
    except ImportError:
        pass
    hits = sum(w in _FR_WORDS for w in words)
    return hits >= 2 and hits / max(1, len(words)) >= 0.3


def language_spans(text):
    text = unicodedata.normalize("NFC", text).replace("’", "'")
    parts = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[a-zà-ÿ]+", sentence.casefold())
        if _french_sentence(sentence, words):
            pieces = [("fr", sentence)]
        else:
            pieces, position = [], 0
            for match in _PHRASES.finditer(sentence):
                if match.start() > position:
                    pieces.append(("en", sentence[position : match.start()]))
                phrase = match.group()
                if phrase.casefold() == "voila":
                    phrase = "voilà"
                pieces.append(("fr", phrase))
                position = match.end()
            if position < len(sentence):
                pieces.append(("en", sentence[position:]))
        for language, piece in pieces:
            if not re.search(r"\w", piece):
                if parts:
                    parts[-1] = (parts[-1][0], parts[-1][1] + piece)
                continue
            piece = piece.strip(" ,;:")
            if parts and parts[-1][0] == language:
                parts[-1] = (language, parts[-1][1] + " " + piece)
            else:
                parts.append((language, piece))
    return parts
