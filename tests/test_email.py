import logging
import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock

import pytest

from app.core import email as email_module
from app.core.config import settings
from app.core.email import EmailSender


def test_email_sender_sends_todo_notification(monkeypatch, caplog):
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.__exit__.return_value = False
    smtp_factory = MagicMock(return_value=smtp)

    monkeypatch.setattr("app.core.email.smtplib.SMTP", smtp_factory)
    caplog.set_level(logging.INFO, logger="app.core.email")

    EmailSender("mailhog", 1025, "from@example.com").send_todo_created_notification(
        42, "Test todo", "to@example.com"
    )

    smtp_factory.assert_called_once_with("mailhog", 1025, timeout=10)
    assert smtp.send_message.call_count == 1
    message = smtp.send_message.call_args.args[0]
    assert isinstance(message, EmailMessage)
    assert message["Subject"] == "Todo created: Test todo"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert 'The todo "Test todo" (ID: 42) was created.' in message.get_content()
    assert "Sent todo creation email for todo_id=42" in caplog.text


def test_email_sender_logs_smtp_errors_and_reraises(monkeypatch, caplog):
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.__exit__.return_value = False
    smtp.send_message.side_effect = smtplib.SMTPException("SMTP unavailable")
    smtp_factory = MagicMock(return_value=smtp)

    monkeypatch.setattr("app.core.email.smtplib.SMTP", smtp_factory)
    caplog.set_level(logging.ERROR, logger="app.core.email")

    with pytest.raises(smtplib.SMTPException, match="SMTP unavailable"):
        EmailSender("mailhog", 1025, "from@example.com").send_todo_created_notification(
            42, "Test todo", "to@example.com"
        )

    assert "Failed to send todo creation email for todo_id=42" in caplog.text


def test_send_todo_created_email_uses_configured_smtp(monkeypatch):
    sender_config = {}
    sent_notifications = []

    class FakeEmailSender:
        def __init__(self, host, port, from_email):
            sender_config.update(host=host, port=port, from_email=from_email)

        def send_todo_created_notification(self, todo_id, title, recipient):
            sent_notifications.append((todo_id, title, recipient))

    monkeypatch.setattr(email_module, "EmailSender", FakeEmailSender)
    monkeypatch.setattr(settings, "SMTP_HOST", "mailhog")
    monkeypatch.setattr(settings, "SMTP_PORT", 1025)
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sender@example.com")

    email_module.send_todo_created_email(7, "Configured todo", "to@example.com")

    assert sender_config == {
        "host": "mailhog",
        "port": 1025,
        "from_email": "sender@example.com",
    }
    assert sent_notifications == [(7, "Configured todo", "to@example.com")]
