# capp/classes/ai/language_detector.py

import logging
import re
from typing import Optional

from langdetect import detect as langdetect_detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

logger = logging.getLogger(__name__)

# Make langdetect deterministic
DetectorFactory.seed = 0


class LanguageDetector:
    """
    Detect the language of a user message: English (en), Swahili (sw), or Sheng (sh).
    """

    # Common Sheng words / phrases (examples; can be expanded)
    SHENG_MARKERS = {
        "niko",
        "poa",
        "morio",
        "sheng",
        "mambo",
        "vipi",
        "sasa",
        "niaje",
        "noma",
        "bazenga",
        "kiki",
        "kudade",
        "raba",
        "chapo",
        "suru",
        "bonga",
        "nyandua",
        "gwara",
        "dunga",
        "keroro",
        "manze",
        "bash",
        "shuffle",
        "bangi",
        "mzinga",
        "tei",
        "kushoto",
        "hivi",
        "ile",
        "hii",
        "wananibamba",
        "tulia",
        "nika",
        "wera",
        "chali",
        "dem",
        "feeling",
        "mood",
        "vibe",
        "connect",
        "story",
        "maisha",
        # greetings / reactions
        "aje",
        "ukoaje",
        "uko",
        "rada",
        "freshi",
        "safi",
        "fiti",
        "powa",
        "mbele",
        "sawa",
        "asanti",
        "shukran",
        "karibu",
        # people
        "msee",
        "mzae",
        "budaa",
        "mbogi",
        "mabeshte",
        "beshti",
        "mshikaji",
        "brathe",
        "siz",
        "squad",
        "crew",
        "wasee",
        "madame",
        "maduda",
        "mathe",
        "babaa",
        "mtu wangu",
        # money
        "pesa",
        "doe",
        "doh",
        "cash",
        "kitu",
        "kumi",
        "soo",
        "ngiri",
        "luku",
        "mihadarati",
        "mkwanja",
        "pato",
        "ganji",
        "mapene",
        "chapaa",
        # actions
        "enda",
        "kuja",
        "toka",
        "ingia",
        "chill",
        "hang",
        "link",
        "fanya",
        "piga",
        "leta",
        "chukua",
        "kataa",
        "kubali",
        "rush",
        "bounce",
        "kam",
        "endaivi",
        "rudi",
        "tupa",
        # places / streets
        "base",
        "mtaa",
        "hood",
        "ghetto",
        "kanairo",
        "nai",
        "ushago",
        "ploti",
        "kejani",
        "ndani",
        "nje",
        "stage",
        "docks",
        "corner",
        "spot",
        "joint",
        # vibes / descriptions
        "noma sana",
        "safi sana",
        "fiti sana",
        "poa sana",
        "ngori",
        "mbaya",
        "poa kabisa",
        "freshi kabisa",
        "kali",
        "tamu",
        "rahisi",
        "ngumu",
        "tricky",
        "serious",
        "legit",
        "real",
        "fake",
        "design",
        "clutch",
        "rare",
        "normal",
        "basic",
        # social / conversation
        "story gani",
        "sema",
        "ambia",
        "uliza",
        "jibu",
        "ongea",
        "skiza",
        "sikia",
        "elewa",
        "kumbuka",
        "sahau",
        "jua",
        "ambiwa",
        "ambiwa aje",
        # time / frequency
        "leo",
        "jana",
        "kesho",
        "siku",
        "saa",
        "time",
        "saa hii",
        "bado",
        "haraka",
        "polepole",
        "later",
        "mapema",
        "saa ngapi",
        # emphasis / fillers
        "maze",
        "manzee",
        "ati",
        "kwanza",
        "bana",
        "yaani",
        "ebu",
        "ebu ngoja",
        "si",
        "ama",
        "kwani",
        "mbona",
        "aje sasa",
        # lifestyle / vibes
        "turnup",
        "form",
        "energy",
        "hustle",
        "grind",
        "jipange",
        "jipata",
        "jitume",
        "ongeza",
        "punguza",
        "endelea",
        "anza",
        "maliza",
        # transport / movement
        "mat",
        "nduthi",
        "bodaboda",
        "ride",
        "trip",
        "safari",
        "drive",
        "panda",
        "shuka",
        # food / casual
        "chakula",
        "kula",
        "kunywa",
        "chai",
        "soda",
        "nyama",
        "ugali",
        "chapo mbili",
        "smokie",
        "mayai",
        "kuku",
        "kitu kidogo",
        # modern slang crossover
        "lowkey",
        "highkey",
        "vibes tu",
        "no stress",
        "big deal",
        "small thing",
        "full time",
        "part time",
        "side hustle",
        "main thing",
    }

    # Regex pattern for typical Sheng interjections / slang words
    SHENG_PATTERN = r"\b(" + "|".join(re.escape(w) for w in SHENG_MARKERS) + r")\b"

    @classmethod
    def detect(cls, text: str) -> Optional[str]:
        """
        Return language code: 'en', 'sw', 'sh' (Sheng), or None if unknown.
        """
        if not text or not text.strip():
            return None

        # Clean text minimally (remove excessive punctuation, but keep words)
        cleaned = cls._clean_text(text)

        # First attempt: langdetect
        try:
            lang = langdetect_detect(cleaned)
        except LangDetectException:
            logger.debug("langdetect failed for text: %s", cleaned[:50])
            lang = None

        # If langdetect says Swahili, check for Sheng markers
        if lang == "sw":
            if cls._has_sheng_markers(cleaned):
                return "sh"  # Sheng
            return "sw"

        # If langdetect says English, but there are strong Sheng indicators, mark as Sheng
        if lang == "en":
            # If many Sheng words are present, it's likely Sheng
            if cls._has_strong_sheng_indicators(cleaned):
                return "sh"
            return "en"

        # If langdetect gave something else (like 'so' or 'ny'), try to guess from script/markers
        if lang and lang != "sw" and lang != "en":
            # Possibly a rare language; check if Swahili characters dominate
            if cls._likely_swahili(cleaned):
                if cls._has_sheng_markers(cleaned):
                    return "sh"
                return "sw"

        # Fallback: if text contains Sheng markers, assume Sheng
        if cls._has_sheng_markers(cleaned):
            return "sh"

        # Final fallback: return English (most common)
        return "en"

    @classmethod
    def _clean_text(cls, text: str) -> str:
        """Remove URLs, mentions, excessive punctuation, but keep letters and spaces."""
        text = re.sub(r"http\S+", "", text)
        text = re.sub(r"@\w+", "", text)
        text = re.sub(r"[^\w\s]", " ", text)  # replace non-word chars with space
        text = re.sub(r"\s+", " ", text).strip()
        return text.lower()

    @classmethod
    def _has_sheng_markers(cls, text: str) -> bool:
        """Check if text contains any Sheng markers."""
        return bool(re.search(cls.SHENG_PATTERN, text))

    @classmethod
    def _has_strong_sheng_indicators(cls, text: str) -> bool:
        """Check if text contains a significant number of Sheng words (e.g., ≥2)."""
        matches = re.findall(cls.SHENG_PATTERN, text)
        return len(matches) >= 2

    @classmethod
    def _likely_swahili(cls, text: str) -> bool:
        """
        Heuristic: if text contains common Swahili function words,
        it's likely Swahili (or Sheng).
        """
        swahili_words = {
            "na",
            "ni",
            "ya",
            "wa",
            "kama",
            "kwa",
            "hii",
            "hiyo",
            "ni",
            "si",
            "za",
            "la",
            "tu",
            "bila",
            "lakini",
            "pia",
        }
        words = set(text.split())
        return len(words & swahili_words) >= 1
