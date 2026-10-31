"""Fake HTTP objects shared by all tests: no real requests to Higgsfield."""

import json


class FakeResponse:
    """Imitates the parts of requests.Response that hf.py uses."""

    def __init__(self, status_code: int, body=None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.content = self.text.encode()
        self.reason = "Fake"

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class FakeSession:
    """Returns the prepared responses one after another and records every call.

    An entry can also be an exception – then it is raised.
    """

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
