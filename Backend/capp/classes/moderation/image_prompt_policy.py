# capp/classes/moderation/image_prompt_policy.py
"""
Centralised prohibited-subject policy for AI image generation.

Product policy
--------------
Adult NSFW / sexually explicit imagery is ALLOWED. What is never allowed —
sexual or not, incidental or central — is any image that requests or
materially involves:

  * ``minors``   – children, babies, teenagers, or any under-18 person;
  * ``bestiality`` – sexual acts or fetishised sexual contexts involving animals;
  * ``deceased`` – corpses, cadavers, dead or dying people, funerary subjects.

Non-sexual depictions that include animals (e.g. someone riding a horse,
walking a dog, farm scenes with livestock, or wildlife in East Africa) are
allowed. Only sexual/bestiality content involving animals is prohibited.

This module is the single source of truth for that policy. Every image
generation path (in-chat generation, character gallery generation, avatar
creation/regeneration, the Celery task, and the low-level provider helpers)
routes its text through :class:`ImagePromptPolicy` before any provider request
is dispatched and before any sparks are charged.

Relationship to provider safety
-------------------------------
The fal.ai Seedream request deliberately sets ``enable_safety_checker: False``
(a product requirement). That provider-side setting is INDEPENDENT of this
module: the application policy here is the only guarantee that prohibited
subjects are refused, so it must be enforced server-side on every path and can
never be relaxed on the assumption that the provider will catch something.

Detection approach and limitations
----------------------------------
Detection is deterministic: text is unicode-normalised, leet-speak and
separator obfuscation is folded, and terms are matched on word/phrase
boundaries so benign substrings ("va*cat*ion", "*cow*ard", "*bat*tery") do not
trip the filter. Numeric ages below 18 are detected via age-expression
patterns rather than bare digits.

This is a lexical filter, not a semantic classifier. It cannot recognise
paraphrase, novel euphemism, non-English phrasings beyond the terms listed
below, or a prohibited subject implied purely by context. It is a strong
deterministic floor, not a complete semantic guarantee. See the module-level
notes in the PR description for the documented gaps.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Pattern, Tuple

# Error code returned to API clients. Frontends map this to a localised
# message; it intentionally does not disclose which category matched.
PROHIBITED_SUBJECT_ERROR_CODE = "image_prompt_prohibited_subject"

# Policy categories, in evaluation order.
CATEGORY_MINORS = "minors"
CATEGORY_BESTIALITY = "bestiality"
CATEGORY_DECEASED = "deceased"


# ---------------------------------------------------------------------------
# Term lists
# ---------------------------------------------------------------------------
# Entries are matched on word/phrase boundaries against normalised text. Use
# the singular form: a trailing-plural allowance ("s"/"es") is added
# automatically, so "puppy"/"puppies" style irregulars still need their own
# entry.

MINOR_TERMS: Tuple[str, ...] = (
    # Direct nouns
    "child",
    "children",
    "childlike",
    "child like",
    "kid",
    "kiddie",
    "kiddo",
    "baby",
    "babies",
    "infant",
    "newborn",
    "new born",
    "toddler",
    "preschooler",
    "kindergartner",
    "schoolchild",
    "schoolchildren",
    "schoolboy",
    "schoolgirl",
    "school kid",
    "school student",
    "minor",
    "underage",
    "under age",
    "under aged",
    "juvenile",
    "adolescent",
    "preadolescent",
    "pre adolescent",
    "pubescent",
    "prepubescent",
    "pre pubescent",
    "prepubertal",
    "teen",
    "teenage",
    "teenager",
    "preteen",
    "pre teen",
    "tween",
    "youngster",
    "youth",
    # "boy"/"girl" alone are routinely used for adults, so only the
    # unambiguous modified forms are listed (see _MINOR_MODIFIER_PATTERN for
    # the general "<young-ish modifier> boy/girl" rule).
    "little one",
    "lad",
    "toddlerhood",
    "nursery school",
    "primary school",
    "elementary school",
    "middle school",
    "high school",
    "highschool",
    "grade school",
    "junior school",
    "day care",
    "daycare",
    "kindergarten",
    "playground child",
    "son",
    "daughter",
    "nephew",
    "niece",
    "grandchild",
    "grandchildren",
    "granddaughter",
    "grandson",
    "stepson",
    "stepdaughter",
    "loli",
    "lolita",
    "shota",
    "jailbait",
    "youngling",
    "sixth grader",
    "seventh grader",
    "eighth grader",
    # Kiswahili
    "mtoto",
    "watoto",
    "mtoto mdogo",
    "mchanga",
    "mtoto mchanga",
    "kijana",
    "vijana",
    "kijana mdogo",
    "msichana",
    "wasichana",
    "mvulana",
    "wavulana",
    "mwanafunzi",
    "wanafunzi",
    "balehe",
    "utotoni",
    "chekechea",
    "shule ya msingi",
)

# Bestiality / zoophilia-related terms only.
# This list intentionally blocks sexual/bestiality content while allowing
# normal, non-sexual interactions with animals (riding, walking, farming,
# wildlife scenes, etc.).
BESTIALITY_TERMS: Tuple[str, ...] = (
    # Core clinical / slang terms
    "bestiality",
    "zoophilia",
    "zoophile",
    # Extra-cautious explicit phrases involving sexual acts with animals
    "animal sex",
    "sex with animal",
    "sex with animals",
    "sex with dog",
    "sex with dogs",
    "sex with cat",
    "sex with cats",
    "sex with horse",
    "sex with horses",
    "sex with livestock",
    "sex with cattle",
    "sex with goat",
    "sex with goats",
    "sex with sheep",
    "sex with pig",
    "sex with pigs",
    "sex with donkey",
    "sex with donkeys",
    "sex with mule",
    "sex with mules",
    "sex with camel",
    "sex with camels",
    "sex with chicken",
    "sex with chickens",
    "sex with bird",
    "sex with birds",
    "sex with wildlife",
    "sex with wild animal",
    "sex with wild animals",
    "fucking animal",
    "fucking animals",
    "fucking dog",
    "fucking dogs",
    "fucking cat",
    "fucking cats",
    "fucking horse",
    "fucking horses",
    "fucking livestock",
    "fucking cattle",
    "fucking goat",
    "fucking goats",
    "fucking sheep",
    "fucking pig",
    "fucking pigs",
    "fucking donkey",
    "fucking donkeys",
    "fucking mule",
    "fucking mules",
    "fucking camel",
    "fucking camels",
    "fucking chicken",
    "fucking chickens",
    "fucking bird",
    "fucking birds",
    "fucking wildlife",
    "fucking wild animal",
    "fucking wild animals",
    "having sex with animal",
    "having sex with animals",
    "having sex with dog",
    "having sex with dogs",
    "having sex with cat",
    "having sex with cats",
    "having sex with horse",
    "having sex with horses",
    "having sex with livestock",
    "having sex with cattle",
    "having sex with goat",
    "having sex with goats",
    "having sex with sheep",
    "having sex with pig",
    "having sex with pigs",
    "having sex with donkey",
    "having sex with donkeys",
    "having sex with mule",
    "having sex with mules",
    "having sex with camel",
    "having sex with camels",
    "having sex with chicken",
    "having sex with chickens",
    "having sex with bird",
    "having sex with birds",
    "having sex with wildlife",
    "having sex with wild animal",
    "having sex with wild animals",
    # Kiswahili (explicit sexual formulations)
    "kujamiiana na mnyama",
    "kujamiiana na wanyama",
    "kufanya ngono na mnyama",
    "kufanya ngono na wanyama",
    "ngono na mnyama",
    "ngono na wanyama",
)

DECEASED_TERMS: Tuple[str, ...] = (
    # "carcass", "the dead" and "mzoga" are matched in _DEATH_PROXIMITY_PATTERNS
    # instead, so an animal's carcass or a dead fish is not a deceased person.
    "corpse",
    "cadaver",
    "human carcass",
    "dead body",
    "dead person",
    "dead people",
    "dead man",
    "dead men",
    "dead woman",
    "dead women",
    "dead child",
    "dead girl",
    "dead boy",
    "deceased",
    "dying person",
    "dying man",
    "dying woman",
    "lifeless body",
    "murder victim",
    "dead victim",
    "mutilated body",
    "dismembered body",
    "decapitated",
    "beheaded",
    "decomposing body",
    "decomposed body",
    "rotting corpse",
    "putrefied",
    "rigor mortis",
    "post mortem",
    "postmortem",
    "autopsy",
    "morgue",
    "mortuary",
    "funeral home",
    "embalmed",
    "embalming",
    "casket",
    "coffin",
    "open casket",
    "shroud",
    "burial",
    "buried body",
    "exhumed",
    "exhumation",
    "open grave",
    "mass grave",
    "fresh grave",
    "grave site",
    "gravesite",
    "grave yard",
    "graveyard",
    "cemetery",
    "tombstone",
    "headstone",
    "crypt",
    "tomb",
    "mausoleum",
    "necropsy",
    "necrophilia",
    "necrophile",
    "mummified corpse",
    "mummified body",
    "skeleton remains",
    "human remains",
    "skull remains",
    "gore",
    "hanged body",
    "drowned body",
    "corpse bride",
    "zombie",
    "undead",
    "revenant",
    "ghoul",
    "lich",
    "reanimated corpse",
    "wake ceremony",
    "lying in state",
    # Bare "dead"/"dying"/"died" are NOT listed: "dead leaves", "dead end" and
    # "deadpan" are ordinary scene language. They are caught instead by
    # _DEATH_PROXIMITY_PATTERNS when they appear near a person noun.
    # Kiswahili
    "maiti",
    "marehemu",
    "amefariki",
    "amekufa",
    "mfu",
    "wafu",
    "kaburi",
    "makaburi",
    "mazishi",
    "jeneza",
    "chumba cha maiti",
    "mochari",
    "msiba",
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Common leet-speak / homoglyph substitutions used to slip past word lists.
_LEET_MAP = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "8": "b",
        "9": "g",
        "@": "a",
        "$": "s",
        "!": "i",
        "|": "i",
        "£": "e",
    }
)

_CONFUSABLE_MAP = str.maketrans(
    {
        # Conservative Cyrillic/Greek lookalikes found in Latin policy terms.
        "а": "a",
        "е": "e",
        "і": "i",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "Α": "a",
        "Β": "b",
        "Ε": "e",
        "Ι": "i",
        "Κ": "k",
        "Μ": "m",
        "Ν": "n",
        "Ο": "o",
        "Ρ": "p",
        "Τ": "t",
        "Χ": "x",
        "α": "a",
        "β": "b",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "χ": "x",
    }
)

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_REPEAT_RE = re.compile(r"([a-z])\1{2,}")
# Three or more single letters separated by spaces: "c h i l d".
_SPACED_LETTERS_RE = re.compile(r"(?<![a-z0-9])(?:[a-z] ){2,}[a-z](?![a-z0-9])")


def _normalise(text: str) -> str:
    """
    Fold text to a lowercase, single-spaced, ASCII form, preserving digits.

    Accents are stripped, separator characters (punctuation, underscores,
    zero-width joiners) collapse to single spaces, and runs of 3+ identical
    letters are squashed ("naaaked" -> "naked"). The result always has a single
    leading and trailing space so word-boundary matching is uniform.
    """
    if not text:
        return " "
    lowered = unicodedata.normalize("NFKD", str(text)).lower()
    lowered = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    lowered = _NON_WORD_RE.sub(" ", lowered)
    lowered = _REPEAT_RE.sub(r"\1", lowered)
    return f" {' '.join(lowered.split())} "


def _canonical_source(text: str, *, map_leet: bool) -> str:
    """Canonicalize confusables while preserving whitespace boundaries."""
    source = unicodedata.normalize("NFKD", str(text)).translate(_CONFUSABLE_MAP).lower()
    source = "".join(ch for ch in source if not unicodedata.combining(ch) and unicodedata.category(ch) != "Cf")
    return source.translate(_LEET_MAP) if map_leet else source


def _separator_elided(text: str) -> str:
    """Remove punctuation inside words without joining real whitespace."""
    chars = []
    for ch in text:
        if ch.isspace():
            chars.append(" ")
        elif ch.isascii() and ch.isalnum():
            chars.append(ch)
    compact = _REPEAT_RE.sub(r"\1", "".join(chars))
    return f" {' '.join(compact.split())} "


def _normalised_variants(text: str) -> Tuple[str, ...]:
    """
    Return the normalised forms of ``text`` that patterns are matched against.

    Three variants are produced so a single spelling trick does not defeat the
    policy while digits stay intact for the numeric-age patterns:

    1. the plain normalised text (digits preserved — needed for "12 year old");
    2. the same text with leet/homoglyph substitutions reversed ("ch1ld");
    3. variant 2 with spaced-out letter runs rejoined ("c h i l d" -> "child").
    """
    canonical = _canonical_source(text, map_leet=False)
    plain = _normalise(canonical)
    variants = [plain]
    unleet = _normalise(_canonical_source(text, map_leet=True))
    if unleet != plain:
        variants.append(unleet)
    despaced = _SPACED_LETTERS_RE.sub(lambda m: m.group(0).replace(" ", ""), unleet)
    if despaced != unleet:
        variants.append(despaced)
    elided = _separator_elided(_canonical_source(text, map_leet=True))
    if elided not in variants:
        variants.append(elided)
    return tuple(variants)


def _normalise_term(term: str) -> str:
    """Normalise a policy term the same way prompt text is normalised."""
    return _normalise(term).strip()


def _build_term_pattern(terms: Iterable[str]) -> Pattern[str]:
    """
    Compile one alternation matching any term on word boundaries.

    Each term also matches a simple trailing plural ("dog" -> "dogs",
    "fox" -> "foxes"), and intra-term spaces tolerate any run of whitespace.
    """
    alternatives: List[str] = []
    for term in sorted({_normalise_term(t) for t in terms if _normalise_term(t)}, key=len, reverse=True):
        escaped = r"\s+".join(re.escape(word) for word in term.split(" "))
        alternatives.append(f"{escaped}(?:es|s)?")
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


_MINOR_PATTERN = _build_term_pattern(MINOR_TERMS)
_BESTIALITY_PATTERN = _build_term_pattern(BESTIALITY_TERMS)
_DECEASED_PATTERN = _build_term_pattern(DECEASED_TERMS)

_SEXUAL_ACT = (
    r"(?:sex(?:ual)?|intercourse|oral\s+sex|penetrat(?:e|es|ed|ing)|"
    r"erotic|mating|mate[ds]?|copulat(?:e|es|ed|ing)|genital)"
)
_ANIMAL_WORD = r"(?:animal|dog|cat|horse|goat|sheep|pig|donkey|mule|camel|" r"chicken|bird|livestock|cattle|wildlife)"
_SEXUAL_GAP = r"(?:\s+[a-z0-9]+){0,6}\s+"
_BESTIALITY_PROXIMITY_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(rf"(?<![a-z0-9]){_SEXUAL_ACT}{_SEXUAL_GAP}{_ANIMAL_WORD}(?![a-z0-9])"),
    re.compile(rf"(?<![a-z0-9]){_ANIMAL_WORD}{_SEXUAL_GAP}{_SEXUAL_ACT}(?![a-z0-9])"),
)

# "boy"/"girl"/"male"/"female" are only prohibited when qualified by a
# youth modifier, because unqualified they routinely describe adults.
_YOUTH_MODIFIER = (
    r"(?:little|lil|small|tiny|baby|young|younger|youngest|innocent|"
    r"petite young|barely legal|school|middle school|high school|"
    r"elementary|primary school|mdogo|kidogo)"
)
_MINOR_MODIFIER_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(rf"(?<![a-z0-9]){_YOUTH_MODIFIER}\s+(?:boy|girl|male|female|one|body)(?![a-z0-9])"),
    # "girl child", "boy child" (common East African formulation)
    re.compile(r"(?<![a-z0-9])(?:boy|girl)\s+child(?![a-z0-9])"),
)

# Numeric under-18 age expressions. Bare numbers are never enough — the digits
# must sit in an age-shaped phrase so "17th century market" stays allowed.
_UNDER_18 = r"(?:[0-9]|1[0-7])"
_NUMERIC_AGE_PATTERNS: Tuple[Pattern[str], ...] = (
    # "16 year old", "9 yr old", "12yo", "15 y o"
    re.compile(rf"(?<![0-9]){_UNDER_18}\s*(?:year|yr|y)s?\s*(?:o|old)(?![a-z0-9])"),
    # "aged 14", "age 7", "age of 13", "13 years of age"
    re.compile(rf"(?<![a-z0-9])age[ds]?\s+(?:of\s+)?{_UNDER_18}(?![0-9])"),
    re.compile(rf"(?<![0-9]){_UNDER_18}\s*(?:year|yr)s?\s+of\s+age(?![0-9])"),
    # "she is 15 years old", "he is only 12", "just 9"
    re.compile(rf"(?<![a-z0-9])(?:is|only|just|barely)\s+{_UNDER_18}\s*(?:year|yr)s?\s*old(?![a-z0-9])"),
    re.compile(rf"(?<![a-z0-9])(?:only|just|barely)\s+{_UNDER_18}(?![0-9])"),
    # "grade 6", "class 4", "form 2", "standard 5" (school-year formulations)
    re.compile(r"(?<![a-z0-9])(?:grade|class|form|standard|std)\s+(?:[1-9]|1[0-2])(?![0-9])"),
    # Kiswahili: "miaka 15", "umri wa miaka 12", "mwaka 9"
    re.compile(rf"(?<![a-z0-9])miaka\s+{_UNDER_18}(?![0-9])"),
    re.compile(rf"(?<![a-z0-9])mwaka\s+{_UNDER_18}(?![0-9])"),
    re.compile(rf"(?<![a-z0-9])umri\s+(?:wa\s+)?{_UNDER_18}(?![0-9])"),
)

# Hunting, fishing, food and dead animals are allowed (product decision,
# 2026-10-14); bestiality stays prohibited through its own category. A death
# word that describes an animal is therefore not a deceased person:
#   * a death word directly followed by an animal noun, optionally after
#     "of"/"wa"/"ya" and one article, demonstrative or number: "a woman holding
#     a dead fish", "the man who killed two deer", "mzoga wa mbuzi";
#   * "carcass" directly after an animal noun: "a goat carcass".
# Death words about people are unaffected: "a man killed by a lion", "the
# carcass of a woman", "a human carcass".
_ANIMAL_NOUNS: Tuple[str, ...] = (
    "animal",
    "dog",
    "cat",
    "horse",
    "goat",
    "sheep",
    "lamb",
    "pig",
    "boar",
    "warthog",
    "cow",
    "bull",
    "ox",
    "oxen",
    "calf",
    "calves",
    "donkey",
    "mule",
    "camel",
    "chicken",
    "hen",
    "rooster",
    "duck",
    "goose",
    "geese",
    "turkey",
    "bird",
    "rabbit",
    "hare",
    "deer",
    "antelope",
    "gazelle",
    "impala",
    "kudu",
    "eland",
    "buffalo",
    "wildebeest",
    "zebra",
    "giraffe",
    "elephant",
    "lion",
    "leopard",
    "cheetah",
    "hyena",
    "crocodile",
    "hippo",
    "snake",
    "fish",
    "tilapia",
    "catfish",
    "trout",
    "salmon",
    "tuna",
    "shark",
    "prawn",
    "shrimp",
    "crab",
    "lobster",
    "octopus",
    "livestock",
    "cattle",
    "poultry",
    # Kiswahili
    "mnyama",
    "wanyama",
    "mbuzi",
    "ng'ombe",
    "kuku",
    "samaki",
    "kondoo",
    "nguruwe",
    "swala",
    "nyati",
)
_ANIMAL_FORMS: Tuple[str, ...] = tuple(
    sorted(
        {
            form
            for noun in _ANIMAL_NOUNS
            for form in (_normalise_term(noun), _normalise_term(noun) + "s", _normalise_term(noun) + "es")
        },
        key=len,
        reverse=True,
    )
)
_ANIMAL_NOUN = (
    r"(?:" + "|".join(r"\s+".join(re.escape(word) for word in form.split(" ")) for form in _ANIMAL_FORMS) + r")"
)
# Articles, demonstratives and numbers only. Possessives are left out on purpose:
# punctuation is dropped during normalisation, so "a man killed, his dog nearby"
# would otherwise read as an animal being killed.
_ANIMAL_DETERMINER = r"(?:a|an|the|this|that|these|those|some|one|two|three|four|five|several|many|[0-9]+)"
# Negative lookahead placed right after a death word: the word describes an animal.
_DESCRIBES_ANIMAL = rf"(?!\s+(?:(?:of|wa|ya)\s+)?(?:{_ANIMAL_DETERMINER}\s+)?{_ANIMAL_NOUN}(?![a-z0-9]))"
# "carcass" is a death word unless an animal noun comes right before it. Like
# "mzoga", it is only prohibited near a person noun: on its own, a carcass is
# an animal's.
_CARCASS = "carcass" + "".join(f"(?<!{re.escape(form)} carcass)" for form in _ANIMAL_FORMS) + "(?:es)?"

# Bare death words are ambiguous on their own ("dead leaves"), so they are only
# prohibited when they sit within a few words of a person noun, in either
# order: "dead woman", "the woman is dead", "her husband, recently deceased".
_DEATH_WORD = (
    r"(?:dead|dying|died|dies|lifeless|deceased|expired|slain|murdered|killed|amekufa|amefariki|wamekufa|"
    r"mi?zoga|" + _CARCASS + r")"
)
_PERSON_WORD = (
    r"(?:person|people|human|man|men|woman|women|male|female|body|bodies|guy|"
    r"lady|ladies|wife|wives|husband|father|mother|dad|mum|mom|parent|"
    r"grandmother|grandfather|granny|sibling|brother|sister|friend|lover|"
    r"victim|soldier|patient|bride|groom|widow|widower|face|corpse|"
    r"actor|actress|model|celebrity|teacher|doctor|nurse|student|"
    r"mtu|watu|mwanamke|mwanamume|mume|mke|mzazi|rafiki|mwathirika)"
)
_GAP = r"(?:\s+[a-z0-9]+){0,3}\s+"
_DEATH_PROXIMITY_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(rf"(?<![a-z0-9]){_DEATH_WORD}{_DESCRIBES_ANIMAL}{_GAP}{_PERSON_WORD}(?![a-z0-9])"),
    re.compile(rf"(?<![a-z0-9]){_PERSON_WORD}{_GAP}{_DEATH_WORD}(?![a-z0-9]){_DESCRIBES_ANIMAL}"),
    # "the dead" as a noun means dead people ("the dead rising"); "the dead
    # fish" is an animal.
    re.compile(rf"(?<![a-z0-9])the\s+dead(?![a-z0-9]){_DESCRIBES_ANIMAL}"),
)

# Checked in this order; the first match wins.
_CATEGORY_PATTERNS: Tuple[Tuple[str, Tuple[Pattern[str], ...]], ...] = (
    (CATEGORY_MINORS, (_MINOR_PATTERN,) + _MINOR_MODIFIER_PATTERNS + _NUMERIC_AGE_PATTERNS),
    (CATEGORY_BESTIALITY, (_BESTIALITY_PATTERN,) + _BESTIALITY_PROXIMITY_PATTERNS),
    (CATEGORY_DECEASED, (_DECEASED_PATTERN,) + _DEATH_PROXIMITY_PATTERNS),
)


class ProhibitedImageSubjectError(Exception):
    """
    Raised when text destined for an image provider violates the policy.

    Carries the matched ``category`` for server-side logging only — the
    category is never returned to API clients.
    """

    def __init__(self, category: str, code: str = PROHIBITED_SUBJECT_ERROR_CODE):
        self.category = category
        self.code = code
        super().__init__(f"prohibited image subject: {category}")


class ImagePromptPolicy:
    """
    Prohibited-subject policy for every AI image generation path.

    Adult NSFW / explicit content is deliberately NOT screened here. Only the
    three prohibited subject categories (minors, bestiality, deceased people)
    are.
    """

    ERROR_CODE = PROHIBITED_SUBJECT_ERROR_CODE
    CATEGORIES = (CATEGORY_MINORS, CATEGORY_BESTIALITY, CATEGORY_DECEASED)

    @staticmethod
    def find_violation(*texts: Optional[str]) -> Optional[str]:
        """
        Return the first prohibited category found across ``texts``, else None.

        Every argument is screened, so a prohibited subject cannot be smuggled
        in through a secondary field (character description, caption, negative
        prompt, reference description, …). ``None`` and blank values are
        skipped.
        """
        for text in texts:
            if not text:
                continue
            variants = _normalised_variants(text)
            for category, patterns in _CATEGORY_PATTERNS:
                for pattern in patterns:
                    if any(pattern.search(variant) for variant in variants):
                        return category
        return None

    @classmethod
    def is_allowed(cls, *texts: Optional[str]) -> bool:
        """True when none of ``texts`` involves a prohibited subject."""
        return cls.find_violation(*texts) is None

    @classmethod
    def assert_allowed(cls, *texts: Optional[str]) -> None:
        """
        Raise :class:`ProhibitedImageSubjectError` if any text violates policy.

        Used as the last line of defence immediately before a provider request
        so no code path — view, service, Celery task, or direct helper call —
        can reach an image provider with prohibited content.
        """
        category = cls.find_violation(*texts)
        if category:
            raise ProhibitedImageSubjectError(category)
