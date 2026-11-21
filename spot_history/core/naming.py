# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import difflib
import re
import unicodedata

_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u00b4": "'",
        "\u0060": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)

_SPECIAL_MAP = {
    "ø": "o",
    "å": "a",
    "æ": "ae",
    "œ": "oe",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ł": "l",
}

_NUMBER_WORDS = {
    "ein": "1",
    "eins": "1",
    "zwei": "2",
    "drei": "3",
    "vier": "4",
    "fuenf": "5",
    "fünf": "5",
    "sechs": "6",
    "sieben": "7",
    "acht": "8",
    "neun": "9",
    "zehn": "10",
    "elf": "11",
    "zwoelf": "12",
    "zwölf": "12",
    "dreizehn": "13",
    "vierzehn": "14",
    "fuenfzehn": "15",
    "fünfzehn": "15",
    "sechzehn": "16",
    "siebzehn": "17",
    "achtzehn": "18",
    "neunzehn": "19",
    "zwanzig": "20",
}

_ARTICLES = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "the",
    "la",
    "le",
    "les",
    "el",
    "los",
    "las",
    "il",
    "lo",
    "l",
    "zum",
    "zur",
}

_GENERIC_TOKENS = {
    "restaurant",
    "ristorante",
    "trattoria",
    "osteria",
    "pizzeria",
    "wirtshaus",
    "gasthaus",
    "gasthof",
    "landgasthof",
    "gaststatte",
    "gaststaette",
    "wirtschaft",
    "brauhaus",
    "brauerei",
    "cafe",
    "kafe",
    "kaffee",
    "coffee",
    "bar",
    "pub",
    "kneipe",
    "lounge",
    "club",
    "biergarten",
    "beergarden",
    "imbiss",
    "kiosk",
    "hotel",
    "pension",
    "hostel",
    "gasthausbrauerei",
    "gartencenter",
    "laden",
    "shop",
    "store",
    "markt",
    "market",
    "supermarkt",
    "baeckerei",
    "bakery",
}

_PARENTHETICAL = re.compile(r"\(([^()]*)\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _fold_common(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.translate(_QUOTE_MAP)
    for source, target in _SPECIAL_MAP.items():
        text = text.replace(source, target)
    return text.replace("ß", "ss")


def _clean(text: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def fold_name(value: str | None) -> str:
    text = _fold_common(value).replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return _clean(_strip_accents(text))


def fold_name_german(value: str | None) -> str:
    text = (
        _fold_common(value)
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
    )
    return _clean(_strip_accents(text))


def _strip_parentheticals(value: str) -> list[str]:
    parts = [value]
    stripped = _PARENTHETICAL.sub(" ", value)
    if stripped != value:
        parts.append(stripped)
    for match in _PARENTHETICAL.finditer(value):
        inner = match.group(1).strip()
        if inner:
            parts.append(inner)
    return parts


def core_name(folded: str) -> str:
    tokens = [token for token in folded.split() if token]
    if not tokens:
        return ""
    tokens = [_NUMBER_WORDS.get(token, token) for token in tokens]
    while len(tokens) > 1 and (tokens[0] in _GENERIC_TOKENS or tokens[0] in _ARTICLES):
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in _GENERIC_TOKENS:
        tokens.pop()
    while len(tokens) > 1 and tokens[0] in _ARTICLES:
        tokens.pop(0)
    return " ".join(tokens)


def name_variants(value: str | None) -> tuple[str, ...]:
    variants: list[str] = []
    for part in _strip_parentheticals(value or ""):
        for folded in (fold_name(part), fold_name_german(part)):
            if not folded:
                continue
            variants.append(folded)
            core = core_name(folded)
            if core and core != folded:
                variants.append(core)
    seen: set[str] = set()
    unique: list[str] = []
    for variant in variants:
        if variant in seen:
            continue
        seen.add(variant)
        unique.append(variant)
    return tuple(unique)


def _pair_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    tokens_left = set(left.split())
    tokens_right = set(right.split())
    union = tokens_left | tokens_right
    jaccard = len(tokens_left & tokens_right) / len(union) if union else 0.0
    containment = 0.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 6 and shorter in longer:
        containment = 0.88
    subset = 0.0
    minor, major = (
        (tokens_left, tokens_right)
        if len(tokens_left) <= len(tokens_right)
        else (tokens_right, tokens_left)
    )
    if minor.issubset(major) and any(len(token) >= 4 for token in minor):
        subset = 0.92
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    return max(jaccard, containment, subset, ratio)


def name_similarity(left: str | None, right: str | None) -> float:
    best = 0.0
    for variant_left in name_variants(left):
        for variant_right in name_variants(right):
            best = max(best, _pair_similarity(variant_left, variant_right))
            if best >= 1.0:
                return 1.0
    return round(best, 3)
