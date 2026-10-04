# capp/classes/moderation/text_safety_policy.py
"""
Deterministic pre-generation safety screen for user chat text.

Product policy
--------------
This is the chat-text counterpart of ``image_prompt_policy.py``: a
deterministic lexical floor that runs BEFORE the user message is stored,
billed, prompt-built or answered. It does not replace the safety sandwich the
model sees (``prompt_builder.py``) — the model-level policy stays the semantic
backstop for everything the word lists miss.

What it detects, and what happens
---------------------------------
Four classes of decision, with fundamentally different outcomes:

* **care** (``self_harm``, ``gender_violence`` support terms): the message is
  NOT blocked and NOT counted as a violation. The companion answers it
  empathetically under the existing safety-sandwich rules (crisis language +
  helpline). A care event is only recorded so support/context exists; it
  never contributes to a lock.
* **concern** (``weapons_context``, bare ``human_trafficking`` terms): the
  message is NOT blocked, but a low-weight signal is recorded. Prior
  concerns can aggravate a later violation (see ``safety_ledger.py``).
* **violation** (everything else): the message is blocked and a safety event
  is recorded. Severity drives escalation: only CRITICAL categories lock the
  account immediately; HIGH and lower accumulate toward the threshold.

Adult consensual sexual content is ALLOWED and is not screened here — only the
illegal-harm categories below are. Adult taboo fiction is likewise ignored
unless it clearly matches a category. Companion-language endearment ("baby",
"babe") and household mentions of children ("my kids are asleep") are not
minor signals: words that are ubiquitous in benign companion chat are not
treated as evidence of anything (see the 2026-10-04 audit).

Detection approach and limitations
----------------------------------
Detection is deterministic, mirroring ``image_prompt_policy.py``: text is
unicode-normalised, leet-speak and separator obfuscation is folded, and terms
match on word/phrase boundaries so benign substrings ("va*cat*ion",
"*cow*ard") do not trip the lists.

Version 2 of the policy is precision-first, after the 2026-10-04 audit
demonstrated that broad word co-occurrence locks ordinary users:

* **CSAM is tiered.** Only an EXPLICIT underage marker (a stated age under
  18, "underage", "preteen", "toddler", "loli", "shota", "jailbait", ...) next
  to sexual vocabulary is CRITICAL. Direct child-sexual relationship phrases
  ("sex with a child", "child porn", "ngono na mtoto") are HIGH — they block
  the message and count toward the threshold but never lock alone. Kinship
  and endearment words (baby, kid, teen, mtoto as a bare mention) are not
  minor hints at all.
* **Violence requires intent phrasing**, not bare nouns: "murder", "bomb",
  "massacre", "slaughter" as nouns are how people discuss news and history.
  The planning signal is an intent construction ("gonna kill", "open fire",
  "bomb the") aimed at a crowd or location.
* **Trafficking requires an exploitation phrase** ("sell her to", "pimp
  her", "kuuza mtoto"). "Sell her laptop" is commerce, not trafficking, and
  the bare term "human trafficking" is only a concern signal — the words
  people use to ask what it is or how to report it.
* **Educational/discussion framing** (documentary, essay, "read about",
  news, history class) suspends the CSAM age rule, the violence planning
  rules and trafficking phrases: the model-level backstop still refuses
  actual solicitation phrased behind those words.
* **Hyperbolic threats** ("I'm gonna kill my boss lol") are ignored
  entirely: joking is the normal register of companion chat, and logged
  joke-signals would poison later escalation.
* **Survivor and education language about gender-based violence** (domestic
  violence, FGM, forced marriage, ubakaji) is a CARE response, never a
  block. Only perpetrator-intent phrases ("beat my wife", "circumcise her",
  "marry her off") are violations.

A lexical floor cannot recognise paraphrase, novel euphemism, non-English
phrasings beyond the terms listed, or harm implied purely by context. Every
event is reversible by an admin without seeing the message text, and the
account lock is admin-reversible; the ledger can also run in shadow mode
(``SAFETY_LEDGER_MODE``) where nothing is blocked or locked.

Privacy
-------
The policy itself is stateless and stores nothing. The ledger that consumes
its decisions (``safety_ledger.py``) persists category/severity metadata
only — never message text, never embeddings, never LLM-written summaries.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Optional

from capp.classes.moderation.image_prompt_policy import (
    _build_term_pattern,
    _normalised_variants,
)

# ---------------------------------------------------------------------------
# Categories, severities, actions
# ---------------------------------------------------------------------------

# Care: recorded for support context, never blocks, never counts toward a lock.
CATEGORY_SELF_HARM = "self_harm"
CATEGORY_GENDER_VIOLENCE = "gender_violence"

# Concern: recorded, never blocks; aggravates later violations.
CATEGORY_WEAPONS_CONTEXT = "weapons_context"
CATEGORY_TRAFFICKING_TERM = "human_trafficking"

# Violations: block + record.
CATEGORY_CSAM = "csam"
CATEGORY_VIOLENCE_PLANNING = "violence_planning"
CATEGORY_TRAFFICKING = "human_trafficking"
CATEGORY_THREATS = "threats_targeted"
CATEGORY_WEAPONS_INSTRUCTIONS = "weapons_instructions"

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

CLASSIFIER_VERSION = "text-safety-policy/3"


@dataclass(frozen=True)
class SafetyDecision:
    """One classified message. Contains no message text."""

    category: str
    severity: str
    is_care: bool = False
    is_concern: bool = False
    # True when the message ALSO matched self-harm crisis language: the
    # decision is a violation, but the ledger records a care event next to
    # it so support context is not lost (see safety_ledger.screen_message).
    also_care: bool = False
    confidence: float = 1.0
    classifier_version: str = CLASSIFIER_VERSION

    @property
    def is_violation(self) -> bool:
        return not self.is_care and not self.is_concern


# ---------------------------------------------------------------------------
# Term lists (English + Kiswahili)
# ---------------------------------------------------------------------------
# Entries are matched on word/phrase boundaries against normalised text.
# Phrases are preferred over single words everywhere a composite rule exists,
# because the composite — not the bare word — is what keeps benign chat safe.

# Checked FIRST so "I want to kill myself" is care, not violence.
_SELF_HARM_PATTERN = _build_term_pattern(
    (
        "suicide",
        "suicidal",
        "kill myself",
        "killing myself",
        "end my life",
        "ending my life",
        "end it all",
        "self harm",
        "hurt myself",
        "harming myself",
        "cutting myself",
        "cut myself",
        "want to die",
        "wanting to die",
        "wish i was dead",
        "wish i were dead",
        "better off dead",
        "do not want to live",
        "dont want to live",
        "don t want to live",
        "don't want to live",
        "no reason to live",
        "overdose on purpose",
        "nataka kujiua",
        "nitajiua",
        "kujiua",
        "sitaki kuishi",
        "najiumiza",
        "kujidhuru",
        "nimechoka kuishi",
        "nataka kufa",
    )
)

# Sexual vocabulary for the CSAM tiers. Disclosure verbs ("rape", "molest",
# "abuse", "assault") are deliberately NOT here: a past-tense disclosure
# ("my uncle raped me as a child") must reach the companion, which answers it
# as a care conversation, not be blocked as CSAM production. Kiswahili
# "tamaa" (desire of any kind — "nina tamaa ya kupata mtoto", I want to have
# a child) is likewise excluded; only the explicit "tamaa ya ngono" counts.
_SEXUAL_HINT_PATTERN = _build_term_pattern(
    (
        "sex",
        "sexual",
        "sexy",
        "nude",
        "nudes",
        "naked",
        "fuck",
        "fucking",
        "horny",
        "cum",
        "blowjob",
        "anal",
        "oral sex",
        "sleep with",
        "sleeping with",
        "porn",
        "nsfw",
        "erotic",
        "nudity",
        "ngono",
        "uchii",
        "tamaa ya ngono",
        "pornografia",
    )
)

# EXPLICIT content NOUNS: a narrower set than the sexual hints above. The
# English young-minor terms ("little girl", "schoolgirl", ...) only combine
# with THESE, not with generic "sexy", so adult roleplay tropes ("sexy
# schoolgirl costume") pass while "naked pictures of a schoolgirl" does not.
_EXPLICIT_CONTENT_NOUNS = _build_term_pattern(
    (
        "nude",
        "nudes",
        "naked",
        "nudity",
        "porn",
        "pornography",
        "pornografia",
        "pics",
        "pictures",
        "photos",
        "photo",
        "images",
        "video",
        "videos",
        "fuck",
        "fucking",
        "cum",
        "blowjob",
        "anal",
        "erotic",
        "ngono",
        "uchii",
    )
)

# EXPLICIT underage markers. Next to sexual vocabulary these are the only
# CSAM signals rated CRITICAL — none of these words is ordinary endearment or
# household talk. Stated ages are matched as "<n> year old" phrases (singular
# and plural, digit and spelled out, plus the "13yo" abbreviation), not bare
# digits, so "room 17" or "17 sparks" cannot combine with a stray sexual word
# into a lock. Kiswahili ages use the "miaka <n>" construction.
_SPELLED_AGES = (
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
)

_EXPLICIT_MINOR_PATTERN = _build_term_pattern(
    (
        "underage",
        "under age",
        "under 18",
        "under eighteen",
        "preteen",
        "pre teen",
        "pre-teen",
        "toddler",
        "loli",
        "shota",
        "jailbait",
        "chini ya miaka",
    )
    + tuple(f"{age} year old" for age in range(2, 18))
    + tuple(f"{age} years old" for age in range(2, 18))
    + tuple(f"{word} year old" for word in _SPELLED_AGES)
    + tuple(f"{word} years old" for word in _SPELLED_AGES)
    + tuple(f"{age}yo" for age in range(2, 18))
    + tuple(f"miaka {age}" for age in range(2, 11))
)

# Kiswahili young-minor terms ("mtoto mdogo" and friends) are unambiguous
# enough for co-occurrence with sexual vocabulary — unlike English kinship
# words, they are never endearment — but they land in the HIGH tier, not
# CRITICAL: one phrasing never locks an account on its own.
_YOUNG_MINOR_SW_PATTERN = _build_term_pattern(
    (
        "mtoto mdogo",
        "msichana mdogo",
        "mvulana mdogo",
        "binti mdogo",
        "kijana mdogo",
    )
)

# English young-minor terms ("little girl", "schoolgirl", ...) are roleplay
# and costume vocabulary too ("sexy schoolgirl costume"), so they combine
# only with EXPLICIT content nouns, never with generic "sexy".
_YOUNG_MINOR_EN_PATTERN = _build_term_pattern(
    (
        "little girl",
        "little boy",
        "young girl",
        "young boy",
        "schoolgirl",
        "schoolboy",
    )
)

# Direct child-sexual RELATIONSHIP phrases: HIGH, not CRITICAL. These state a
# sexual act with a child without a stated age, so they block and count toward
# the threshold, but a single match never locks the account on its own.
# Kinship words appear only inside these full phrases — "my kids are asleep"
# or "hey baby" can never reach them.
_DIRECT_CSAM_PATTERN = _build_term_pattern(
    (
        "sex with a child",
        "sex with children",
        "sex with a kid",
        "sex with kids",
        "sex with a minor",
        "sex with a teen",
        "sex with a teenager",
        "sex with my child",
        "sex with my kid",
        "sex with my daughter",
        "sex with my son",
        "sex with a little girl",
        "sex with a little boy",
        "sex with a young girl",
        "sex with a young boy",
        "sleep with a child",
        "sleep with my daughter",
        "sleep with my son",
        "molest a child",
        "molest a kid",
        "molest her",
        "molest him",
        "molest them",
        "nudes of a child",
        "nudes of a minor",
        "nudes of children",
        "nudes of a little girl",
        "nudes of a young girl",
        "child nudes",
        "minor nudes",
        "underage nudes",
        "child porn",
        "kid porn",
        "kiddie porn",
        "child pornography",
        "loli porn",
        "shota porn",
        "schoolgirl nudes",
        "schoolgirl porn",
        "schoolboy porn",
        "naked schoolgirl",
        "nude schoolgirl",
        "naked schoolboy",
        "nude schoolboy",
        "child sexual abuse material",
        "csam",
        "ngono na mtoto",
        "ngono na watoto",
        "ngono na mtoto wangu",
        "ngono na binti yangu",
        "ngono na mwana wangu",
        "ngono na msichana mdogo",
        "ngono na mtoto mdogo",
        "picha za ngono za mtoto",
        "ponografia ya watoto",
        "pono ya watoto",
    )
)

# Bare-age STATEMENTS ("she is 15", "she's 15", "ana miaka 15"): combined
# with sexual vocabulary these are HIGH — blocked and counted, never an instant
# lock, because a stated age can appear in a household message ("my daughter
# is 15"). Only the "<verb> <age>" construction counts, so counts and
# quantities ("we had sex 15 times", "15 sparks") never match. "she s"/
# "he s" cover the contractions — the normaliser turns "she's" into
# "she s".
_BARE_AGE_STATEMENT_PATTERN = _build_term_pattern(
    tuple(
        f"{verb} {age}"
        for age in range(11, 18)
        for verb in (
            "is",
            "shes",
            "she s",
            "hes",
            "he s",
            "turned",
            "turns",
            "age",
            "ni",
            "miaka",
        )
    )
)

# Violent intent PHRASES. Bare nouns ("murder", "bomb", "massacre",
# "slaughter") are deliberately absent: those are how people discuss news,
# history, idioms ("the food was the bomb") and exams ("bombing my exams").
# Bare Kiswahili "nitaua" / "nataka kuua" (I will kill / I want to kill) are
# also absent — without an object they cover pest control and idiom just
# like their English counterparts ("nitaua nyoka", kill a snake); the
# person-object forms stay, and the people-plural forms moved to the
# mass-violence rule below.
_VIOLENCE_INTENT_PATTERN = _build_term_pattern(
    (
        "i will kill",
        "i ll kill",
        "i'll kill",
        "i want to kill",
        "wanna kill",
        "going to kill",
        "gonna kill",
        "plan to kill",
        "planning to kill",
        "how to kill",
        "kill him",
        "kill her",
        "kill them",
        "murder them",
        "murder him",
        "murder her",
        "i will stab",
        "want to stab",
        "wanna stab",
        "going to stab",
        "gonna stab",
        "stab my",
        "stab her",
        "stab him",
        "i will poison",
        "want to poison",
        "wanna poison",
        "going to poison",
        "gonna poison",
        "poison my",
        "poison her",
        "poison him",
        "nataka kuua mtu",
        "nitaua mtu",
        "kuua mtu",
        "nitamuua",
        "mpango wa kushambulia",
    )
)

# Mass-violence CONSTRUCTIONS aimed at a place: "bomb the school",
# "shoot up the mall". Generated as "<verb> the <venue>" for concrete
# venues only — "concert" is deliberately excluded from the generated
# objects ("bomb the concert" is not a natural threat shape; "open fire at
# the concert" is, and it is covered by the crowd rule below).
_MASS_VIOLENCE_OBJECT_PATTERN = _build_term_pattern(
    tuple(
        f"{verb} the {place}"
        for verb in ("bomb", "bombing", "attack", "blow up", "shoot up")
        for place in (
            "school",
            "skul",
            "shule",
            "church",
            "mosque",
            "kanisa",
            "msikiti",
            "mall",
            "shopping mall",
            "market",
            "soko",
            "stadium",
            "kiwanja",
            "campus",
            "university",
            "chuo kikuu",
            "hospital",
            "hospitali",
            "bus station",
            "airport",
        )
    )
)

# Crowd-kill phrases ("kill everyone", "open fire"): combined with ANY
# place word — including "concert" and "crowd" — this is the planning
# signal. Includes the Kiswahili people-plural constructions.
_CROWD_KILL_PATTERN = _build_term_pattern(
    (
        "kill everyone",
        "kill them all",
        "murder everyone",
        "open fire",
        "shoot up",
        "kuua watu",
        "nitaua watu",
        "nataka kuua watu",
        "waua watu",
    )
)

# Bomb-placement intent: combined with a place word this is CRITICAL. Bare
# "i will bomb" is deliberately absent — "I will bomb my chemistry exam" is
# idiom; the venue-directed form is caught by the mass-violence objects
# above instead.
_BOMB_INTENT_PATTERN = _build_term_pattern(
    (
        "plant a bomb",
        "place a bomb",
        "weka bomu",
        "nitaweka bomu",
    )
)

# Crowded-place / location targets. Combined with intent above, this is the
# planning signal. Bare "people"/"watu" are NOT targets — "people will love
# this video" is ordinary chat; the target must be a place or a crowd.
_PLACE_TARGET_PATTERN = _build_term_pattern(
    (
        "school",
        "skul",
        "shule",
        "shuleni",
        "church",
        "mosque",
        "kanisa",
        "kanisani",
        "msikiti",
        "msikitini",
        "mall",
        "shopping mall",
        "market",
        "soko",
        "sokoni",
        "stadium",
        "kiwanja",
        "kiwanjani",
        "concert",
        "campus",
        "university",
        "chuo kikuu",
        "hospital",
        "hospitali",
        "bus station",
        "airport",
        "ukiwanja wa ndege",
        "crowd",
        "crowded place",
    )
)

# Individual-person targets. Combined with intent: a targeted threat.
_PERSON_TARGET_PATTERN = _build_term_pattern(
    (
        "my wife",
        "my husband",
        "my girlfriend",
        "my boyfriend",
        "my partner",
        "my boss",
        "my teacher",
        "my neighbor",
        "my neighbour",
        "my friend",
        "mke wangu",
        "mume wangu",
        "mpenzi wangu",
        "rafiki yangu",
        "bwana harusi",
        "bibiharusi",
    )
)

# Explicit planning phrases that are violations on their own (HIGH: a
# quoted phrase in a news discussion is common, so it takes the ladder, not
# an instant lock).
_PLANNING_PHRASE_PATTERN = _build_term_pattern(
    (
        "school shooting",
        "mass shooting",
        "shooting spree",
        "attack plan",
        "kill plan",
        "bomb plot",
        "terror attack",
        "suicide bombing",
        "suicide vest",
        "shambulio la panga",
        "mpango wa shambulio",
    )
)

# Rape threats: intent constructions only. Past-tense disclosures ("she was
# raped by") never match these.
_RAPE_THREAT_PATTERN = _build_term_pattern(
    (
        "i will rape",
        "i want to rape",
        "going to rape",
        "gonna rape",
        "planning to rape",
        "nitakubaka",
    )
)

# Weapon construction instructions that NEVER suspend: instructional
# phrasings ("how to ...") and named devices. Bare "make a bomb" style
# phrases live in _BARE_BOMB_PHRASE_PATTERN below and can be suspended by
# a food/cooking context ("I can make a bomb jollof rice").
_WEAPONS_INSTRUCTIONS_PATTERN = _build_term_pattern(
    (
        "how to make a bomb",
        "how to build a bomb",
        "how to make explosives",
        "pipe bomb",
        "pressure cooker bomb",
        "how to make a gun",
        "how to build a gun",
        "build a gun",
        "3d printed gun",
        "3 d printed gun",
        "print a gun",
        "print a firearm",
        "how to make napalm",
        "how to make a molotov",
        "molotov cocktail",
        "molotov cocktail recipe",
        "homemade gun",
        "homemade bomb",
        "improvised explosive",
        "ied",
        "how to make poison",
        "how to make ricin",
        "ghost gun",
        "how to make a silencer",
        "jinsi ya kutengeneza bomu",
        "kutengeneza bomu",
        "bomu la nyumbani",
        "jinsi ya kutengeneza silaha",
        "kutengeneza bastola",
    )
)

# Bare bomb-building phrases: violations unless the message is clearly about
# food ("make a bomb jollof rice"). "kitchen" is deliberately NOT a food
# marker — "how to make a bomb in the kitchen" stays a violation (and its
# instructional form matches the pattern above regardless).
_BARE_BOMB_PHRASE_PATTERN = _build_term_pattern(
    (
        "make a bomb",
        "build a bomb",
        "making a bomb",
        "building a bomb",
        "make explosives",
        "build explosives",
        "making explosives",
        "building explosives",
    )
)

# Space-free variants of the core weapon phrases, matched against the
# despaced normalisation variant: "m a k e  a  b o m b" normalises to
# "makeabomb", which the whitespace-aware patterns above can never match.
# Word boundaries still apply, so "makeabomb" inside ordinary words cannot
# trigger it.
_WEAPONS_COMPACT_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(
        re.escape(phrase.replace(" ", ""))
        for phrase in (
            "make a bomb",
            "build a bomb",
            "making a bomb",
            "how to make a bomb",
            "how to build a bomb",
            "make explosives",
            "build explosives",
            "pipe bomb",
            "pressure cooker bomb",
            "homemade bomb",
            "improvised explosive",
            "molotov cocktail",
            "kutengeneza bomu",
        )
    )
    + r")(?![a-z0-9])"
)

# Food / cooking context markers: suspend the bare bomb-building phrases
# only. Multiword dishes, meals and cooking verbs.
_FOOD_CONTEXT_PATTERN = _build_term_pattern(
    (
        "jollof",
        "rice",
        "stew",
        "ugali",
        "chapati",
        "chapo",
        "pilau",
        "biryani",
        "pizza",
        "burger",
        "pasta",
        "salad",
        "soup",
        "cake",
        "cookies",
        "pancake",
        "barbecue",
        "bbq",
        "nyama choma",
        "dinner",
        "lunch",
        "breakfast",
        "meal",
        "dish",
        "recipe",
        "cook",
        "cooking",
        "bake",
        "baking",
        "tasty",
        "delicious",
        "yummy",
        "flavor",
        "flavour",
    )
)

# Toy / game markers: "where can I buy bullets for my nerf gun" is a
# shopping question, not a weapons-interest signal worth logging.
_TOY_WEAPON_PATTERN = _build_term_pattern(
    (
        "nerf",
        "airsoft",
        "paintball",
        "water gun",
        "toy gun",
        "toy",
        "video game",
        "game",
    )
)

_WEAPONS_CONTEXT_PATTERN = _build_term_pattern(
    (
        "buy a gun",
        "buy a firearm",
        "buy a weapon",
        "buying a gun",
        "buy guns",
        "where can i get a gun",
        "where can i buy a gun",
        "where to buy a gun",
        "get a gun",
        "get a firearm",
        "get a weapon",
        "buy ammunition",
        "buy bullets",
        "buy ammo",
        "gun license",
        "illegal gun",
        "black market gun",
        "gun on the black market",
        "no license gun",
        "unlicensed gun",
        "bunduki",
        "silaha",
        "bastola",
        "panga",
        "nunua bunduki",
        "kupata bunduki",
        "nunua silaha",
    )
)

# Direct exploitation phrases: CRITICAL trafficking violations. "sell her"
# and "sell him" alone are deliberately absent — selling laptops, cars and
# ideas is commerce — and so are the BARE group forms ("sell girls", "buy a
# girl", "deliver girls"): those also cover market commerce ("I sell girls
# shoes", "I deliver girls school uniforms") and gifts ("buy a girl a
# drink"). The group forms require a movement/exploitation preposition
# ("sell girls to", "buy a girl for"), and possessives require "to"/"for"
# after the kinship word ("sell my daughter to"), so possessive commerce
# ("sell my son's old bicycle") stays untouched. Passport retention is the
# coercion hallmark of recruitment.
_TRAFFICKING_PATTERN = _build_term_pattern(
    (
        "trafficking her",
        "trafficking him",
        "traffic her",
        "traffic him",
        "traffick her",
        "traffick him",
        "traffick girls",
        "traffick boys",
        "traffick children",
        "trafficking girls",
        "trafficking boys",
        "trafficking children",
        "sell her to",
        "sell him to",
        "sell them to",
        "sell the girl",
        "sell girls to",
        "sell girls for",
        "sell boys to",
        "sell my daughter to",
        "sell my daughter for",
        "sell my son to",
        "sell my son for",
        "sell my child to",
        "sell my child for",
        "buy a girl for",
        "buy a girl from",
        "buy girls for",
        "buy girls from",
        "buy a boy for",
        "buy a boy from",
        "buy boys for",
        "deliver girls to",
        "deliver girls for",
        "deliver boys to",
        "deliver boys for",
        "pimp her",
        "pimp him",
        "pass her around",
        "pass him around",
        "share her for money",
        "share him for money",
        "sex slave",
        "forced prostitution",
        "forced escort",
        "bring girls for men",
        "bring boys for men",
        "keep their passports",
        "take their passports",
        "hold their passports",
        "confiscate their passports",
        "withhold their passports",
        "take her passport",
        "take his passport",
        "keep her passport",
        "keep his passport",
        "confiscate her passport",
        "confiscate his passport",
        "msafirishe",
        "uuze msichana",
        "kuuza mtoto",
        "kuuza watoto",
        "kumsafirisha kwa wanaume",
        "umalaya wa kulazimishwa",
        "mtumwa wa ngono",
        "kumpitisha kwa wanaume",
    )
)

# Bare trafficking terms: a CONCERN, never a block. These are the words of
# questions ("is it human trafficking when ...?"), reporting ("how do I
# report human trafficking") and education — which must pass — and they are
# rare enough in benign companion chat that keeping them as a logged,
# low-weight signal is safe.
_TRAFFICKING_TERM_PATTERN = _build_term_pattern(
    (
        "human trafficking",
        "sex trafficking",
    )
)

# Gender-based violence support vocabulary: CARE. Survivors ("I am a victim
# of domestic violence"), helpers ("my sister was forced marriage at 15,
# I want to help her") and students ("my essay is about FGM") must reach the
# companion and get a supportive answer, never a block.
_GENDER_CARE_PATTERN = _build_term_pattern(
    (
        "fgm",
        "female genital mutilation",
        "female circumcision",
        "forced marriage",
        "domestic violence",
        "intimate partner violence",
        "acid attack",
        "honour killing",
        "honor killing",
        "clitoris removed",
        "clitoris cut",
        "kata uke wangu",
        "ndoa ya lazima",
        "olewa kwa lazima",
        "kufungwa ndoa kwa lazima",
        "ubakaji",
        "ukatili wa kijinsia",
        "ukeketaji",
        "tohara ya msichana",
    )
)

# Perpetrator-intent phrases: violations (HIGH). These describe doing the
# harm, not surviving or studying it.
_GENDER_VIOLENCE_PATTERN = _build_term_pattern(
    (
        "circumcise her",
        "circumcise my",
        "cut her clit",
        "cut off her clit",
        "marry her off",
        "marry off my",
        "beat my wife",
        "beat your wife",
        "beat my husband",
        "beat my girlfriend",
        "beat my partner",
        "mpige mke",
        "kumkeketa",
        "kukata uke",
        "kumbaka",
    )
)

# Educational / discussion framing. When present, the CSAM age rule, the
# violence planning rules and trafficking phrases are suspended: people
# discussing documentaries, essays, news and history must pass, and the
# model-level safety sandwich still refuses anything actually solicited
# behind those words. Generic question openers ("is it", "what is") are
# deliberately NOT framing markers — they are trivially prepended to a
# solicitation. First-person reading/writing markers ("i am writing",
# "i read about", "i watched") were REMOVED: audit 2 showed they suspend
# real solicitations ("I am writing a story: sex with a child"), while the
# noun frames below (documentary, essay, article, ...) already cover the
# benign cases that need them.
_EDUCATIONAL_FRAME_PATTERN = _build_term_pattern(
    (
        "documentary",
        "essay about",
        "essay on",
        "school essay",
        "article about",
        "article on",
        "the news",
        "in the news",
        "news about",
        "i studied",
        "studying",
        "study about",
        "history class",
        "in history",
        "history of",
        "how do i report",
        "how to report",
        "i learned about",
        "learn about",
        "research on",
        "researching",
        "lecture",
        "class about",
        "makala",
    )
)

# Parenting / legal-question context. A marker here suspends ONLY the
# age-based CSAM tiers (a stated age or young-minor term next to sexual
# vocabulary) — never the direct phrases ("sex with a child" still blocks).
# Parents asking how to talk to their children about sex, and legal
# questions about ages of consent, must pass. The markers are multiword on
# purpose: a broad single word ("asked", "teach") would let a solicitation
# hide behind it — "explain how to fuck a 12 year old" matches none of
# these and still blocks.
_PARENTING_CONTEXT_PATTERN = _build_term_pattern(
    (
        "explain sex",
        "explained sex",
        "explaining sex",
        "explain to my",
        "explain it to",
        "asked me what",
        "asked me about",
        "asked about sex",
        "asked what sex",
        "asked her about",
        "asked him about",
        "walked in while",
        "walked in on",
        "is it legal",
        "legal for",
        "age of consent",
        "sex education",
        "sex ed",
        "puberty",
        "birds and the bees",
        "talk to her",
        "talk to him",
        "talk to them",
        "talk to my",
        "talk with her",
        "talk with him",
        "talk about sex",
    )
)

# Joking markers. A threat phrased as a joke ("I'm gonna kill my boss lol")
# is the normal register of companion chat; logging it as a concern would
# poison later escalation, so it is ignored entirely.
_HYPERBOLE_PATTERN = _build_term_pattern(
    (
        "lol",
        "lmao",
        "haha",
        "hehe",
        "hihi",
        "hahaha",
        "jk",
        "just kidding",
        "just joking",
        "joking",
        "kidding",
    )
)


def _matches(pattern, variants) -> bool:
    return any(pattern.search(variant) for variant in variants)


class TextSafetyPolicy:
    """
    Stateless classifier for user chat text.

    ``evaluate`` returns at most one decision per message, checked in a fixed
    order. VIOLATIONS are checked first (audit 2, finding M1): when a message
    combines harm-to-others language with self-harm crisis language, the
    violation wins and ``also_care`` preserves the crisis signal for the
    ledger — a suicidal user planning a school attack still gets blocked,
    and the account still shows the care event. Pure crisis language
    ("I want to kill myself") is still a care conversation, never violence.
    """

    @staticmethod
    def evaluate(text: Optional[str]) -> Optional[SafetyDecision]:
        """Classify ``text``; ``None`` when nothing matches (the normal case)."""
        if not text or not text.strip():
            return None
        variants = _normalised_variants(text)
        educational = _matches(_EDUCATIONAL_FRAME_PATTERN, variants)
        parenting = _matches(_PARENTING_CONTEXT_PATTERN, variants)
        food = _matches(_FOOD_CONTEXT_PATTERN, variants)
        self_harm = _matches(_SELF_HARM_PATTERN, variants)
        gender_care = _matches(_GENDER_CARE_PATTERN, variants)

        # 1) CSAM, tiered:
        #    CRITICAL — explicit underage marker + sexual vocabulary. This is
        #    the only CSAM signal that can lock an account on its own, and it
        #    requires a stated age or an unambiguous underage term, never a
        #    kinship word or endearment. Suspended behind educational framing
        #    and behind parenting/legal-question context.
        #    HIGH — direct child-sexual relationship phrases ("sex with a
        #    child") never suspend behind parenting context; the AGE-based
        #    rules (bare age, young-minor term + sexual vocabulary) do.
        if (
            _matches(_EXPLICIT_MINOR_PATTERN, variants)
            and _matches(_SEXUAL_HINT_PATTERN, variants)
            and not educational
            and not parenting
        ):
            return SafetyDecision(category=CATEGORY_CSAM, severity=SEVERITY_CRITICAL, also_care=self_harm)
        age_rule = (
            (_matches(_BARE_AGE_STATEMENT_PATTERN, variants) and _matches(_SEXUAL_HINT_PATTERN, variants))
            or (_matches(_YOUNG_MINOR_SW_PATTERN, variants) and _matches(_SEXUAL_HINT_PATTERN, variants))
            or (_matches(_YOUNG_MINOR_EN_PATTERN, variants) and _matches(_EXPLICIT_CONTENT_NOUNS, variants))
        )
        if (_matches(_DIRECT_CSAM_PATTERN, variants) or (age_rule and not parenting)) and not educational:
            return SafetyDecision(category=CATEGORY_CSAM, severity=SEVERITY_HIGH, also_care=self_harm)

        # 2) Violence planning: CRITICAL requires a place-directed mass
        #    construction — "<verb> the <venue>", a crowd-kill phrase with
        #    any place word, or bomb placement aimed at a place. Idiom
        #    ("gonna kill it at the concert", "bomb the other team at the
        #    stadium") matches none of these shapes. Suspended behind
        #    educational framing.
        if not educational:
            if (
                _matches(_MASS_VIOLENCE_OBJECT_PATTERN, variants)
                or (_matches(_CROWD_KILL_PATTERN, variants) and _matches(_PLACE_TARGET_PATTERN, variants))
                or (_matches(_BOMB_INTENT_PATTERN, variants) and _matches(_PLACE_TARGET_PATTERN, variants))
            ):
                return SafetyDecision(
                    category=CATEGORY_VIOLENCE_PLANNING,
                    severity=SEVERITY_CRITICAL,
                    also_care=self_harm,
                )
            if _matches(_PLANNING_PHRASE_PATTERN, variants):
                return SafetyDecision(
                    category=CATEGORY_VIOLENCE_PLANNING,
                    severity=SEVERITY_HIGH,
                    also_care=self_harm,
                )

        # 3) Trafficking: a direct exploitation phrase is CRITICAL (also
        #    suspended behind educational framing); the bare term is only a
        #    logged concern — the register of questions and reporting.
        if not educational and _matches(_TRAFFICKING_PATTERN, variants):
            return SafetyDecision(category=CATEGORY_TRAFFICKING, severity=SEVERITY_CRITICAL, also_care=self_harm)
        if _matches(_TRAFFICKING_TERM_PATTERN, variants):
            return SafetyDecision(
                category=CATEGORY_TRAFFICKING_TERM,
                severity=SEVERITY_LOW,
                is_concern=True,
            )

        # 4) Gender-based violence: perpetrator-intent phrases only — checked
        #    BEFORE the care tier so "how to circumcise her at home fgm" is a
        #    violation, not a care conversation (audit 2, finding H2).
        if _matches(_GENDER_VIOLENCE_PATTERN, variants):
            return SafetyDecision(category=CATEGORY_GENDER_VIOLENCE, severity=SEVERITY_HIGH, also_care=self_harm)

        # 5) Targeted threats: intent + person target, unless it is phrased
        #    as a joke. Rape threats are intent constructions alone.
        if _matches(_RAPE_THREAT_PATTERN, variants):
            return SafetyDecision(category=CATEGORY_THREATS, severity=SEVERITY_HIGH, also_care=self_harm)
        if _matches(_VIOLENCE_INTENT_PATTERN, variants) and _matches(_PERSON_TARGET_PATTERN, variants):
            if not _matches(_HYPERBOLE_PATTERN, variants):
                return SafetyDecision(category=CATEGORY_THREATS, severity=SEVERITY_HIGH, also_care=self_harm)

        # 6) Weapon construction instructions: instructional phrasings and
        #    named devices never suspend; bare "make a bomb" phrases are
        #    suspended by a food context; spaced-out letter runs match the
        #    compact variants.
        if (
            _matches(_WEAPONS_INSTRUCTIONS_PATTERN, variants)
            or _matches(_WEAPONS_COMPACT_PATTERN, variants)
            or (_matches(_BARE_BOMB_PHRASE_PATTERN, variants) and not food)
        ):
            return SafetyDecision(category=CATEGORY_WEAPONS_INSTRUCTIONS, severity=SEVERITY_HIGH, also_care=self_harm)

        # 7) Weapon acquisition concern: logged only, aggravates later —
        #    unless it is about toys or games.
        if _matches(_WEAPONS_CONTEXT_PATTERN, variants) and not _matches(_TOY_WEAPON_PATTERN, variants):
            return SafetyDecision(
                category=CATEGORY_WEAPONS_CONTEXT,
                severity=SEVERITY_LOW,
                is_concern=True,
            )

        # 8) Care: a pure self-harm or survivor message is a care
        #    conversation, never a block.
        if self_harm:
            return SafetyDecision(
                category=CATEGORY_SELF_HARM,
                severity=SEVERITY_HIGH,
                is_care=True,
            )
        if gender_care:
            return SafetyDecision(
                category=CATEGORY_GENDER_VIOLENCE,
                severity=SEVERITY_HIGH,
                is_care=True,
            )

        return None

    @staticmethod
    def evaluate_guest(text: Optional[str]) -> bool:
        """``True`` when a guest message must be blocked.

        Guests have no account to lock, so the ledger does not apply; the
        deterministic floor still blocks outright violations. Care and
        concern messages pass through exactly as for authenticated users.
        """
        decision = TextSafetyPolicy.evaluate(text)
        return decision is not None and decision.is_violation
