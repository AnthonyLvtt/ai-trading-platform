import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order import preparation_http
from atp.testnet_activation.runtime_credentials import (
    ReferencedEnvironmentCredentialsProvider,
    new_credential_reference,
)


def provider(monkeypatch):
    monkeypatch.setenv("ATP_BINANCE_TESTNET_API_KEY", "synthetic-key")
    monkeypatch.setenv("ATP_BINANCE_TESTNET_API_SECRET", "synthetic-secret")
    return ReferencedEnvironmentCredentialsProvider(new_credential_reference())


def test_http_closed_get_surface_and_sanitized_errors(monkeypatch, caplog):
    seen = []

    class Connection:
        def __init__(self, host, timeout):
            assert host == "testnet.binance.vision"

        def request(self, method, path, headers):
            assert method == "GET"
            seen.append(path.split("?", 1)[0])
            if path.startswith("/api/v3/account"):
                assert headers["X-MBX-APIKEY"] == "synthetic-key"
                raise OSError("synthetic-key synthetic-secret")

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda limit: b'{"serverTime": 1}')

        def close(self):
            pass

    monkeypatch.setattr(preparation_http, "HTTPSConnection", Connection)
    source = preparation_http.TestnetReadOnlySource(provider(monkeypatch))
    assert source.read("time") == {"serverTime": 1}
    with pytest.raises(EvidenceError) as caught:
        source.read("account")
    text = repr(source) + str(caught.value) + caplog.text
    assert "synthetic-key" not in text and "synthetic-secret" not in text
    for resource in ("order", "cancel", "withdraw", "https://api.binance.com", "../order"):
        with pytest.raises(EvidenceError):
            source.read(resource)
    assert seen == ["/api/v3/time", "/api/v3/account"]


@pytest.mark.parametrize(
    "code,body", [(302, b"{}"), (500, b"{}"), (200, b"{"), (200, b'{"api_key":"secret"}')]
)
def test_http_response_fail_closed(monkeypatch, code, body):
    connection = SimpleNamespace(
        request=lambda *a, **kw: None,
        close=lambda: None,
        getresponse=lambda: SimpleNamespace(status=code, read=lambda limit: body),
    )
    monkeypatch.setattr(preparation_http, "HTTPSConnection", lambda *a, **kw: connection)
    with pytest.raises(EvidenceError):
        preparation_http.TestnetReadOnlySource(provider(monkeypatch)).read("time")


def cli_module():
    spec = importlib.util.spec_from_file_location(
        "check_cli", Path("scripts/submit_first_testnet_order.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_refused_before_any_credentials_or_network(monkeypatch):
    module = cli_module()

    def forbidden(*a, **kw):
        pytest.fail("No credential or network boundary should be entered")

    monkeypatch.setattr(module, "ReferencedEnvironmentCredentialsProvider", forbidden)
    monkeypatch.setattr(module, "TestnetReadOnlySource", forbidden)
    result = module.run(SimpleNamespace(check_only=False, confirm_testnet_permissions=True))
    assert result["status"] == "BLOCKED" and result["real_economic_calls"] == 0


def test_cli_snapshot_not_inherited_by_collectors(monkeypatch, tmp_path):
    import os
    import sys

    module = cli_module()
    provider(monkeypatch)
    calls = []

    def release(*a, **kw):
        assert "ATP_BINANCE_TESTNET_API_KEY" not in os.environ
        assert "ATP_BINANCE_TESTNET_API_SECRET" not in os.environ
        calls.append("release")
        return 1

    monkeypatch.setitem(sys.modules, "release", SimpleNamespace(run=release))
    monkeypatch.setitem(sys.modules, "qualify_testnet", SimpleNamespace(collect=None))
    with pytest.raises(EvidenceError):
        module.run(
            SimpleNamespace(
                check_only=True,
                confirm_testnet_permissions=True,
                session_dir=tmp_path / "private",
                release_version="test",
                source_commit="a" * 40,
            )
        )
    assert calls == ["release"]
    for path in (tmp_path / "private").rglob("*.json"):
        assert "synthetic-secret" not in json.dumps(json.loads(path.read_text()))
