import asyncio
import logging
import aiosmtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, host: str, port: int, from_email: str) -> None:
        self.from_email = from_email
        self.client = aiosmtplib.SMTP(
            hostname=host,
            port=port,
            timeout=10,
        )
        self._lock = asyncio.Lock()

    async def send_todo_created_notification(
        self, todo_id: int, title: str, recipient: str
    ) -> None:
        message = EmailMessage()
        message["Subject"] = f"Todo created: {title}"
        message["From"] = self.from_email
        message["To"] = recipient
        message.set_content(f'The todo "{title}" (ID: {todo_id}) was created.')

        async with self._lock:
            try:
                if not self.client.is_connected:
                    await self.client.connect()
                await self.client.send_message(message)
            except (OSError, aiosmtplib.SMTPException):
                self.client.close()
                logger.exception(
                    "Failed to send todo creation email for todo_id=%s", todo_id
                )
                raise

        logger.info("Sent todo creation email for todo_id=%s", todo_id)

    async def close(self) -> None:
        async with self._lock:
            if self.client.is_connected:
                try:
                    await self.client.quit()
                except (OSError, aiosmtplib.SMTPException):
                    self.client.close()
                    logger.exception("Failed to close the SMTP connection")
                    raise


email_sender = EmailSender(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_email=settings.SMTP_FROM_EMAIL,
)


async def send_todo_created_email(todo_id: int, title: str, recipient: str) -> None:
    await email_sender.send_todo_created_notification(todo_id, title, recipient)
