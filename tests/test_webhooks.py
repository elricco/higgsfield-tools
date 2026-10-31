"""Tests for webhooks.py – signature checks with a known secret, no network."""

import base64
import hashlib
import hmac

import pytest

import webhooks

KEY = b"test-key-1234567890"
SECRET = "whsec_" + base64.b64encode(KEY).decode()
BODY = b'{"request_id":"r1","status":"completed"}'
TIMESTAMP = "1767225600"
NOW = 1767225600.0


def signature(key: bytes = KEY, body: bytes = BODY, webhook_id: str = "msg_1", timestamp: str = TIMESTAMP) -> str:
    """Computed independently of webhooks.sign, straight from the Standard Webhooks recipe."""
    digest = hmac.new(key, f"{webhook_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def headers(sig: str | None = None, **overrides) -> dict:
    values = {"webhook-id": "msg_1", "webhook-timestamp": TIMESTAMP, "webhook-signature": sig or signature()}
    values.update(overrides)
    return values


def test_sign_matches_the_standard_recipe():
    assert webhooks.sign(SECRET, "msg_1", TIMESTAMP, BODY) == signature()


def test_valid_signature_passes():
    webhooks.verify(SECRET, headers(), BODY, NOW)


def test_any_of_two_signatures_passes():
    # During secret rotation Higgsfield sends one signature per secret.
    old = signature(key=b"an-old-key")
    webhooks.verify(SECRET, headers(f"{old} {signature()}"), BODY, NOW)


def test_modified_body_fails():
    with pytest.raises(webhooks.WebhookError, match="signature"):
        webhooks.verify(SECRET, headers(), BODY.replace(b"completed", b"failed"), NOW)


def test_old_timestamp_fails():
    with pytest.raises(webhooks.WebhookError, match="timestamp"):
        webhooks.verify(SECRET, headers(), BODY, NOW + 301)


def test_future_timestamp_fails():
    with pytest.raises(webhooks.WebhookError, match="timestamp"):
        webhooks.verify(SECRET, headers(), BODY, NOW - 301)


@pytest.mark.parametrize("missing", ["webhook-id", "webhook-timestamp", "webhook-signature"])
def test_missing_headers_fail(missing):
    values = headers()
    del values[missing]
    with pytest.raises(webhooks.WebhookError, match=missing):
        webhooks.verify(SECRET, values, BODY, NOW)


def test_non_numeric_timestamp_fails():
    with pytest.raises(webhooks.WebhookError, match="timestamp"):
        webhooks.verify(SECRET, headers(**{"webhook-timestamp": "soon"}), BODY, NOW)


def test_headers_are_case_insensitive():
    values = {"Webhook-Id": "msg_1", "Webhook-Timestamp": TIMESTAMP, "WEBHOOK-SIGNATURE": signature()}
    webhooks.verify(SECRET, values, BODY, NOW)


def test_secret_without_prefix_works():
    webhooks.verify(base64.b64encode(KEY).decode(), headers(), BODY, NOW)


def test_envelope_to_status_flattens_payload():
    event = {
        "request_id": "r1",
        "status": "completed",
        "error": None,
        "expires_at": "2026-11-07T12:00:00Z",
        "payload": {"images": [{"url": "u"}]},
    }
    assert webhooks.envelope_to_status(event) == {
        "request_id": "r1",
        "status": "completed",
        "error": None,
        "expires_at": "2026-11-07T12:00:00Z",
        "images": [{"url": "u"}],
    }


def test_envelope_to_status_without_payload():
    event = {"request_id": "r1", "status": "nsfw", "error": None, "payload": None}
    assert webhooks.envelope_to_status(event) == {
        "request_id": "r1",
        "status": "nsfw",
        "error": None,
        "expires_at": None,
    }
