"""Webhooks: checks that a delivery really comes from Higgsfield.

Higgsfield signs every webhook with the "Standard Webhooks" scheme
(https://www.standardwebhooks.com/). Like catalog.py and gallery.py, all
functions here are pure: bytes and text in, result or error out.
"""

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping

# Deliveries whose timestamp is further away from "now" are rejected (replay protection).
TOLERANCE_SECONDS = 300
SECRET_PREFIX = "whsec_"


class WebhookError(Exception):
    """The delivery cannot be trusted. The message says why."""


def signing_key(secret: str) -> bytes:
    """The HMAC key: the secret without "whsec_", base64-decoded."""
    try:
        return base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except (binascii.Error, ValueError) as error:
        raise WebhookError("HF_WEBHOOK_SECRET is not a valid whsec_ secret.") from error


def sign(secret: str, webhook_id: str, timestamp: str, body: bytes) -> str:
    """Computes the signature "v1,<base64>" for a delivery."""
    # The signed content is "<id>.<timestamp>.<raw body>" – the body exactly as received.
    content = f"{webhook_id}.{timestamp}.".encode() + body
    # hmac.new(key, message, hash) computes a keyed hash: only who knows the key can produce it.
    digest = hmac.new(signing_key(secret), content, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def verify(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> None:
    """Raises WebhookError unless the delivery is correctly signed and recent."""
    # Header names are case-insensitive in HTTP, so we compare them in lower case.
    lowered = {name.lower(): value for name, value in headers.items()}
    for name in ("webhook-id", "webhook-timestamp", "webhook-signature"):
        if not lowered.get(name):
            raise WebhookError(f"Header {name} is missing.")
    timestamp = lowered["webhook-timestamp"]
    try:
        age = abs(now - int(timestamp))
    except ValueError as error:
        raise WebhookError("The webhook-timestamp is not a number.") from error
    if age > TOLERANCE_SECONDS:
        raise WebhookError(f"The webhook-timestamp is {int(age)} s away from now (allowed: {TOLERANCE_SECONDS} s).")
    expected = sign(secret, lowered["webhook-id"], timestamp, body)
    # During a secret rotation there are several space-separated signatures; one match is enough.
    # compare_digest takes the same time for every input, so an attacker cannot guess byte by byte.
    if not any(hmac.compare_digest(expected, candidate) for candidate in lowered["webhook-signature"].split()):
        raise WebhookError("No matching signature.")


def envelope_to_status(event: dict) -> dict:
    """Turns a webhook delivery into the same shape as GET /requests/<id>/status.

    The webhook nests the results in "payload"; the status response has them at the top level.
    """
    return {
        "request_id": event.get("request_id"),
        "status": event.get("status"),
        "error": event.get("error"),
        "expires_at": event.get("expires_at"),
        **(event.get("payload") or {}),
    }
