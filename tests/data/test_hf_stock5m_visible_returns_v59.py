"""Visible-action return reconciliations; never certified total-return labels."""
from decimal import Decimal
import unittest
from atlaspm.data import hf_stock5m_visible_returns_v59 as v


def identity(symbol="ABC",start="2020-01-01",end=None):
    return dict(security_id="ID1",symbol=symbol,exchange="XNAS",valid_from=start,valid_to=end,
                known_date="2020-01-01",record_id=symbol)


def action(kind,day,value=None,new_symbol=None):
    return dict(security_id="ID1",kind=kind,effective_date=day,value=value,new_symbol=new_symbol,
                known_date="2020-02-01",record_id=kind)


def marks(**overrides):
    return dict(security_id="ID1",start_date="2020-02-01",end_date="2020-03-31",
                start_symbol="ABC",end_symbol="ABC",start_close="100",end_close="55",**{}) | overrides


class VisibleReturnsTests(unittest.TestCase):
    def test_split_then_dividend(self):
        out=v._interval(marks(),[identity()],
                        [action("SPLIT","2020-02-10","2"),action("CASH_DIVIDEND","2020-03-10","1")],
                        "2020-04-01")
        self.assertEqual(Decimal(out["split_adjusted_start_close"]),Decimal("50"))
        self.assertEqual(Decimal(out["split_adjusted_price_return"]),Decimal(".1"))
        self.assertEqual(Decimal(out["visible_action_return_no_reinvestment"]),Decimal(".12"))
        self.assertFalse(out["approved_for_model_training"])

    def test_ticker_transition_requires_explicit_action(self):
        ids=[identity(end="2020-03-01"),identity("XYZ","2020-03-01")]
        p=marks(end_symbol="XYZ")
        with self.assertRaisesRegex(ValueError,"TICKER_TRANSITION"):
            v._interval(p,ids,[],"2020-04-01")
        r=v._interval(p,ids,[action("TICKER_CHANGE","2020-03-01",new_symbol="XYZ")],"2020-04-01")
        self.assertEqual(r["identity_record_ids"],["ABC","XYZ"])

    def test_same_day_action_and_exit_blocked(self):
        for actions in ([action("SPLIT","2020-03-01","2"),action("CASH_DIVIDEND","2020-03-01","1")],
                        [action("DELISTING","2020-03-01")]):
            with self.assertRaises(ValueError):
                v._interval(marks(),[identity()],actions,"2020-04-01")

    def test_missing_interval_and_same_day_as_of(self):
        for ids,p,as_of in ([identity(end="2020-02-15"),identity("ABC","2020-03-01")],marks(),"2020-04-01"),([identity()],marks(),"2020-03-31"):
            with self.assertRaises(ValueError):
                v._interval(p,ids,[],as_of)
