from capp.classes.email.email_service import get_email_service


class PasswordRecoveryEmailService:
    @classmethod
    def send_recovery_code(cls, to_email: str, full_name: str, code: str) -> bool:
        subject = "Reset your account password"

        text_body = (  # noqa: E231
            f"Hello {full_name}, \n\n"
            f"Your password reset code is: {code}\n\n"
            f"This code will expire shortly.\n\n"
            f"If you did not request a password reset, please ignore this email."
        )

        html_body = f"""  # noqa: E231,E221,E222
        <html>
          <body>
            <p>Hello {full_name},</p>
            <p>Your password reset code is:</p>
            <h2>{code}</h2>
            <p>This code will expire shortly.</p>
            <p>If you did not request a password reset, please ignore this email.</p>
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
