import logging
import aiosmtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, host: str, port: int, from_email: str) -> None:
        self.host = host
        self.port = port
        self.from_email = from_email

    async def send_todo_created_notification(
        self, todo_id: int, title: str, recipient: str
    ) -> None:
        message = EmailMessage()
        message["Subject"] = f"Todo created: {title}"
        message["From"] = self.from_email
        message["To"] = recipient
        message.set_content(f'The todo "{title}" (ID: {todo_id}) was created.')

        try:
            await aiosmtplib.send(
                message,
                hostname=self.host,
                port=self.port,
                timeout=10,
            )
        except (OSError, aiosmtplib.SMTPException):
            logger.exception(
                "Failed to send todo creation email for todo_id=%s", todo_id
            )
            raise

        logger.info("Sent todo creation email for todo_id=%s", todo_id)


async def send_todo_created_email(todo_id: int, title: str, recipient: str) -> None:
    sender = EmailSender(
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        from_email=settings.SMTP_FROM_EMAIL,
    )
    await sender.send_todo_created_notification(todo_id, title, recipient)
