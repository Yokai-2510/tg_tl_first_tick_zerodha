"""Rejection classification, LPP re-pricing, order-type resolution. BUILD_SPEC §8."""

from __future__ import annotations

import pytest

from backend.brokers.kite.orders import (
    cancel, classify_rejection, is_final, lpp_reprice, modify, parse_lpp_limit,
    place, read_history, resolve_order_type, resolve_product,
    summarise_history_row,
)
from backend.core.enums import (
    OrderStatus, OrderType, Product, RejectionKind, Side, Validity,
)

#: Verbatim rejection observed in production on 24 Jul 2026.
REAL_LPP_MSG = (
    "This order is outside the allowed LPP limit (646.85). You can place an "
    "order within the range or use GTT for long-standing orders."
)


# -- LPP parsing -----------------------------------------------------------

def test_parse_real_lpp_message():
    assert parse_lpp_limit(REAL_LPP_MSG) == 646.85


@pytest.mark.parametrize("msg", [
    "allowed LPP limit (100)",
    "ALLOWED LPP LIMIT (100.5)",
    "allowed  LPP  limit ( 100.50 )",
])
def test_parse_lpp_variants(msg):
    assert parse_lpp_limit(msg) is not None


@pytest.mark.parametrize("msg", [None, "", "Insufficient funds", "LPP limit exceeded"])
def test_parse_lpp_returns_none_when_absent(msg):
    assert parse_lpp_limit(msg) is None


def test_lpp_reprice_real_vector():
    """646.85 * 0.99 = 640.38 -> FLOOR(0.05) = 640.35, inside the band."""
    px = lpp_reprice(lpp_limit=646.85, live_ltp=0.0, tick=0.05)
    assert px == 640.35
    assert px < 646.85


def test_lpp_reprice_takes_the_lower_cap():
    # live LTP 588 -> 588*1.09 = 640.92 is lower than 646.85*0.99 = 640.38? no:
    # 640.92 > 640.38, so the message cap wins.
    assert lpp_reprice(lpp_limit=646.85, live_ltp=588.0, tick=0.05) == 640.35
    # A much lower LTP makes the LTP band the binding constraint.
    assert lpp_reprice(lpp_limit=646.85, live_ltp=500.0, tick=0.05) == 545.00


def test_lpp_reprice_floors_so_rounding_never_exceeds_band():
    for limit in (100.03, 100.07, 646.85, 1000.02):
        assert lpp_reprice(lpp_limit=limit, live_ltp=0.0, tick=0.05) <= limit


def test_lpp_reprice_requires_some_basis():
    with pytest.raises(ValueError):
        lpp_reprice(lpp_limit=None, live_ltp=0.0, tick=0.05)


# -- rejection classification ---------------------------------------------

@pytest.mark.parametrize("msg,kind", [
    (REAL_LPP_MSG, RejectionKind.LPP),
    ("Insufficient funds for this order", RejectionKind.MARGIN),
    ("Margin shortfall", RejectionKind.MARGIN),
    ("Market order is not allowed for this contract", RejectionKind.ORDER_TYPE),
    ("Trading is blocked for this symbol", RejectionKind.RMS),
    ("Too many requests", RejectionKind.RATE_LIMIT),
    ("Request timed out", RejectionKind.NETWORK),
    ("Invalid token", RejectionKind.AUTH),
    ("Something we have never seen", RejectionKind.OTHER),
    (None, RejectionKind.OTHER),
    ("", RejectionKind.OTHER),
])
def test_classify_rejection(msg, kind):
    assert classify_rejection(msg) is kind


def test_lpp_beats_generic_patterns():
    """The LPP text also contains 'order' and 'range' — LPP must still win."""
    assert classify_rejection(REAL_LPP_MSG) is RejectionKind.LPP


# -- order type / product resolution (R6) ----------------------------------

def test_market_downgraded_to_limit_for_stock_options():
    assert resolve_order_type(is_index_symbol=False,
                              configured=OrderType.MARKET) == OrderType.LIMIT


def test_market_allowed_for_index_options():
    assert resolve_order_type(is_index_symbol=True,
                              configured=OrderType.MARKET) == OrderType.MARKET


def test_limit_is_passed_through():
    for idx in (True, False):
        assert resolve_order_type(is_index_symbol=idx,
                                  configured=OrderType.LIMIT) == OrderType.LIMIT


def test_product_routing():
    assert resolve_product(is_index_symbol=False, stock_product=Product.NRML,
                           index_product=Product.MIS) == Product.NRML
    assert resolve_product(is_index_symbol=True, stock_product=Product.NRML,
                           index_product=Product.MIS) == Product.MIS


# -- history summarisation (R13) -------------------------------------------

def test_terminal_vs_interim_statuses():
    complete = summarise_history_row({"order_id": "1", "status": "COMPLETE",
                                      "filled_quantity": 625,
                                      "average_price": 158.0}, "1")
    assert complete.success and is_final(complete)
    assert complete.filled_quantity == 625 and complete.average_price == 158.0

    for interim in ("OPEN PENDING", "VALIDATION PENDING", "MODIFY PENDING", "OPEN"):
        r = summarise_history_row({"order_id": "1", "status": interim}, "1")
        assert not is_final(r), f"{interim} must not be terminal"


def test_rejected_row_carries_kind_and_limit():
    r = summarise_history_row(
        {"order_id": "9", "status": "REJECTED", "status_message": REAL_LPP_MSG}, "9")
    assert is_final(r) and not r.success
    assert r.rejection_kind is RejectionKind.LPP
    assert r.lpp_limit == 646.85


def test_status_case_is_normalised():
    assert summarise_history_row({"status": "complete"}, "1").status == "COMPLETE"


# -- broker calls with a fake client --------------------------------------

class FakeKite:
    def __init__(self, *, fail: str | None = None):
        self.fail, self.calls = fail, []

    def place_order(self, **kw):
        self.calls.append(("place", kw))
        if self.fail:
            raise RuntimeError(self.fail)
        return "260805000123456"

    def modify_order(self, **kw):
        self.calls.append(("modify", kw))
        if self.fail:
            raise RuntimeError(self.fail)

    def cancel_order(self, **kw):
        self.calls.append(("cancel", kw))
        if self.fail:
            raise RuntimeError(self.fail)

    def order_history(self, order_id):
        self.calls.append(("history", order_id))
        if self.fail:
            raise RuntimeError(self.fail)
        return [{"order_id": order_id, "status": "OPEN PENDING"},
                {"order_id": order_id, "status": "COMPLETE",
                 "filled_quantity": 625, "average_price": 158.0}]


def test_place_success_sends_expected_params():
    kite = FakeKite()
    res = place(kite, tradingsymbol="INDIGO26AUG5300PE", exchange="NFO",
                side=Side.BUY, quantity=625, price=160.40,
                order_type=OrderType.LIMIT, product=Product.NRML,
                validity=Validity.IOC, tag="pos_20260805_001")
    assert res.success and res.order_id == "260805000123456"
    _, kw = kite.calls[0]
    assert kw["price"] == 160.40 and kw["validity"] == "IOC"
    assert kw["quantity"] == 625 and kw["variety"] == "regular"
    assert len(kw["tag"]) <= 20
    assert res.ack_ms >= 0


def test_place_market_order_sends_no_price():
    kite = FakeKite()
    place(kite, tradingsymbol="NIFTY2680724000CE", exchange="NFO",
          side=Side.BUY, quantity=75, price=0.0, order_type=OrderType.MARKET)
    _, kw = kite.calls[0]
    assert "price" not in kw


def test_place_rejection_is_returned_not_raised():
    kite = FakeKite(fail=REAL_LPP_MSG)
    res = place(kite, tradingsymbol="X", exchange="NFO", side=Side.BUY,
                quantity=1, price=10.0)
    assert res.success is False
    assert res.rejection_kind is RejectionKind.LPP
    assert res.lpp_limit == 646.85


def test_modify_and_cancel_return_results():
    kite = FakeKite()
    assert modify(kite, order_id="1", price=99.0).success
    assert cancel(kite, order_id="1").success
    bad = FakeKite(fail="Insufficient funds")
    assert modify(bad, order_id="1", price=99.0).rejection_kind is RejectionKind.MARGIN


def test_read_history_returns_latest_row():
    res = read_history(FakeKite(), "260805000123456")
    assert res.status == OrderStatus.COMPLETE
    assert res.filled_quantity == 625


def test_read_history_error_is_returned():
    res = read_history(FakeKite(fail="Request timed out"), "1")
    assert not res.success and res.rejection_kind is RejectionKind.NETWORK
