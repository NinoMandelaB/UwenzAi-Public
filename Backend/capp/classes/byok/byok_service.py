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

    # ── Execute BYOK chat completion ──────────────────────────────────────────

    @classmethod
    def chat_completion(
        cls,
        byok_config,  # UserBYOKConfig instance
        messages: list,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Call the user's own API endpoint. No fallback — if it fails, raise.
        The caller (tasks.py) must handle the exception and surface it to the user.
        """
        api_key = byok_config.get_decrypted_api_key()
        client = OpenAI(api_key=api_key, base_url=byok_config.api_url)
        response = client.chat.completions.create(
            model=byok_config.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return {
            "id": response.id,
            "choices": [
                {
                    "message": {
                        "content": response.choices[0].message.content,
                    }
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
