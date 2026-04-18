"""
Text normalization for word filter: handles leet-speak, Unicode substitutions,
extra spacing, and character tricks. Substring matching catches partials.
"""

import unicodedata
import re

# Common leet-speak and look-alike substitutions
LEET_MAP: dict[str, str] = {
    # Digits
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "6": "g", "7": "t", "8": "b", "9": "g",
    # Symbols
    "@": "a", "$": "s", "!": "i", "(": "c", "+": "t",
    "<": "c", "|": "i", "¡": "i", "£": "e",
    # Cyrillic look-alikes
    "а": "a", "е": "e", "і": "i", "о": "o", "р": "p",
    "с": "c", "у": "y", "х": "x",
    # Fullwidth Latin
    "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e",
    "ｆ": "f", "ｇ": "g", "ｈ": "h", "ｉ": "i", "ｊ": "j",
    "ｋ": "k", "ｌ": "l", "ｍ": "m", "ｎ": "n", "ｏ": "o",
    "ｐ": "p", "ｑ": "q", "ｒ": "r", "ｓ": "s", "ｔ": "t",
    "ｕ": "u", "ｖ": "v", "ｗ": "w", "ｘ": "x", "ｙ": "y",
    "ｚ": "z",
    # Math bold/italic
    "𝐚": "a", "𝐛": "b", "𝐜": "c",
    # Misc look-alikes
    "ν": "v", "μ": "u", "α": "a", "β": "b",
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def normalize(text: str) -> str:
    """
    Return a lowercase, leet-decoded, punctuation-stripped version of *text*
    with all whitespace removed — ready for substring word-filter matching.
    """
    # Strip URLs so filter doesn't catch them (link filter handles URLs separately)
    text = _URL_RE.sub("", text)

    # NFKD decomposition collapses many Unicode variants into ASCII
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    text = text.lower()

    # Apply leet-speak map
    result = []
    for ch in text:
        result.append(LEET_MAP.get(ch, ch))
    text = "".join(result)

    # Strip everything that isn't a letter or digit, then return
    return re.sub(r"[^a-z0-9]", "", text)


def contains_banned_word(text: str, banned_words: list[str]) -> str | None:
    """Return the first matched banned word, or None."""
    normalized = normalize(text)
    for word in banned_words:
        if normalize(word) in normalized:
            return word
    return None
