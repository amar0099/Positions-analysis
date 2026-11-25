import unittest
from datetime import date, timedelta

import pandas as pd

from src.options_terminal.risk_analysis import position_expiry_risk, stress_test_portfolio


class RiskAnalysisTests(unittest.TestCase):
    def test_long_option_max_loss_is_premium_paid(self):
        legs = pd.DataFrame([{
            "Underlying": "NIFTY", "Expiry": "2027-01-28", "OptionType": "CE",
            "Position": "BUY", "Strike": 25000, "Quantity": 65, "EntryPrice": 100,
        }])
        result = position_expiry_risk(legs).iloc[0]
        self.assertEqual(result["MaxLoss"], 6500)
        self.assertTrue(pd.isna(result["MaxProfit"]) is False)
        self.assertEqual(result["MaxProfitLabel"], "Unlimited")

    def test_short_call_is_unlimited_loss(self):
        legs = pd.DataFrame([{
            "Underlying": "NIFTY", "Expiry": "2027-01-28", "OptionType": "CE",
            "Position": "SELL", "Strike": 25000, "Quantity": 65, "EntryPrice": 100,
        }])
        result = position_expiry_risk(legs).iloc[0]
        self.assertEqual(result["MaxLossLabel"], "Unlimited")
        self.assertEqual(result["MaxProfit"], 6500)

    def test_short_put_loss_is_bounded(self):
        legs = pd.DataFrame([{
            "Underlying": "NIFTY", "Expiry": "2027-01-28", "OptionType": "PE",
            "Position": "SELL", "Strike": 25000, "Quantity": 65, "EntryPrice": 100,
        }])
        result = position_expiry_risk(legs).iloc[0]
        self.assertEqual(result["MaxLoss"], (25000 - 100) * 65)

    def test_stress_test_returns_portfolio_and_leg_rows(self):
        expiry = (date.today() + timedelta(days=30)).isoformat()
        legs = pd.DataFrame([{
            "Underlying": "NIFTY", "Expiry": expiry, "OptionType": "CE",
            "Position": "BUY", "Strike": 25000, "Quantity": 65, "EntryPrice": 100,
            "Spot": 25000, "IV": 0.20,
        }])
        portfolio, detail = stress_test_portfolio(
            legs, [{"name": "down", "spot_pct": -5, "iv_points": 10, "days_forward": 1}],
            date.today(), 0.065, 0.0
        )
        self.assertEqual(len(portfolio), 1)
        self.assertEqual(len(detail), 1)
        self.assertEqual(portfolio.iloc[0]["Scenario"], "down")


if __name__ == "__main__":
    unittest.main()
