from django.conf import settings

from capp.classes.email.smtp_service import SMTPEmailService


def get_email_service() -> SMTPEmailService:
    return SMTPEmailService(
        host=settings.MAIL_SERVER,
        port=settings.MAIL_PORT,
        username=settings.MAIL_USERNAME,
        password=settings.MAIL_PASSWORD,
        from_email=settings.MAIL_FROM,
        use_tls=settings.MAIL_USE_TLS,
    )
