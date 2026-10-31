"""Tests for the pure parts of server.py: configuration, cost policy, routing."""

from pathlib import Path

import pytest

import hf
import server

BASE = {"HF_API_KEY": "key", "HF_API_SECRET": "secret", "HF_SERVER_TOKEN": "token"}


def config(**overrides):
    return server.load_server_config({**BASE, **overrides})


def test_defaults():
    cfg = config()
    assert cfg.data_dir == Path("/data")
    assert cfg.listen == ("0.0.0.0", 8080)
    assert cfg.webhook_listen is None
    assert cfg.fallback_poll_seconds == 300
    assert cfg.max_usd is None
    assert cfg.allow_unpriced == frozenset()
    assert cfg.webhooks_enabled is False
    assert cfg.webhook_url is None


def test_all_variables():
    cfg = config(
        HF_DATA_DIR="/srv/hf",
        HF_LISTEN="127.0.0.1:9000",
        HF_WEBHOOK_LISTEN="0.0.0.0:9001",
        HF_PUBLIC_URL="https://hf.example.com/",
        HF_WEBHOOK_SECRET="whsec_dGVzdC1rZXk=",
        HF_FALLBACK_POLL_SECONDS="60",
        HF_MAX_USD="0.5",
        HF_ALLOW_UNPRICED="a/b, c/d",
    )
    assert cfg.data_dir == Path("/srv/hf")
    assert cfg.listen == ("127.0.0.1", 9000)
    assert cfg.webhook_listen == ("0.0.0.0", 9001)
    assert cfg.webhooks_enabled is True
    assert cfg.webhook_url == "https://hf.example.com/webhooks/higgsfield"
    assert cfg.fallback_poll_seconds == 60
    assert cfg.max_usd == 0.5
    assert cfg.allow_unpriced == frozenset({"a/b", "c/d"})


def test_allow_all_unpriced():
    assert config(HF_ALLOW_UNPRICED="*").allow_unpriced == frozenset({"*"})


@pytest.mark.parametrize("missing", ["HF_API_KEY", "HF_API_SECRET", "HF_SERVER_TOKEN"])
def test_missing_required_variable(missing):
    environ = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(hf.CliError, match=missing) as info:
        server.load_server_config(environ)
    assert info.value.exit_code == 2


@pytest.mark.parametrize("only", [{"HF_PUBLIC_URL": "https://x.example.com"}, {"HF_WEBHOOK_SECRET": "whsec_abc"}])
def test_half_webhook_config_is_refused(only):
    with pytest.raises(hf.CliError, match="HF_PUBLIC_URL and HF_WEBHOOK_SECRET"):
        config(**only)


def test_public_url_must_be_https():
    with pytest.raises(hf.CliError, match="https://"):
        config(HF_PUBLIC_URL="http://x.example.com", HF_WEBHOOK_SECRET="whsec_abc")


def test_server_must_not_run_in_client_mode():
    with pytest.raises(hf.CliError, match="HF_SERVER"):
        config(HF_SERVER="http://other:8080")


@pytest.mark.parametrize(
    "variable, value",
    [("HF_LISTEN", "8080"), ("HF_LISTEN", "host:port"), ("HF_MAX_USD", "cheap"), ("HF_FALLBACK_POLL_SECONDS", "0")],
)
def test_bad_values(variable, value):
    with pytest.raises(hf.CliError, match=variable):
        config(**{variable: value})


@pytest.mark.parametrize(
    "cost, max_usd, model, allowed, refused",
    [
        ({"usd": 0.4, "pricing": None}, None, "a/b", frozenset(), False),
        ({"usd": None, "pricing": "$0.2/s"}, None, "a/b", frozenset(), False),
        ({"usd": 0.4, "pricing": None}, 0.5, "a/b", frozenset(), False),
        ({"usd": 0.6, "pricing": None}, 0.5, "a/b", frozenset(), True),
        ({"usd": None, "pricing": "$0.2/s"}, 0.5, "a/b", frozenset(), True),
        ({"usd": None, "pricing": "$0.2/s"}, 0.5, "a/b", frozenset({"a/b"}), False),
        ({"usd": None, "pricing": None}, 0.5, "a/b", frozenset({"*"}), False),
        ({"usd": None, "pricing": None}, 0.5, "a/b", frozenset({"c/d"}), True),
    ],
)
def test_budget_refusal(cost, max_usd, model, allowed, refused):
    assert (server.budget_refusal(cost, max_usd, model, allowed) is not None) is refused


def test_budget_refusal_messages():
    over = server.budget_refusal({"usd": 0.6, "pricing": None}, 0.5, "a/b", frozenset())
    assert "above the limit of $0.500" in over
    unknown = server.budget_refusal({"usd": None, "pricing": "$0.2/s"}, 0.5, "kling/x", frozenset())
    assert "kling/x" in unknown and "HF_ALLOW_UNPRICED" in unknown and "ask the owner" in unknown


@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("GET", "/healthz", ("healthz", {})),
        ("POST", "/webhooks/higgsfield", ("webhook", {})),
        ("GET", "/v1/status", ("status_v1", {})),
        ("GET", "/v1/jobs/r1", ("job", {"id": "r1"})),
        ("GET", "/v1/jobs", ("jobs", {})),
        ("GET", "/v1/files/2026-10-31/x.png", ("file", {"rel": "2026-10-31/x.png"})),
        ("GET", "/v1/spend", ("spend", {})),
        ("GET", "/v1/characters", ("characters", {})),
        ("PUT", "/v1/characters/Mila", ("character", {"name": "Mila"})),
        ("GET", "/requests/r1/status", ("request_status", {"id": "r1"})),
        ("POST", "/v1/custom-references", ("training", {})),
        ("GET", "/v1/custom-references/ref-1", ("forward", {})),
        ("POST", "/requests/r1/cancel", ("forward", {})),
        ("POST", "/estimate/higgsfield-ai/soul/v2/standard", ("forward", {})),
        ("POST", "/files/generate-upload-url", ("forward", {})),
        ("GET", "/models", ("forward", {})),
        ("POST", "/higgsfield-ai/soul/v2/standard", ("generation", {"model": "higgsfield-ai/soul/v2/standard"})),
        ("GET", "/higgsfield-ai/soul/v2/standard", ("not_found", {})),
        ("POST", "/single", ("not_found", {})),
        ("POST", "/v1/unknown/thing", ("not_found", {})),
        ("DELETE", "/v1/jobs/r1", ("not_found", {})),
        ("GET", "/webhooks/higgsfield", ("not_found", {})),
    ],
)
def test_route(method, path, expected):
    assert server.route(method, path) == expected


def test_malformed_webhook_secret_is_refused():
    with pytest.raises(hf.CliError, match="HF_WEBHOOK_SECRET"):
        config(HF_PUBLIC_URL="https://x.example.com", HF_WEBHOOK_SECRET="whsec_not base64!")
