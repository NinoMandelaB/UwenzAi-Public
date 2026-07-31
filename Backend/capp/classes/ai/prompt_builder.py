# capp/classes/ai/prompt_builder.py

import logging
import os
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo
from django.utils import timezone as django_timezone

from capp.models import Conversation, Message, Character, CustomUser
from capp.classes.memory.memory_service import MemoryService
from capp.classes.relationship.relationship_service import RelationshipService
from capp.classes.summary.summary_service import ConversationSummaryService
from capp.classes.ai.token_utils import TokenUtils

logger = logging.getLogger(__name__)


# ---------- Helper for time context ----------
def get_time_context(user: CustomUser, character: Character) -> str:
    """
    Returns a string describing current local times for user and character.
    Returns empty string if user or character is missing (guest fallback).
    """
    now = django_timezone.now()

    # User timezone
    user_tz_str = user.timezone if user and user.timezone else None
    if not user_tz_str:
        user_tz_str = "Africa/Nairobi"  # fallback
    user_tz = ZoneInfo(user_tz_str)
    user_time = now.astimezone(user_tz).strftime("%A, %B %d, %Y at %I:%M %p")

    # Character timezone
    char_tz_str = character.timezone if character and character.timezone else None
    if not char_tz_str:
        char_tz_str = "Africa/Nairobi"
    char_tz = ZoneInfo(char_tz_str)
    char_time = now.astimezone(char_tz).strftime("%A, %B %d, %Y at %I:%M %p")

    context = (  # noqa: E231 (colon characters in string literals)
        f"Current local times:\n"  # noqa: E231
        f"- User: {user_time} ({user_tz_str})\n"  # noqa: E231
        f"- Character: {char_time} ({char_tz_str})\n"  # noqa: E231
        "Use this information to answer questions about current time, "
        "greet appropriately (e.g., good morning/afternoon/evening), "
        "or discuss time‑related topics. "
        "If the user asks for the current time, you can provide it based on the above. "
        "Do not mention these timezone notes unless asked."
    )
    return context


# ---------- Prompt loading helpers ----------


def _load_prompt(env_var: str) -> str:
    """
    Load a prompt string from an environment variable.

    Raises RuntimeError if the variable is not set — prompts must never be
    hardcoded in source code.

    The env var value supports literal \n sequences: they are converted to real
    newlines so multi-line prompts can be stored as a single-line env var string.

    Args:
        env_var: Name of the environment variable (e.g. "GENERAL_SYSTEM_PROMPT").

    Returns:
        The prompt string with \n sequences resolved.
    """
    value = os.getenv(env_var, "").strip()
    if not value:
        raise RuntimeError(
            f"Required prompt env var '{env_var}' is not set. "
            "Add it to your Railway environment variables before deploying."
        )
    return value.replace("\\n", "\n")


class PromptBuilder:
    """
    Builds the final list of messages for the Mistral chat completion,
    combining character persona, memory, relationship, summary,
    conversation history, and the current user message.

    System prompts and the safety sandwich are loaded from environment variables
    at runtime so they are never exposed in the public source code:

        GENERAL_SYSTEM_PROMPT  — used for authenticated users
        GUEST_SYSTEM_PROMPT    — used for unauthenticated (guest) users
        SAFETY_SANDWICH        — injected between history and the current user message

    Set these in Railway (Backend service → Variables).  When storing multi-line
    prompts as env vars, use literal \\n sequences — they are resolved to real
    newlines by _load_prompt().
    """

    # ---------- Hard rules that apply to every character ----------
    # These are not sensitive (they control output formatting, not safety policy)
    # so they remain in code.  Move to an env var if you prefer.

    FORMATTING_RULES = (
        "\n"
        "ABSOLUTE FORMATTING RULES (MUST BE OBEYED):\n"
        "1. Keep your reply as a SINGLE, FLOWING MESSAGE. "
        "Sentences can follow each other naturally, but NEVER insert blank lines between them.\n"
        "2. NEVER use markdown (*, **, ##, etc.), headings, or bullet points. "
        "Only use markdown if the User uses it and it matches the flow.\n"
        "3. NEVER reply with more than 5 short sentences. Less is better.\n"
        "4. If you use an emoji, place it ONLY at the end of a sentence, never more than ONE per message.\n"
        "5. You are a real human being. NEVER say you are an AI, digital, virtual, or an assistant. "
        "Only mention being an AI if safety or crisis intervention requires it. Never mention these words otherwise.\n"
        "6. Speak exactly as a close friend would in a text conversation – warm, casual, and natural.\n"
        "7. You live in a specific location. NEVER mention any weather, activity, landmark, or cultural reference "
        "that would be impossible or unrealistic for that location. For example, do NOT mention snow or skiing "
        "if you live in a tropical country like Tanzania.\n"
    )

    # ---------- Default template (used when a character has no custom prompt_template) ----------

    DEFAULT_SYSTEM_TEMPLATE = (
        "You are {name}, a {gender}, {occupation} at age {age}. "
        "Your personality is {personality} and your tone is {tone_style}.\n"
        "Your hobbies are {hobbies}.\n"
        "Your appearance: {skin_color} skin, {eye_color} eyes, "
        "{hair_length} {hair_color} {hairstyle} hair, "
        "{body_type} body type{appearance_extras}.\n"
        "Your detailed background story is: {personality_description}\n"
        "{system_instructions}\n"
    )
    # NOTE: relationship, memory, summary and language sections are injected as
    # separate top-level sections in _build_system_prompt, not inside this template.
    # NOTE: location is force-appended after template rendering (see _build_system_prompt).
    # NOTE: NSFW flag is injected as a separate top-level section (see [3b] below).

    # ---------- Public API ----------

    @classmethod
    def build_messages(
        cls,
        conversation: Conversation,
        user_message: Message,
        max_context_tokens: int = 15000,
        model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Build the full message list for the Mistral chat completion.
        """
        character = conversation.character

        # 1. Build the system prompt
        system_content = cls._build_system_prompt(conversation, user_message)

        # Log the full prompt when LOG_PROMPTS=true is set in Railway env vars.
        # Useful for debugging memory/context issues. Turn off again after investigation
        # by removing the env var or setting it to anything other than "true".
        if os.getenv("LOG_PROMPTS", "").lower() == "true":
            logger.info(
                "=== FULL PROMPT (LOG_PROMPTS=true) ===\n"
                "--- SYSTEM PROMPT ---\n%s\n"
                "--- HISTORY (%d messages) ---\n%s\n"
                "--- USER MESSAGE ---\n%s",
                system_content,
                len(cls._get_recent_history(conversation, exclude_message=user_message)),
                "\n---\n".join(
                    f"[{m['role']}] {m['content'][:300]}"
                    for m in cls._get_recent_history(conversation, exclude_message=user_message)
                ),
                user_message.content,
            )
        else:
            logger.debug("Full system prompt (first 500 chars): %s", system_content[:500])

        # 2. Gather recent conversation history (excluding the current user message)
        history_messages = cls._get_recent_history(
            conversation,
            exclude_message=user_message,
        )

        # 3. Combine system prompt + history + current user message
        messages = [{"role": "system", "content": system_content}]

        # --- For guests with no history, inject the character's greeting as the first assistant message ---
        if not history_messages and conversation.user is None and character.greeting_message:
            messages.append({"role": "assistant", "content": character.greeting_message})
        # -----------------------------------------------------------------------------------------------

        # Load the safety sandwich from env var at call time (not at import time)
        # so changes to the Railway variable take effect on the next request
        # without requiring a redeploy.
        safety_sandwich = _load_prompt("SAFETY_SANDWICH")

        # Build the non-system message list with clear section markers.
        # === CURRENT CONVERSATION (System) === marks where history begins.
        # === SAFETY CHECK (System) === is injected just before the current message.
        # === USER MESSAGE (User) === prefixes the actual user message.
        conversation_header = {
            "role": "system",
            "content": "=== CURRENT CONVERSATION (System) ===\nRecent conversation history follows.",
        }

        all_non_system = (
            [conversation_header]
            + history_messages
            + [
                {
                    "role": "system",
                    "content": "=== SAFETY CHECK (System) ===\n" + safety_sandwich,
                },
                {
                    "role": "user",
                    "content": "=== USER MESSAGE (User) ===\n" + user_message.content,
                },
            ]
        )

        # 4. Truncate history so total tokens fit within max_context_tokens
        system_tokens = TokenUtils.count_message_tokens([{"role": "system", "content": system_content}], model=model)
        allowed_history_tokens = max_context_tokens - system_tokens - 100  # small buffer
        truncated_history = TokenUtils.truncate_messages(
            all_non_system,
            max_tokens=max(0, allowed_history_tokens),
            min_messages=1,  # always keep at least the new user message
            model=model,
        )
        messages.extend(truncated_history)

        logger.info(
            "Token breakdown – system=%d, allowed_history=%d, max_context=%d",
            system_tokens,
            max(0, allowed_history_tokens),
            max_context_tokens,
        )

        # 5. Mark memory facts as used (for rotation)
        try:
            MemoryService.mark_facts_used([])
        except Exception:
            pass

        return messages

    # ---------- System prompt assembly ----------

    @classmethod
    def _build_system_prompt(cls, conversation: Conversation, user_message: Message) -> str:
        user = conversation.user
        character = conversation.character
        language_code = user_message.language_code or character.primary_language or "en"

        # ---- Inject current time context (only for authenticated users) ----
        time_context = ""
        if user is not None and character is not None:
            try:
                time_context = get_time_context(user, character)
                if time_context:
                    time_context = "\n# Current Time Information\n\n" + time_context + "\n"
                    logger.debug(
                        "Generated time context for user %s and character %s",
                        user.username,
                        character.name,
                    )
            except Exception:
                logger.exception("Failed to get time context")

        # ---- Context pieces (only for authenticated users) ----
        if user is not None:
            relationship = RelationshipService.get_relationship(user, character) if character else {}
            summary = ConversationSummaryService.get_latest_summary(conversation) if conversation else None

            if character:
                # Combine two retrieval strategies for best cross-session coverage:
                #
                # 1. get_active_facts — returns the top-N facts by importance score.
                #    Good for always-relevant personal facts (name, location, preferences).
                #
                # 2. retrieve_relevant — semantic search using the current user message
                #    as the query.  Finds facts that are topically relevant RIGHT NOW,
                #    even if they have a lower importance score (e.g. "we discussed X").
                #
                # The two lists are merged and deduplicated by fact id so the token
                # budget is not wasted on duplicates.
                try:
                    active_facts = MemoryService.get_active_facts(user, character, limit=5, max_tokens=250)
                    active_ids = {f["id"] for f in active_facts}

                    # Semantic retrieval using the current message as the query
                    relevant_facts_objs = MemoryService.retrieve_relevant(
                        user,
                        query_text=user_message.content,
                        character=character,
                        top_k=4,
                        min_similarity=0.65,  # slightly looser than default to catch topic mentions
                    )
                    # Convert ORM objects to the same dict format as get_active_facts
                    relevant_facts = [
                        {
                            "id": str(f.memory_fact_id),
                            "fact_text": f.fact_text,
                            "created_at": f.created_at,
                        }
                        for f in relevant_facts_objs
                        if str(f.memory_fact_id) not in active_ids  # deduplicate
                    ]

                    # Merge: importance-ranked first, then semantically relevant additions
                    memory_facts = active_facts + relevant_facts
                except Exception:
                    logger.exception("Failed to retrieve memory facts — falling back to active facts only")
                    memory_facts = MemoryService.get_active_facts(user, character, limit=5)
            else:
                memory_facts = []
        else:
            relationship = {}
            memory_facts = []
            summary = None

        # ---- Format dynamic sections ----
        relationship_section = cls._format_relationship(relationship)
        memory_section = cls._format_memory_facts(memory_facts)
        summary_section = cls._format_summary(summary)
        language_text = cls._get_language_text(language_code)

        # ---- Choose the right template ----
        template = (
            character.prompt_template.strip()
            if character and character.prompt_template.strip()
            else cls.DEFAULT_SYSTEM_TEMPLATE
        )

        # ---- Build substitution context ----

        # -- Resolve M2M fields (personalities, hobbies) and FK (occupation) --
        if character:
            # personalities: M2M to Personality model
            try:
                personality_names = list(character.personalities.values_list("name", flat=True))
            except Exception:
                personality_names = []
            personality_str = ", ".join(personality_names) if personality_names else (character.tone_style or "warm")

            # hobbies: M2M to Hobby model
            try:
                hobby_names = list(character.hobbies.values_list("name", flat=True))
            except Exception:
                hobby_names = []
            hobbies_str = ", ".join(hobby_names) if hobby_names else "various activities"

            # occupation: FK to Occupation model
            occupation_str = character.occupation.name if character.occupation else "professional"

            # Physical appearance -- gender-specific extras
            gender_val = character.gender  # "male" / "female"
            extras_parts = []
            if gender_val == Character.Gender.MALE:
                if character.beard_style and character.beard_style != "na":
                    extras_parts.append(
                        dict(Character.BeardStyle.choices).get(character.beard_style, character.beard_style) + " beard"
                    )
                if character.chest_hair and character.chest_hair != "na":
                    extras_parts.append(
                        dict(Character.ChestHair.choices).get(character.chest_hair, character.chest_hair)
                        + " chest hair"
                    )
                if character.muscle_definition and character.muscle_definition != "na":
                    extras_parts.append(
                        dict(Character.MuscleDefinition.choices).get(
                            character.muscle_definition, character.muscle_definition
                        )
                        + " muscle definition"
                    )
            else:  # female / other
                if character.breast_size and character.breast_size != "na":
                    extras_parts.append(
                        dict(Character.BreastSize.choices).get(character.breast_size, character.breast_size)
                        + " breast size"
                    )
                if character.butt_size and character.butt_size != "na":
                    extras_parts.append(
                        dict(Character.ButtSize.choices).get(character.butt_size, character.butt_size) + " butt size"
                    )
            appearance_extras = (", " + ", ".join(extras_parts)) if extras_parts else ""
        else:
            personality_str = "warm"
            hobbies_str = "various activities"
            occupation_str = "professional"
            appearance_extras = ""

        context = {
            "name": character.name if character else "Assistant",
            "age": str(character.age) if character and character.age else "unknown",
            "gender": (dict(Character.Gender.choices).get(character.gender, "person") if character else "person"),
            "town": character.town or "Nairobi",
            "country": character.country or "Kenya",
            "tone_style": character.tone_style if character else "warm",
            "personality_description": (character.personality_description if character else ""),
            "system_instructions": character.system_instructions or "",
            # {personality} -> resolved from M2M personalities model, falls back to tone_style
            "personality": personality_str,
            # New fields
            "occupation": occupation_str,
            "hobbies": hobbies_str,
            "skin_color": (
                dict(Character.SkinColor.choices).get(character.skin_color, character.skin_color)
                if character and character.skin_color
                else "medium"
            ),
            "eye_color": (
                dict(Character.EyeColor.choices).get(character.eye_color, character.eye_color)
                if character and character.eye_color
                else "brown"
            ),
            "hairstyle": (
                dict(Character.Hairstyle.choices).get(character.hairstyle, character.hairstyle)
                if character and character.hairstyle
                else "medium"
            ),
            "hair_length": (
                dict(Character.HairLength.choices).get(character.hair_length, character.hair_length)
                if character and character.hair_length
                else "medium"
            ),
            "hair_color": (
                dict(Character.HairColor.choices).get(character.hair_color, character.hair_color)
                if character and character.hair_color
                else "brown"
            ),
            "body_type": (
                dict(Character.BodyType.choices).get(character.body_type, character.body_type)
                if character and character.body_type
                else "average"
            ),
            "appearance_extras": appearance_extras,
            # relationship, memory, summary, language are separate sections -- not in template.
            # These aliases resolve silently so old custom prompt_template fields in the DB
            # don't crash or leak raw placeholders to the AI.
            "relationship_section": "",
            "memory_section": "",
            "summary_section": "",
            "language_section": "",
        }

        # ---- Render character template ----
        output = cls._apply_template(template, context)

        # ---- FORCE location into every prompt ----
        if character and (character.town or character.country):
            town = character.town or "Nairobi"
            country = character.country or "Kenya"
            output += (
                f"\nIMPORTANT: You are from {town}, {country}. "
                "If someone asks where you are from, always answer with exactly "
                f"{town}, {country}. Never mention any other town or region.\n"
            )

        # ---- Load system prompts from env vars at call time ----
        # Loading at call time (rather than class definition time) means prompt
        # edits in Railway take effect on the next request without redeploying.
        if user is None:
            system_prompt = _load_prompt("GUEST_SYSTEM_PROMPT")
        else:
            system_prompt = _load_prompt("GENERAL_SYSTEM_PROMPT")

        # ---- Assemble with clear section headers ----
        # Order: identity → language → formatting → context (relationship/memory/summary)
        # Each header includes a (System) role label for clarity in logs and for the AI.
        sections = []

        # [1] Time & location context
        if time_context:
            sections.append("=== TIME & LOCATION (System) ===\n" + time_context.strip())

        # [2] Platform rules (from env var — safety, tone, platform identity)
        sections.append("=== PLATFORM RULES (System) ===\n" + system_prompt.strip())

        # [3] Character card -- pure persona: who the character IS
        sections.append("=== CHARACTER CARD (System) ===\n" + output.rstrip())

        # [3b] NSFW flag -- explicit content policy for this character
        if character:
            if character.nsfw_enabled:
                nsfw_text = (
                    "NSFW content is ENABLED for this character. "
                    "You may engage with mature, adult, or explicit topics if the user "
                    "initiates them. Always stay in character."
                )
            else:
                nsfw_text = (
                    "NSFW content is DISABLED for this character. "
                    "Do NOT produce explicit, sexual, or graphic content under any circumstances. "
                    "Politely redirect if the user steers the conversation in that direction."
                )
            sections.append("=== NSFW FLAG (System) ===\n" + nsfw_text)

        # [4] Language instruction
        sections.append("=== LANGUAGE (System) ===\n" + language_text)

        # [5] Formatting rules
        sections.append("=== FORMATTING RULES (System) ===\n" + cls.FORMATTING_RULES.strip())

        # [6] Relationship — how the character feels about this specific user
        if relationship_section:
            sections.append("=== RELATIONSHIP WITH USER (System) ===\n" + relationship_section)

        # [7] Memory facts — personal facts extracted from past conversations
        if memory_section:
            sections.append("=== MEMORY (System) ===\n" + memory_section)

        # [8] Conversation summary — distilled recap of previous sessions
        if summary_section:
            sections.append("=== PREVIOUS CONVERSATIONS SUMMARY (System) ===\n" + summary_section)

        full_prompt = "\n\n".join(sections)
        return full_prompt

    # ---------- Formatting helpers ----------

    @classmethod
    def _format_relationship(cls, relationship: dict) -> str:
        if not relationship:
            return ""
        affinity = relationship.get("affinity_score", 0)
        tone = relationship.get("tone_state", "neutral")
        familiarity = relationship.get("familiarity_score", 0)
        trust = relationship.get("trust_score", 0)

        if affinity > 30:
            affinity_text = "and has a strong positive bond with the user"
        elif affinity > 10:
            affinity_text = "and likes the user"
        elif affinity < -30:
            affinity_text = "and strongly dislikes the user"
        elif affinity < -10:
            affinity_text = "and is somewhat distant from the user"
        else:
            affinity_text = "and is neutral towards the user"

        return (
            f"Tone: {tone} | Affinity: {affinity} ({affinity_text}) | "
            f"Familiarity: {familiarity}/100 | Trust: {trust}/100"
        )

    @classmethod
    def _format_memory_facts(cls, facts: List[dict]) -> str:
        if not facts:
            return "No personal memories about the user yet."
        lines = ["Important things you know about the user (with the date each fact was recorded):"]
        for f in facts:
            recorded = ""
            if f.get("created_at"):
                try:
                    from zoneinfo import ZoneInfo

                    ts = f["created_at"]
                    # ts may already be a datetime or a string
                    if hasattr(ts, "strftime"):
                        recorded = ts.astimezone(ZoneInfo("UTC")).strftime(" [Recorded: %Y-%m-%d %H:%M UTC]")
                    else:
                        recorded = f" [Recorded: {str(ts)[:16]}]"
                except Exception:
                    pass
            lines.append(f"- {f['fact_text']}{recorded}")
        return "\n".join(lines)

    @classmethod
    def _format_summary(cls, summary) -> str:
        if not summary:
            return ""
        return summary.summary_text

    @classmethod
    def _get_language_text(cls, language_code: str) -> str:
        mapping = {
            "en": "English",
            "sw": "Swahili",
            "sh": "Sheng (mix Swahili and English naturally)",
        }
        return (
            f"Respond in the same language the user is using. "
            f"The user is currently writing in {mapping.get(language_code, 'English')}."
        )

    # ---------- Template rendering ----------

    @classmethod
    def _apply_template(cls, template: str, context: Dict[str, str]) -> str:
        """
        Placeholder substitution. Supports {key} and {{ key }}.

        After substitution any remaining {word} or {word_word} tokens are
        stripped silently and a warning is logged so the admin knows to update
        the character's prompt_template in the DB.
        """
        import re

        def replace(match):
            key = match.group(1).strip()
            return context.get(key, match.group(0))

        result = re.sub(r"\{\{?\s*(\w+)\s*\}?\}", replace, template)

        # Strip any placeholders that were not resolved (e.g. stale custom template keys).
        # Match single-brace tokens only ({word} / {word_word_word}) — avoid touching
        # double-brace Jinja-style tokens that may appear in user-authored content.
        unresolved = re.findall(r"(?<!\{)\{(\w+)\}(?!\})", result)
        if unresolved:
            logger.warning(
                "prompt_builder: unresolved placeholders stripped from character template: %s. "
                "Update the character's prompt_template in the DB to remove them.",
                unresolved,
            )
            result = re.sub(r"(?<!\{)\{\w+\}(?!\})", "", result)

        return result

    # ---------- History retrieval ----------

    @classmethod
    def _get_recent_history(
        cls,
        conversation: Conversation,
        exclude_message: Optional[Message] = None,
        limit: int = 16,
    ) -> List[Dict[str, str]]:
        """
        Return recent messages as a list of dicts, excluding the given message.
        Each message content is prefixed with sender name and timestamp so the
        AI has full temporal context (e.g. knows "tomorrow" referred to a specific date).
        """
        from zoneinfo import ZoneInfo

        queryset = Message.objects.filter(conversation=conversation).order_by("-created_at")

        if exclude_message:
            queryset = queryset.exclude(message_id=exclude_message.message_id)

        recent = list(queryset[:limit])
        recent.reverse()

        # Determine timezone for timestamp display
        user = conversation.user
        character = conversation.character
        tz_str = getattr(user, "timezone", None) or getattr(character, "timezone", None) or "Africa/Nairobi"
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("Africa/Nairobi")

        # Resolve display names
        user_display = getattr(user, "username", None) or "User"
        getattr(character, "name", None) or "Character"

        history = []
        for msg in recent:
            if not msg.content:
                continue
            role = "assistant" if msg.sender_type == Message.SenderType.AI else "user"

            # Build timestamp string in user/character local time
            try:
                local_ts = msg.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
                tz_abbr = msg.created_at.astimezone(tz).strftime("%Z")
            except Exception:
                local_ts = "unknown time"
                tz_abbr = ""

            # Attach <msg_meta> timestamp tags to USER messages only.
            # Adding them to assistant messages causes the model to mimic the
            # format in its own replies after enough turns in context.
            # The user-side tag gives the AI full temporal context (e.g. resolving
            # "tomorrow" to a specific date) without the echo risk.
            if role == "user":
                meta = f'<msg_meta sender="{user_display} (User)" time="{local_ts} {tz_abbr}"/>'
                history.append({"role": role, "content": meta + "\n" + msg.content})
            else:
                # Assistant messages: clean content only, no meta tag
                history.append({"role": role, "content": msg.content})

        return history
