import logging
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import email as email_module
from app.core.config import settings
from app.core.email import EmailSender


@pytest.mark.asyncio
async def test_email_sender_sends_todo_notification(monkeypatch, caplog):
    send = AsyncMock()
    monkeypatch.setattr("app.core.email.aiosmtplib.send", send)
    caplog.set_level(logging.INFO, logger="app.core.email")

    await EmailSender(
        "mailhog", 1025, "from@example.com"
    ).send_todo_created_notification(
        42, "Test todo", "to@example.com"
    )

    send.assert_awaited_once()
    message = send.await_args.args[0]
    assert send.await_args.kwargs == {
        "hostname": "mailhog",
        "port": 1025,
        "timeout": 10,
    }
    assert isinstance(message, EmailMessage)
    assert message["Subject"] == "Todo created: Test todo"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert 'The todo "Test todo" (ID: 42) was created.' in message.get_content()
    assert "Sent todo creation email for todo_id=42" in caplog.text


@pytest.mark.asyncio
async def test_email_sender_logs_smtp_errors_and_reraises(monkeypatch, caplog):
    send = AsyncMock(side_effect=email_module.aiosmtplib.SMTPException("SMTP unavailable"))
    monkeypatch.setattr("app.core.email.aiosmtplib.send", send)
    caplog.set_level(logging.ERROR, logger="app.core.email")

    with pytest.raises(
        email_module.aiosmtplib.SMTPException, match="SMTP unavailable"
    ):
        await EmailSender(
            "mailhog", 1025, "from@example.com"
        ).send_todo_created_notification(
            42, "Test todo", "to@example.com"
        )

    assert "Failed to send todo creation email for todo_id=42" in caplog.text


@pytest.mark.asyncio
async def test_send_todo_created_email_uses_configured_smtp(monkeypatch):
    sender_config = {}
    sent_notifications = []

    class FakeEmailSender:
        def __init__(self, host, port, from_email):
            sender_config.update(host=host, port=port, from_email=from_email)

        async def send_todo_created_notification(self, todo_id, title, recipient):
            sent_notifications.append((todo_id, title, recipient))

    monkeypatch.setattr(email_module, "EmailSender", FakeEmailSender)
    monkeypatch.setattr(settings, "SMTP_HOST", "mailhog")
    monkeypatch.setattr(settings, "SMTP_PORT", 1025)
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sender@example.com")

    await email_module.send_todo_created_email(
        7, "Configured todo", "to@example.com"
    )

    assert sender_config == {
        "host": "mailhog",
        "port": 1025,
        "from_email": "sender@example.com",
    }
    assert sent_notifications == [(7, "Configured todo", "to@example.com")]
