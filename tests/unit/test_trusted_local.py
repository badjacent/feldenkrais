"""Full, superuser access without login: localhost and the Mini, never Lambda."""

from types import SimpleNamespace
from unittest.mock import patch

import web


def _request(host, cookies=None):
    return SimpleNamespace(headers={"host": host}, cookies=cookies or {})


def test_localhost_is_trusted_and_superuser():
    with patch.object(web, "IS_LAMBDA", False):
        assert web._is_trusted_local(_request("localhost:8000"))
        assert web._is_superuser_active(_request("127.0.0.1:8000"), None)


def test_mini_is_trusted_when_its_runner_opts_in(monkeypatch):
    request = _request("100.108.196.80:8000")
    with patch.object(web, "IS_LAMBDA", False):
        monkeypatch.delenv("FULL_ACCESS_WITHOUT_LOGIN", raising=False)
        assert not web._is_trusted_local(request)
        monkeypatch.setenv("FULL_ACCESS_WITHOUT_LOGIN", "1")
        assert web._is_trusted_local(request)
        assert web._is_superuser_active(_request("locals-mac-mini:8000"), None)


def test_lambda_is_never_trusted(monkeypatch):
    monkeypatch.setenv("FULL_ACCESS_WITHOUT_LOGIN", "1")
    with patch.object(web, "IS_LAMBDA", True):
        assert not web._is_trusted_local(_request("localhost:8000"))
        assert not web._is_superuser_active(_request("aionyourside.net"), None)
        eligible = {"superuser_eligible": True}
        assert web._is_superuser_active(_request("aionyourside.net", {web.SUPERUSER_COOKIE: "1"}), eligible)
        assert not web._is_superuser_active(_request("aionyourside.net"), eligible)
