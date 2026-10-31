"""hf serve: runs hf as a small service for agents.

The service holds the Higgsfield credentials, receives completion webhooks
(polling as a safety net), stores results and the job log centrally and
answers the hf CLI in client mode (HF_SERVER). See docs/server.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hf import CliError

WEBHOOK_PATH = "/webhooks/higgsfield"
# Higgsfield paths that are passed on unchanged (with the server's credentials).
FORWARD_PREFIXES = ("/requests/", "/estimate/", "/files/", "/v1/custom-references/")


# --- Configuration ----------------------------------------------------------


# @dataclass writes __init__ and friends for us; frozen=True makes the object read-only.
@dataclass(frozen=True)
class ServerConfig:
    api_key: str
    api_secret: str
    token: str
    data_dir: Path
    listen: tuple[str, int]
    webhook_listen: tuple[str, int] | None
    public_url: str | None
    webhook_secret: str | None
    fallback_poll_seconds: int
    max_usd: float | None
    allow_unpriced: frozenset[str]

    # @property: read like an attribute (config.webhooks_enabled), computed on access.
    @property
    def webhooks_enabled(self) -> bool:
        return bool(self.public_url and self.webhook_secret)

    @property
    def webhook_url(self) -> str | None:
        return self.public_url.rstrip("/") + WEBHOOK_PATH if self.webhooks_enabled else None


def config_error(message: str) -> CliError:
    # Exit code 2 like missing credentials in the CLI: a setup problem, not a failed job.
    return CliError(message, exit_code=2)


def parse_address(name: str, value: str) -> tuple[str, int]:
    """'0.0.0.0:8080' -> ('0.0.0.0', 8080)"""
    # rpartition splits at the LAST ':' – so the port is always the part after it.
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise config_error(f"{name} must look like host:port, e.g. 0.0.0.0:8080 (is '{value}').")
    return host, int(port)


def parse_number(name: str, value: str, kind: type, minimum: float) -> float:
    try:
        number = kind(value)
    except ValueError:
        raise config_error(f"{name} must be a number (is '{value}').") from None
    if number < minimum:
        raise config_error(f"{name} must be at least {minimum} (is '{value}').")
    return number


def load_server_config(environ: Mapping[str, str]) -> ServerConfig:
    """Reads and checks all settings. Refuses to start on anything missing or half configured."""
    for name in ("HF_API_KEY", "HF_API_SECRET", "HF_SERVER_TOKEN"):
        if not environ.get(name):
            raise config_error(f"{name} must be set for hf serve.")
    if environ.get("HF_SERVER"):
        raise config_error("HF_SERVER is set: hf serve must not run in client mode. Unset HF_SERVER.")
    public_url = environ.get("HF_PUBLIC_URL") or None
    webhook_secret = environ.get("HF_WEBHOOK_SECRET") or None
    if bool(public_url) != bool(webhook_secret):
        raise config_error("Set both HF_PUBLIC_URL and HF_WEBHOOK_SECRET for webhooks, or neither for polling.")
    if public_url and not public_url.startswith("https://"):
        raise config_error("HF_PUBLIC_URL must start with https:// (Higgsfield only calls HTTPS webhooks).")
    max_usd = environ.get("HF_MAX_USD")
    webhook_listen = environ.get("HF_WEBHOOK_LISTEN")
    return ServerConfig(
        api_key=environ["HF_API_KEY"],
        api_secret=environ["HF_API_SECRET"],
        token=environ["HF_SERVER_TOKEN"],
        data_dir=Path(environ.get("HF_DATA_DIR") or "/data"),
        listen=parse_address("HF_LISTEN", environ.get("HF_LISTEN") or "0.0.0.0:8080"),
        webhook_listen=parse_address("HF_WEBHOOK_LISTEN", webhook_listen) if webhook_listen else None,
        public_url=public_url,
        webhook_secret=webhook_secret,
        fallback_poll_seconds=int(
            parse_number("HF_FALLBACK_POLL_SECONDS", environ.get("HF_FALLBACK_POLL_SECONDS") or "300", int, 1)
        ),
        max_usd=parse_number("HF_MAX_USD", max_usd, float, 0) if max_usd else None,
        allow_unpriced=frozenset(
            item.strip() for item in (environ.get("HF_ALLOW_UNPRICED") or "").split(",") if item.strip()
        ),
    )


# --- Cost policy ------------------------------------------------------------


def budget_refusal(cost: dict, max_usd: float | None, model_id: str, allow_unpriced: frozenset[str]) -> str | None:
    """Why the server refuses a job, or None if it may run."""
    if max_usd is None:
        return None
    if cost["usd"] is None:
        if "*" in allow_unpriced or model_id in allow_unpriced:
            return None
        reason = cost.get("pricing") or cost.get("estimate_error") or "no estimate available"
        return (
            f"Price unknown for {model_id} ({reason}) and this server has a limit of ${max_usd:.3f}. "
            f"Nothing was submitted. Only the owner can allow it via HF_ALLOW_UNPRICED – ask the owner."
        )
    if cost["usd"] > max_usd:
        return f"Estimated cost ${cost['usd']:.3f} is above the limit of ${max_usd:.3f}. Nothing was submitted."
    return None


# --- Routing ----------------------------------------------------------------


def route(method: str, path: str) -> tuple[str, dict]:
    """Which handler answers a request. path is without the query string."""
    if method == "GET" and path == "/healthz":
        return "healthz", {}
    if method == "POST" and path == WEBHOOK_PATH:
        return "webhook", {}
    if path.startswith("/v1/") and not path.startswith("/v1/custom-references"):
        return route_service(method, path.removeprefix("/v1/"))
    if method == "GET" and path.startswith("/requests/") and path.endswith("/status"):
        return "request_status", {"id": path.removeprefix("/requests/").removesuffix("/status")}
    if method == "POST" and path == "/v1/custom-references":
        return "training", {}
    if path.startswith(FORWARD_PREFIXES) or path == "/models":
        return "forward", {}
    # Everything else with at least two path parts, e.g. /higgsfield-ai/soul/v2/standard, is a generation.
    parts = [part for part in path.split("/") if part]
    if method == "POST" and len(parts) >= 2 and not path.startswith(("/v1/", "/webhooks/")):
        return "generation", {"model": "/".join(parts)}
    return "not_found", {}


def route_service(method: str, rest: str) -> tuple[str, dict]:
    """The service's own endpoints under /v1/."""
    if method == "GET":
        if rest == "status":
            return "status_v1", {}
        if rest == "jobs":
            return "jobs", {}
        if rest == "spend":
            return "spend", {}
        if rest == "characters":
            return "characters", {}
        if rest.startswith("jobs/") and rest.count("/") == 1:
            return "job", {"id": rest.removeprefix("jobs/")}
        if rest.startswith("files/"):
            return "file", {"rel": rest.removeprefix("files/")}
    if method == "PUT" and rest.startswith("characters/") and rest.count("/") == 1:
        return "character", {"name": rest.removeprefix("characters/")}
    return "not_found", {}
