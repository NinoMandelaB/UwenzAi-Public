# capp/classes/characters/attire_detection.py
"""
Attire-intent detection for user-supplied image instructions.

Why this exists
---------------
Image prompts are assembled from a character sheet plus a free-text user
instruction (``extra_prompt`` / ``primary_instruction``). When the assembled
prompt says nothing about what the subject is wearing, image models fill the
gap from gendered training-data priors — which is how male characters ended
up rendered in female lingerie for prompts as innocent as "at the beach".

The fix is to keep the character's own ``clothing_style`` line in the prompt
UNLESS the user's instruction already expresses attire intent. Attire intent
comes in two flavours, and both must suppress the default-outfit line:

  * explicit clothing  – "red dress", "wearing a suit", "in a bikini";
  * explicit nudity    – "topless", "completely naked", "uchi", "bila nguo".

Explicit user intent always wins. This is an adult app: a request for nudity
or lingerie is honoured exactly as written, with nothing injected. Only an
instruction that is *ambiguous about attire* gets the character's outfit.

This module is the single, documented home of the clothing/nudity word lists
and of the matching logic. The prompt builder
(:meth:`CharacterService._build_character_prompt`) consumes
:func:`mentions_clothing_or_nudity`; nothing else should grow a second copy
of these lists.

Matching rules
--------------
* Case-insensitive, unicode-aware, word-boundary matching: ``shirt`` does
  NOT match inside ``shirtless`` (which is its own nudity token), and
  ``t-shirt`` is recognised as a garment.
* Multi-word phrases ("no clothes", "bila nguo") match with flexible
  whitespace.
* This is a lexical detector, not a semantic classifier — a strong
  deterministic floor, in the same spirit as
  ``capp.classes.moderation.image_prompt_policy``.
"""

import re

# ---------------------------------------------------------------------------
# English clothing terms
# ---------------------------------------------------------------------------
# Garments, footwear, accessories, and the verbs that introduce attire
# ("wearing", "dressed", ...). Plural/-s forms are listed explicitly because
# matching is word-boundary exact and does not stem.
CLOTHING_TOKENS_EN = frozenset(
    {
        # generic attire nouns
        "outfit",
        "outfits",
        "attire",
        "clothes",
        "clothing",
        "garment",
        "garments",
        "apparel",
        "wardrobe",
        "costume",
        "costumes",
        "uniform",
        "uniforms",
        "formalwear",
        "casualwear",
        "sportswear",
        "streetwear",
        "swimwear",
        "underwear",
        "lingerie",
        # attire verbs / adjectives
        "wearing",
        "wears",
        "wore",
        "dressed",
        "clad",
        "clothed",
        # tops
        "shirt",
        "shirts",
        "t-shirt",
        "t-shirts",
        "tee",
        "tees",
        "polo",
        "blouse",
        "blouses",
        "hoodie",
        "hoodies",
        "sweater",
        "sweaters",
        "jumper",
        "cardigan",
        "jacket",
        "jackets",
        "coat",
        "coats",
        "blazer",
        "suit",
        "suits",
        "tuxedo",
        # bottoms
        "dress",
        "dresses",
        "gown",
        "gowns",
        "skirt",
        "skirts",
        "jeans",
        "pants",
        "trousers",
        "shorts",
        "leggings",
        "overalls",
        # swim & intimates (garments, not nudity)
        "bikini",
        "bikinis",
        "swimsuit",
        "swimsuits",
        "trunks",
        "bra",
        "bras",
        "panties",
        "knickers",
        "thong",
        "thongs",
        "boxers",
        "briefs",
        "corset",
        "negligee",
        "nightgown",
        "stockings",
        "socks",
        # footwear & accessories
        "heels",
        "shoes",
        "boots",
        "sneakers",
        "trainers",
        "sandals",
        "hat",
        "hats",
        "cap",
        "caps",
        "beanie",
        "scarf",
        "scarves",
        "tie",
        "ties",
        "bowtie",
        "belt",
        "gloves",
        "robe",
        "robes",
        "bathrobe",
        "pyjamas",
        "pajamas",
        "jersey",
        # East African & modest-fashion garments (shared with Kiswahili usage)
        "kanzu",
        "kitenge",
        "kanga",
        "kaftan",
        "hijab",
        "niqab",
        "abaya",
    }
)

# ---------------------------------------------------------------------------
# Kiswahili clothing terms
# ---------------------------------------------------------------------------
CLOTHING_TOKENS_SW = frozenset(
    {
        "nguo",  # clothes / clothing
        "mavazi",  # attire / outfit
        "vazi",  # garment / dress
        "shati",  # shirt
        "gauni",  # dress / gown
        "suruali",  # trousers
        "sketi",  # skirt
        "kofia",  # hat / cap
        "viatu",  # shoes
        "chupi",  # underwear
        "kuvaa",  # to wear
        "anavaa",  # is wearing
        "amevaa",  # is wearing (perfect)
    }
)

# ---------------------------------------------------------------------------
# English nudity terms
# ---------------------------------------------------------------------------
# A mention of any of these means the user has explicitly described an
# unclothed (or partially unclothed) subject, so the default outfit must NOT
# be injected. "shirtless" lives here rather than in the clothing list: it
# describes the absence of a garment, and word-boundary matching keeps the
# bare token "shirt" from matching inside it.
NUDITY_TOKENS_EN = frozenset(
    {
        "naked",
        "nude",
        "nudes",
        "nudity",
        "topless",
        "bottomless",
        "shirtless",
        "braless",
        "pantyless",
        "undressed",
        "undress",
        "undressing",
        "disrobed",
        "unclothed",
        "bare-chested",
        "barechested",
        "breasts",
        "boobs",
        "tits",
        "titties",
        "nipples",
    }
)

# ---------------------------------------------------------------------------
# Kiswahili nudity terms
# ---------------------------------------------------------------------------
NUDITY_TOKENS_SW = frozenset(
    {
        "uchi",  # naked / nude
    }
)

# ---------------------------------------------------------------------------
# Multi-word phrases (matched with flexible whitespace between words)
# ---------------------------------------------------------------------------
CLOTHING_PHRASES_EN = (
    "crop top",
    "tank top",
    "tube top",
    "ball gown",
    "wedding dress",
    "evening gown",
    "sports bra",
)

CLOTHING_PHRASES_SW = ()

NUDITY_PHRASES_EN = (
    "no clothes",
    "no clothing",
    "without clothes",
    "without clothing",
    "not wearing anything",
    "wearing nothing",
    "bare chest",
    "bare breasts",
    "in the nude",
    "birthday suit",
    "bare ass",
)

# "bila nguo" = "without clothes"
NUDITY_PHRASES_SW = ("bila nguo",)

# Combined sets — the public surface of this module.
CLOTHING_TOKENS = CLOTHING_TOKENS_EN | CLOTHING_TOKENS_SW
NUDITY_TOKENS = NUDITY_TOKENS_EN | NUDITY_TOKENS_SW
CLOTHING_PHRASES = CLOTHING_PHRASES_EN + CLOTHING_PHRASES_SW
NUDITY_PHRASES = NUDITY_PHRASES_EN + NUDITY_PHRASES_SW


def _build_pattern(tokens, phrases) -> "re.Pattern":
    """
    Compile one case-insensitive pattern matching any token or phrase on
    word boundaries. Phrase words are joined with ``\\s+`` so "no   clothes"
    still matches. Longest alternatives first so multi-word phrases win over
    their own single-word members.
    """
    parts = [re.escape(token) for token in tokens]
    for phrase in phrases:
        parts.append(r"\s+".join(re.escape(piece) for piece in phrase.split()))
    parts.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


_CLOTHING_PATTERN = _build_pattern(CLOTHING_TOKENS, CLOTHING_PHRASES)
_NUDITY_PATTERN = _build_pattern(NUDITY_TOKENS, NUDITY_PHRASES)


def mentions_clothing(text: str) -> bool:
    """True if ``text`` names a garment or describes someone getting/being dressed."""
    if not text:
        return False
    without_non_attire_idioms = re.sub(
        r"\b(?:cap\s+the\s+bottle|tie\s+the\s+game|dress\s+the\s+salad)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return bool(_CLOTHING_PATTERN.search(without_non_attire_idioms))


def mentions_nudity(text: str) -> bool:
    """True if ``text`` describes an unclothed or partially unclothed subject."""
    if not text:
        return False
    return bool(_NUDITY_PATTERN.search(text))


def mentions_clothing_or_nudity(text: str) -> bool:
    """
    True if ``text`` expresses ANY attire intent — explicit clothing OR
    explicit nudity. Both suppress the character's default outfit line:
    the first overrides it, the second forbids it.
    """
    return mentions_clothing(text) or mentions_nudity(text)
