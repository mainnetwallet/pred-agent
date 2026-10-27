from decimal import Decimal

import pytest

from pred_agent.util.fees import signed_fee_bps


def test_docs_examples() -> None:
    assert signed_fee_bps(250, Decimal("68")) == 170
    assert signed_fee_bps(250, Decimal("17")) == 207
    assert signed_fee_bps(250, Decimal("50")) == 125  # floor(target/2)
    assert signed_fee_bps(0, Decimal("40")) == 0
    assert signed_fee_bps(-5, Decimal("40")) == 0


def test_integer_floor_edge_prices() -> None:
    # docs: float floor disagrees at 34c/57c/58c for target 100
    assert signed_fee_bps(100, Decimal("34")) == 66
    assert signed_fee_bps(100, Decimal("57")) == 57
    assert signed_fee_bps(100, Decimal("58")) == 58


def test_out_of_range_price() -> None:
    with pytest.raises(ValueError):
        signed_fee_bps(100, Decimal("100"))
