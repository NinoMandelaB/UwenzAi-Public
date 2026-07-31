import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class SMTPEmailService:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        use_tls: bool = True,
        timeout: int = 20,
    ):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.from_email = from_email
        self.use_tls = use_tls
        self.timeout = timeout

    def send_email(
        self,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: Optional[str] = None,
        reply_to: Optional[str] = None,
        cc: Optional[Iterable[str]] = None,
        bcc: Optional[Iterable[str]] = None,
    ) -> bool:
        if not to_email:
            raise ValueError("to_email is required")

        if not subject:
            raise ValueError("subject is required")

        if not text_body and not html_body:
            raise ValueError("Either text_body or html_body must be provided")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to_email

        if reply_to:
            msg["Reply-To"] = reply_to

        if cc:
            msg["Cc"] = ", ".join(cc)

        recipients = [to_email]
        if cc:
            recipients.extend(list(cc))
        if bcc:
            recipients.extend(list(bcc))

        if text_body:
            msg.set_content(text_body)
        else:
            msg.set_content("")

        if html_body:
            msg.add_alternative(html_body, subtype="html")

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                server.ehlo()

                if self.use_tls:
                    server.starttls()
                    server.ehlo()

                if self.username and self.password:
                    server.login(self.username, self.password)

                server.send_message(msg, to_addrs=recipients)

            logger.info("Email sent successfully to=%s subject=%s", to_email, subject)
            return True

        except Exception:
            logger.exception("Failed to send email to=%s subject=%s", to_email, subject)
            return False
