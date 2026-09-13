from unittest.mock import Mock

from atp.exchange.adapter import ExchangeAdapter, authorize_testnet
from atp.exchange.model import Reason as ExchangeReason
from atp.exchange.submission_gate import authorize_exchange_submission
from atp.ops import ReadinessStatus
from atp.ops.inspection import validate_readiness_result
from atp.release_deployment.model import PromotionStatus
from atp.testnet_activation.contracts import Reason
from tests.activation_support import activation, complete_chain
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW
from tests.unit.test_testnet_activation import offline

__all__ = ["activation", "inputs", "offline"]


def test_complete_chain_final_blocker(activation, tmp_path):
    order, args = complete_chain(activation, tmp_path)
    assert args["readiness"].readiness_status is ReadinessStatus.READY
    assert validate_readiness_result(args["readiness"])
    assert args["promotion"].status is PromotionStatus.ALLOWED
    result = authorize_exchange_submission(order, **args)
    assert result.reason_code is Reason.TESTNET_RUNTIME_BLOCKED
    assert result.transport_call_count == 0
    transport = Mock()
    adapter = ExchangeAdapter(transport)
    outcome = adapter.submit(
        order,
        proof=args["proof"],
        risk=args["risk"],
        strategy=args["strategy"],
        authorization=authorize_testnet(args["readiness"]),
        readiness=args["readiness"],
        submitted_at=NOW,
    )
    assert outcome.reason_code is ExchangeReason.TESTNET_RUNTIME_BLOCKED
    transport.perform.assert_not_called()
