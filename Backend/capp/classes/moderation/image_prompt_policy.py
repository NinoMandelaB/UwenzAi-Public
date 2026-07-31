# capp/classes/moderation/image_prompt_policy.py
"""
Centralised prohibited-subject policy for AI image generation.

Product policy
--------------
Adult NSFW / sexually explicit imagery is ALLOWED. What is never allowed —
sexual or not, incidental or central — is any image that requests or
materially involves:

  * ``minors``   – children, babies, teenagers, or any under-18 person;
  * ``animals``  – pets, livestock, wildlife, insects, or any non-human animal;
  * ``deceased`` – corpses, cadavers, dead or dying people, funerary subjects.

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
paraphrase, novel euphemism, non-English phrasings beyond the Kiswahili terms
listed below, or a prohibited subject implied purely by context. It is a strong
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
CATEGORY_ANIMALS = "animals"
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

ANIMAL_TERMS: Tuple[str, ...] = (
    # Generic
    "animal",
    "beast",
    "creature",
    "critter",
    "pet",
    "puppy",
    "puppies",
    "kitten",
    "cub",
    "livestock",
    "cattle",
    "poultry",
    "wildlife",
    "fauna",
    "mammal",
    "reptile",
    "amphibian",
    "rodent",
    "primate",
    "insect",
    "arachnid",
    "bird",
    "fish",
    "zoo",
    "safari animal",
    "game reserve animal",
    "herd",
    "flock",
    "swarm",
    "veterinary",
    "veterinarian",
    "bestiality",
    "zoophilia",
    # Pets / domestic
    "dog",
    "doggy",
    "hound",
    "canine",
    "cat",
    "feline",
    "hamster",
    "guinea pig",
    "gerbil",
    "ferret",
    "rabbit",
    "bunny",
    "parrot",
    "parakeet",
    "budgie",
    "canary",
    "goldfish",
    "turtle",
    "tortoise",
    "lizard",
    "gecko",
    "iguana",
    "snake",
    "python",
    "cobra",
    "viper",
    "mamba",
    # Livestock / farm
    "cow",
    "bull",
    "ox",
    "oxen",
    "calf",
    "calves",
    "heifer",
    "goat",
    "sheep",
    "lamb",
    "ram",
    "ewe",
    "pig",
    "piglet",
    "hog",
    "boar",
    "horse",
    "foal",
    "mare",
    "stallion",
    "donkey",
    "mule",
    "camel",
    "chicken",
    "hen",
    "rooster",
    "cockerel",
    "duck",
    "duckling",
    "goose",
    "geese",
    "turkey",
    "guinea fowl",
    # Wildlife
    "lion",
    "lioness",
    "tiger",
    "leopard",
    "cheetah",
    "jaguar",
    "panther",
    "lynx",
    "hyena",
    "jackal",
    "wolf",
    "wolves",
    "fox",
    "bear",
    "elephant",
    "rhino",
    "rhinoceros",
    "hippo",
    "hippopotamus",
    "giraffe",
    "zebra",
    "buffalo",
    "wildebeest",
    "antelope",
    "gazelle",
    "impala",
    "kudu",
    "eland",
    "warthog",
    "baboon",
    "monkey",
    "chimpanzee",
    "chimp",
    "gorilla",
    "orangutan",
    "lemur",
    "meerkat",
    "mongoose",
    "porcupine",
    "aardvark",
    "pangolin",
    "hedgehog",
    "badger",
    "otter",
    "beaver",
    "squirrel",
    "chipmunk",
    "rat",
    "mouse",
    "mice",
    "bat",
    "deer",
    "elk",
    "moose",
    "bison",
    "yak",
    "llama",
    "alpaca",
    "kangaroo",
    "koala",
    "panda",
    "sloth",
    "armadillo",
    "raccoon",
    "possum",
    "opossum",
    "skunk",
    "weasel",
    "seal",
    "walrus",
    "whale",
    "dolphin",
    "porpoise",
    "shark",
    "octopus",
    "squid",
    "jellyfish",
    "crab",
    "lobster",
    "shrimp",
    "prawn",
    "starfish",
    "eagle",
    "hawk",
    "falcon",
    "vulture",
    "owl",
    "ostrich",
    "flamingo",
    "pelican",
    "stork",
    "crane bird",
    "heron",
    "ibis",
    "peacock",
    "pigeon",
    "dove",
    "sparrow",
    "crow",
    "raven",
    "seagull",
    "penguin",
    "swan",
    "hornbill",
    "weaver bird",
    "crocodile",
    "alligator",
    "chameleon",
    "frog",
    "toad",
    "salamander",
    "newt",
    "spider",
    "tarantula",
    "scorpion",
    "ant",
    "bee",
    "wasp",
    "hornet",
    "butterfly",
    "butterflies",
    "moth",
    "beetle",
    "cockroach",
    "grasshopper",
    "locust",
    "cricket insect",
    "dragonfly",
    "dragonflies",
    "mosquito",
    "fly insect",
    "housefly",
    "tsetse",
    "termite",
    "centipede",
    "millipede",
    "snail",
    "slug",
    "worm",
    "earthworm",
    "leech",
    "tick insect",
    "flea",
    "louse",
    "lice",
    # Kiswahili
    "mnyama",
    "wanyama",
    "ndege",
    "samaki",
    "mbwa",
    "paka",
    "ng ombe",
    "ngombe",
    "mbuzi",
    "kondoo",
    "nguruwe",
    "farasi",
    "punda",
    "ngamia",
    "kuku",
    "bata",
    "simba",
    "chui",
    "duma",
    "fisi",
    "mbweha",
    "tembo",
    "ndovu",
    "kifaru",
    "kiboko",
    "twiga",
    "pundamilia",
    "nyati",
    "swala",
    "nyani",
    "tumbili",
    "sokwe",
    "panya",
    "popo",
    "nyoka",
    "mamba wa maji",
    "chura",
    "buibui",
    "nyuki",
    "kipepeo",
    "mdudu",
    "wadudu",
    "sungura",
)

DECEASED_TERMS: Tuple[str, ...] = (
    "corpse",
    "cadaver",
    "carcass",
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
    "the dead",
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
    "mzoga",
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


def _normalised_variants(text: str) -> Tuple[str, ...]:
    """
    Return the normalised forms of ``text`` that patterns are matched against.

    Three variants are produced so a single spelling trick does not defeat the
    policy while digits stay intact for the numeric-age patterns:

    1. the plain normalised text (digits preserved — needed for "12 year old");
    2. the same text with leet/homoglyph substitutions reversed ("ch1ld");
    3. variant 2 with spaced-out letter runs rejoined ("c h i l d" -> "child").
    """
    plain = _normalise(text)
    variants = [plain]
    unleet = plain.translate(_LEET_MAP)
    if unleet != plain:
        variants.append(unleet)
    despaced = _SPACED_LETTERS_RE.sub(lambda m: m.group(0).replace(" ", ""), unleet)
    if despaced != unleet:
        variants.append(despaced)
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
_ANIMAL_PATTERN = _build_term_pattern(ANIMAL_TERMS)
_DECEASED_PATTERN = _build_term_pattern(DECEASED_TERMS)

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
    re.compile(rf"(?<![0-9]){_UNDER_18}\s*(?:year|yr)s?\s+of\s+age(?![a-z0-9])"),
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

# Bare death words are ambiguous on their own ("dead leaves"), so they are only
# prohibited when they sit within a few words of a person noun, in either
# order: "dead woman", "the woman is dead", "her husband, recently deceased".
_DEATH_WORD = r"(?:dead|dying|died|dies|lifeless|deceased|expired|slain|murdered|killed|amekufa|amefariki|wamekufa)"
_PERSON_WORD = (
    r"(?:person|people|human|man|men|woman|women|male|female|body|bodies|guy|"
    r"lady|ladies|wife|wives|husband|father|mother|dad|mum|mom|parent|"
    r"grandmother|grandfather|granny|sibling|brother|sister|friend|lover|"
    r"victim|soldier|patient|bride|groom|widow|widower|face|corpse|"
    r"mtu|watu|mwanamke|mwanamume|mume|mke|mzazi|rafiki|mwathirika)"
)
_GAP = r"(?:\s+[a-z0-9]+){0,3}\s+"
_DEATH_PROXIMITY_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(rf"(?<![a-z0-9]){_DEATH_WORD}{_GAP}{_PERSON_WORD}(?![a-z0-9])"),
    re.compile(rf"(?<![a-z0-9]){_PERSON_WORD}{_GAP}{_DEATH_WORD}(?![a-z0-9])"),
)

# Checked in this order; the first match wins.
_CATEGORY_PATTERNS: Tuple[Tuple[str, Tuple[Pattern[str], ...]], ...] = (
    (CATEGORY_MINORS, (_MINOR_PATTERN,) + _MINOR_MODIFIER_PATTERNS + _NUMERIC_AGE_PATTERNS),
    (CATEGORY_ANIMALS, (_ANIMAL_PATTERN,)),
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
    three prohibited subject categories (minors, animals, deceased people) are.
    """

    ERROR_CODE = PROHIBITED_SUBJECT_ERROR_CODE
    CATEGORIES = (CATEGORY_MINORS, CATEGORY_ANIMALS, CATEGORY_DECEASED)

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
