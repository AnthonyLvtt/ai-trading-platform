from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import scripts.qualify_kraken_exposure as module

from atp.exchange.contracts import VenueId
from atp.kraken_private_qualification.exposure_gate import account_identity_from_iiban
from atp.kraken_private_qualification.exposure_gate_model import (
    ExposureGateReason,
    ExposureGateResult,
    ExposureGateStatus,
)
from atp.shared.identity import ContentIdentity

SHA = "a" * 40
IIBAN = "TEST-IIBAN-NEVER-PERSISTED"


def result() -> ExposureGateResult:
    return ExposureGateResult(
        status=ExposureGateStatus.PASSED,
        reason_code=ExposureGateReason.QUALIFIED,
        venue=VenueId.KRAKEN,
        account_identity=account_identity_from_iiban(IIBAN),
        credential_reference_identity=ContentIdentity.from_text("credential"),
        capability_identity=ContentIdentity.from_text("capability"),
        exposure_result_identity=ContentIdentity.from_text("exposure"),
        completed_routes=(
            "/0/private/GetApiKeyInfo",
            "/0/private/BalanceEx",
            "/0/private/TradeVolume",
        ),
        total_private_network_calls=3,
        started_at=datetime(2026, 10, 7, tzinfo=UTC),
        completed_at=datetime(2026, 10, 7, 0, 0, 3, tzinfo=UTC),
        source_commit_sha=SHA,
        source_tree_sha="b" * 40,
        repository_identity=ContentIdentity.from_text("repo"),
        source_identity=ContentIdentity.from_text("source"),
    )


def test_command_requires_exact_confirmation_before_gate(monkeypatch, capsys) -> None:
    prompts = iter((IIBAN, module.CONFIRMATION))
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(prompts))
    called = {}

    def fake_gate(**kwargs):
        called.update(kwargs)
        return result()

    monkeypatch.setattr(module, "qualify_exposure_operator_gate", fake_gate)
    monkeypatch.setattr(
        "sys.argv",
        [
            "qualify_kraken_exposure.py",
            "--expected-source-sha",
            SHA,
            "--source-root",
            ".",
        ],
    )
    assert module.main() == 0
    assert called["expected_source_sha"] == SHA
    assert called["source_root"] == Path(".")
    assert called["expected_account_identity"] == account_identity_from_iiban(IIBAN)
    assert callable(called["credential_loader"])
    assert called["key_info_transport"].__class__.__name__ == "KrakenPrivateHTTPTransport"
    assert called["exposure_transport"].__class__.__name__ == "KrakenExposureHTTPTransport"
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PASSED"
    assert IIBAN not in json.dumps(output)


def test_command_rejects_confirmation_without_gate(monkeypatch) -> None:
    prompts = iter((IIBAN, "NO"))
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(prompts))
    monkeypatch.setattr(
        module,
        "qualify_exposure_operator_gate",
        lambda **kwargs: pytest.fail("gate must not run"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["qualify_kraken_exposure.py", "--expected-source-sha", SHA],
    )
    with pytest.raises(SystemExit):
        module.main()
