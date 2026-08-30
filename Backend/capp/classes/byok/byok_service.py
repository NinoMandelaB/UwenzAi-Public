# capp/classes/byok/byok_service.py

import logging
import sys
import traceback
from typing import Dict, Any

from openai import OpenAI

logger = logging.getLogger(__name__)


class BYOKService:
    """
        Service layer for Bring-Your-Own-Key (BYOK) configuration.
    å
        Responsibilities:
        - Save / retrieve / delete UserBYOKConfig
        - Validate a user-supplied API key by firing a minimal test call
        - Execute a BYOK chat completion during message processing
    """

    # ── Save ─────────────────────────────────────────────────────────────────

    @classmethod
    def save_config(
        cls,
        user,
        api_url: str,
        api_key: str,
        model_name: str,
        system_prompt: str = "",
        store_on_platform: bool = False,
    ) -> Dict[str, Any]:
        from capp.models import UserBYOKConfig

        api_url = api_url.strip().rstrip("/")
        api_key = api_key.strip()
        model_name = model_name.strip()
        system_prompt = system_prompt.strip()

        if not api_url:
            return {"status": "fail", "data": "api_url_required"}
        if not model_name:
            return {"status": "fail", "data": "model_name_required"}

        # ── If api_key is blank, keep the existing stored key ────────────────────
        existing = None
        if not api_key:
            try:
                existing = UserBYOKConfig.objects.get(user=user)
                api_key = existing.get_decrypted_api_key()
            except UserBYOKConfig.DoesNotExist:
                return {"status": "fail", "data": "api_key_required"}

        if not api_key:
            return {"status": "fail", "data": "api_key_required"}

        if system_prompt and not (api_url and api_key and model_name):
            return {"status": "fail", "data": "system_prompt_requires_api_key"}

        # ── Live key validation ───────────────────────────────────────────────────
        validation = cls.validate_credentials(api_url, api_key, model_name)
        if validation.get("status") != "success":
            return validation

        # ── Persist ───────────────────────────────────────────────────────────────
        try:
            obj, created = UserBYOKConfig.create_or_update(
                user=user,
                api_url=api_url,
                api_key=api_key,
                model_name=model_name,
                system_prompt=system_prompt,
                store_on_platform=store_on_platform,
            )
            logger.info(
                "BYOKConfig %s for user=%s (store=%s)",
                "created" if created else "updated",
                user.user_id,
                store_on_platform,
            )
            return {
                "status": "success",
                "data": {
                    "api_url": obj.api_url,
                    "model_name": obj.model_name,
                    "api_key_hint": cls._key_hint(api_key),
                    "system_prompt": obj.system_prompt,
                    "store_on_platform": obj.store_on_platform,
                    "is_active": obj.is_active,
                },
            }
        except Exception:
            logger.exception("Failed to save BYOK config for user=%s", user.user_id)
            return {"status": "fail", "data": "server_error"}

    # ── Retrieve ─────────────────────────────────────────────────────────────

    @classmethod
    def get_config(cls, user) -> Dict[str, Any]:
        """
        Return the user's BYOK config (masked key, never full key).
        Returns status='not_found' when no config exists.
        """
        from capp.models import UserBYOKConfig

        try:
            cfg = UserBYOKConfig.objects.get(user=user)
        except UserBYOKConfig.DoesNotExist:
            return {"status": "not_found"}

        raw_key = cfg.get_decrypted_api_key()
        return {
            "status": "success",
            "data": {
                "api_url": cfg.api_url,
                "model_name": cfg.model_name,
                "api_key_hint": cls._key_hint(raw_key),  # last 4 chars only
                "system_prompt": cfg.system_prompt,
                "store_on_platform": cfg.store_on_platform,
                "is_active": cfg.is_active,
            },
        }

    # ── Delete ────────────────────────────────────────────────────────────────

    @classmethod
    def delete_config(cls, user) -> Dict[str, Any]:
        from capp.models import UserBYOKConfig

        deleted, _ = UserBYOKConfig.objects.filter(user=user).delete()
        if deleted:
            logger.info("Deleted BYOK config for user=%s", user.user_id)
            return {"status": "success"}
        return {"status": "not_found"}

    # ── Toggle active ─────────────────────────────────────────────────────────

    @classmethod
    def set_active(cls, user, is_active: bool) -> Dict[str, Any]:
        from capp.models import UserBYOKConfig

        updated = UserBYOKConfig.objects.filter(user=user).update(is_active=is_active)
        if updated:
            return {"status": "success"}
        return {"status": "not_found"}

    # ── Credential validation (fires a minimal test call) ────────────────────

    @classmethod
    def validate_credentials(cls, api_url: str, api_key: str, model_name: str) -> Dict[str, Any]:
        try:
            client = OpenAI(api_key=api_key, base_url=api_url)
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            logger.info(
                "BYOK credential validation succeeded: url=%s model=%s",
                api_url,
                model_name,
            )
            return {"status": "success"}

        except Exception as exc:
            # Guard: str(exc) can itself raise if response body is not valid JSON
            try:
                error_str = str(exc)
            except Exception:
                error_str = repr(exc)

            # Log full traceback to stderr — Railway shows this as [err] lines
            logger.error(
                f"BYOK validate_credentials FAILED url={api_url} model={model_name}\n" + traceback.format_exc(),
                file=sys.stderr,
                flush=True,
            )

            logger.warning(
                "BYOK validation failed: url=%s model=%s error=%s",
                api_url,
                model_name,
                error_str[:200],
            )

            low = error_str.lower()
            if "401" in low or "authentication" in low or "invalid api key" in low or "unauthorized" in low:
                reason = "invalid_api_key"
            elif "404" in low or "not found" in low or "model" in low:
                reason = "model_not_found"
            elif "connection" in low or "timeout" in low or "network" in low or "connect" in low:
                reason = "connection_error"
            elif "403" in low or "forbidden" in low:
                reason = "access_forbidden"
            else:
                reason = "validation_failed"

            return {"status": "fail", "data": reason}

    # ── Execute BYOK chat completion ────────────────────────────────────────

    # Substrings looked for (case-insensitively) in a 400 error message to
    # recognize "this endpoint doesn't know about the `reasoning_effort`
    # parameter" style rejections. Kept narrow and specific on purpose: we
    # must NOT retry-without-reasoning on unrelated 400s (bad model name,
    # bad messages, content policy, etc.), only on this exact class of
    # rejection, so real errors still surface to the user immediately.
    _UNKNOWN_PARAM_MARKERS = (
        "unknown parameter",
        "unrecognized parameter",
        "unexpected keyword argument",
        "unsupported parameter",
        "extra fields not permitted",
        "additional properties",
        "reasoning_effort",
    )

    @classmethod
    def _is_unknown_reasoning_param_error(cls, exc: Exception) -> bool:
        """
        True only for a 400-class error whose message indicates the
        endpoint rejected an unrecognized/unsupported request parameter
        (almost certainly `reasoning_effort`, the only extra param we ever
        add for BYOK). Any other error (auth, rate limit, 5xx, bad model,
        content filtering, network) returns False so it propagates as-is.
        """
        import openai

        status_code = getattr(exc, "status_code", None)
        if isinstance(exc, openai.BadRequestError):
            status_code = status_code or 400
        if status_code != 400:
            return False

        try:
            message = str(exc).lower()
        except Exception:
            return False
        return any(marker in message for marker in cls._UNKNOWN_PARAM_MARKERS)

    @classmethod
    def chat_completion(
        cls,
        byok_config,  # UserBYOKConfig instance
        messages: list,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        reasoning_effort: str = None,
        tools: list = None,
    ) -> Dict[str, Any]:
        """
        Call the user's own API endpoint.

        ``reasoning_effort``: the conversation's Fast/Deep toggle value
        ("fast"/"deep"). Mapped to a BEST-EFFORT OpenAI-compatible kwarg via
        capp.classes.ai.reasoning.byok_reasoning_kwargs -- unlike the
        Cortecs/Mistral mappings this is NOT verified against the specific
        endpoint's docs (arbitrary third-party endpoint). If the endpoint
        rejects the request because it doesn't recognize the reasoning
        parameter (400, "unknown/unrecognized parameter"-style message), we
        catch exactly that case, log a warning, and retry once without the
        reasoning kwargs so the chat never breaks. Any other error
        (including other 400s) is not retried and propagates to the caller.

        ``tools``: optional OpenAI-compatible tool/function specs (e.g. the
        web_search / open_url tool specs). Passed through unchanged; search
        execution is provider-independent so no BYOK-specific handling is
        needed here beyond forwarding the parameter.

        No fallback to another provider -- if the (possibly retried) call
        still fails, raise. The caller (tasks.py) must handle the exception
        and surface it to the user.
        """
        from capp.classes.ai import reasoning as reasoning_module

        api_key = byok_config.get_decrypted_api_key()
        client = OpenAI(api_key=api_key, base_url=byok_config.api_url)

        reasoning_kwargs = reasoning_module.byok_reasoning_kwargs((reasoning_effort or "fast").lower())

        request_kwargs = {}
        if tools:
            request_kwargs["tools"] = tools

        try:
            response = client.chat.completions.create(
                model=byok_config.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **reasoning_kwargs,
                **request_kwargs,
            )
        except Exception as exc:
            if reasoning_kwargs and cls._is_unknown_reasoning_param_error(exc):
                logger.warning(
                    "BYOK endpoint rejected reasoning param (url=%s model=%s): %s -- " "retrying once without it",
                    byok_config.api_url,
                    byok_config.model_name,
                    exc,
                )
                response = client.chat.completions.create(
                    model=byok_config.model_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **request_kwargs,
                )
            else:
                raise

        message = response.choices[0].message
        message_dict = {"content": message.content}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            message_dict["tool_calls"] = tool_calls

        return {
            "id": response.id,
            "choices": [
                {
                    "message": message_dict,
                }
            ],
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _key_hint(api_key: str) -> str:
        """Return '••••••••' + last 4 chars of the key, or empty string."""
        if not api_key or len(api_key) < 4:
            return "••••"
        return "••••••••" + api_key[-4:]

    # ── Session cleanup (called on logout for non-persistent configs) ─────────

    @classmethod
    def cleanup_non_persistent(cls, user) -> None:
        """
        Delete BYOK configs where store_on_platform=False.
        Call this from the signout view so temporary keys are not retained.
        """
        from capp.models import UserBYOKConfig

        deleted, _ = UserBYOKConfig.objects.filter(user=user, store_on_platform=False).delete()
        if deleted:
            logger.info(
                "Cleaned up %d non-persistent BYOK config(s) for user=%s",
                deleted,
                user.user_id,
            )
