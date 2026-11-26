import logging
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import email as email_module
from app.core.email import EmailSender


@pytest.mark.asyncio
async def test_email_sender_sends_todo_notification(monkeypatch, caplog):
    client = MagicMock()
    client.is_connected = False
    client.connect = AsyncMock()
    client.send_message = AsyncMock()
    client.close = MagicMock()
    monkeypatch.setattr("app.core.email.aiosmtplib.SMTP", MagicMock(return_value=client))
    caplog.set_level(logging.INFO, logger="app.core.email")

    sender = EmailSender("mailhog", 1025, "from@example.com")
    await sender.send_todo_created_notification(42, "Test todo", "to@example.com")

    client.connect.assert_awaited_once_with()
    client.send_message.assert_awaited_once()
    message = client.send_message.await_args.args[0]
    assert isinstance(message, EmailMessage)
    assert message["Subject"] == "Todo created: Test todo"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert 'The todo "Test todo" (ID: 42) was created.' in message.get_content()
    assert "Sent todo creation email for todo_id=42" in caplog.text


@pytest.mark.asyncio
async def test_email_sender_logs_smtp_errors_and_reraises(monkeypatch, caplog):
    client = MagicMock()
    client.is_connected = False
    client.connect = AsyncMock()
    client.send_message = AsyncMock(
        side_effect=email_module.aiosmtplib.SMTPException("SMTP unavailable")
    )
    client.close = MagicMock()
    monkeypatch.setattr("app.core.email.aiosmtplib.SMTP", MagicMock(return_value=client))
    caplog.set_level(logging.ERROR, logger="app.core.email")

    with pytest.raises(
        email_module.aiosmtplib.SMTPException, match="SMTP unavailable"
    ):
        await EmailSender(
            "mailhog", 1025, "from@example.com"
        ).send_todo_created_notification(42, "Test todo", "to@example.com")

    client.close.assert_called_once_with()
    assert "Failed to send todo creation email for todo_id=42" in caplog.text


@pytest.mark.asyncio
async def test_email_sender_reuses_connection_and_closes_it(monkeypatch):
    client = MagicMock()
    client.is_connected = False
    client.connect = AsyncMock(side_effect=lambda: setattr(client, "is_connected", True))
    client.send_message = AsyncMock()
    client.quit = AsyncMock(side_effect=lambda: setattr(client, "is_connected", False))
    client.close = MagicMock()
    monkeypatch.setattr("app.core.email.aiosmtplib.SMTP", MagicMock(return_value=client))

    sender = EmailSender("mailhog", 1025, "from@example.com")
    await sender.send_todo_created_notification(1, "First", "to@example.com")
    await sender.send_todo_created_notification(2, "Second", "to@example.com")
    await sender.close()

    client.connect.assert_awaited_once_with()
    assert client.send_message.await_count == 2
    client.quit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_send_todo_created_email_uses_configured_smtp(monkeypatch):
    send_notification = AsyncMock()
    monkeypatch.setattr(
        email_module.email_sender,
        "send_todo_created_notification",
        send_notification,
    )

    await email_module.send_todo_created_email(
        7, "Configured todo", "to@example.com"
    )

    send_notification.assert_awaited_once_with(
        7, "Configured todo", "to@example.com"
    )
