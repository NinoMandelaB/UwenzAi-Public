import logging
from typing import Dict, Any, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RecaptchaService:
    VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

    @classmethod
    def verify_token(
        cls,
        token: Optional[str],
        remote_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not token:
            return {
                "success": False,
                "code": "recaptcha_required",
            }

        secret_key = getattr(settings, "RECAPTCHA_SECRET_KEY", None)
        if not secret_key:
            logger.error("RECAPTCHA_SECRET_KEY is not configured.")
            return {
                "success": False,
                "code": "recaptcha_not_configured",
            }

        payload = {
            "secret": secret_key,
            "response": token,
        }

        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            response = requests.post(
                cls.VERIFY_URL,
                data=payload,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success") is True:
                return {"success": True}

            logger.warning(
                "reCAPTCHA verification failed. error_codes=%s",
                result.get("error-codes", []),
            )
            return {
                "success": False,
                "code": "recaptcha_verification_failed",
                "error_codes": result.get("error-codes", []),
            }

        except requests.RequestException:
            logger.exception("Error verifying reCAPTCHA token.")
            return {
                "success": False,
                "code": "recaptcha_verification_failed",
            }
