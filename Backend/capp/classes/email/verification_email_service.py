from capp.classes.email.email_service import get_email_service


class VerificationEmailService:
    @classmethod
    def send_verification_code(cls, to_email: str, full_name: str, code: str) -> bool:
        subject = "Verify your email address"

        text_body = (  # noqa: E231
            f"Hello {full_name}, \n\n"
            f"Your verification code is: {code}\n\n"
            f"This code will expire shortly.\n\n"
            f"If you did not create this account, please ignore this email."
        )

        html_body = f"""  # noqa: E231,E221,E222
        <html>
          <body>
            <p>Hello {full_name},</p>
            <p>Your verification code is:</p>
            <h2>{code}</h2>
            <p>This code will expire shortly.</p>
            <p>If you did not create this account, please ignore this email.</p>
          </body>
        </html>
        """

        service = get_email_service()
        return service.send_email(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
