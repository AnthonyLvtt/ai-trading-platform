from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from atp.kraken_private_qualification import (
    KrakenPrivateQualificationStatus,
    qualify_private_offline,
)
from atp.release_deployment.source import inspect_source
from tests.unit.test_kraken_private import evidence


@pytest.fixture(autouse=True)
def clean_source(monkeypatch: pytest.MonkeyPatch):
    source = replace(inspect_source(Path.cwd()), clean=True)
    monkeypatch.setattr(
        "atp.kraken_private_qualification.engine.inspect_source", lambda root: source
    )
    return source


def test_offline_qualification_is_source_bound_and_non_economic(clean_source) -> None:
    result = qualify_private_offline(*evidence())
    assert result.status is KrakenPrivateQualificationStatus.PASSED
    assert result.source_commit_sha == clean_source.source_commit_sha
    assert result.source_identity == clean_source.content_identity
    assert result.private_network_calls == 0
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False


def test_invalid_evidence_fails_qualification() -> None:
    ref, capability, balances, request, orders = evidence()
    result = qualify_private_offline(ref, capability, object(), request, orders)
    assert result.status is KrakenPrivateQualificationStatus.FAILED
    assert result.real_economic_calls == 0


def test_source_change_fails_closed(monkeypatch: pytest.MonkeyPatch, clean_source) -> None:
    values = iter((clean_source, replace(clean_source, source_commit_sha="b" * 40)))
    monkeypatch.setattr(
        "atp.kraken_private_qualification.engine.inspect_source", lambda root: next(values)
    )
    result = qualify_private_offline(*evidence())
    assert result.status is KrakenPrivateQualificationStatus.FAILED
    assert result.reason_code.value == "KRAKEN_SOURCE_INVALID"
    assert result.source_identity is None


def test_no_binance_fallback_is_representable() -> None:
    result = qualify_private_offline(*evidence())
    assert result.venue.value == "KRAKEN"
    assert all(route.startswith("/0/private/") for route in result.route_allowlist)
    assert not hasattr(result, "fallback")
    assert not hasattr(result, "transport")


@pytest.mark.parametrize(
    ("index", "field"),
    [
        (1, "submission_authorized"),
        (1, "side_effect_performed"),
        (2, "side_effect_performed"),
        (4, "side_effect_performed"),
    ],
)
def test_tampered_authority_or_side_effect_evidence_cannot_pass(index: int, field: str) -> None:
    values = list(evidence())
    object.__setattr__(values[index], field, True)
    result = qualify_private_offline(*values)
    assert result.status is KrakenPrivateQualificationStatus.FAILED
    assert result.real_economic_calls == 0
