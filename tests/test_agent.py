"""Synthetic checks of decide(): python -m unittest discover -s tests"""
import math
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent  # noqa: E402

LIVE = ["SPY", "QQQ", "NVDA", "MSFT", "AAPL", "META", "AMZN", "GOOGL", "AVGO", "AMD", "MU", "MRVL", "NFLX", "TSLA",
        "PLTR", "ORCL", "CRM", "JPM", "V", "MA", "COST", "LLY", "SMH", "XLK", "XLC", "XLY", "XLF", "XLI", "XLE", "XLV",
        "XLP", "XLU", "XLRE", "DIA", "IWM", "SOXX", "QLD", "SSO", "TQQQ", "SOXL", "UPRO", "SPXL"]


def market(drift=0.0006, vol=0.01, days=300, seed=1, tickers=LIVE):
    rnd = random.Random(seed)
    out = {}
    for t in tickers:
        px, rows = 100.0, []
        d = drift * (1 + rnd.random()) * (3 if agent.beta(t) == 3 else 2 if agent.beta(t) == 2 else 1)
        for i in range(days):
            o = px
            px *= math.exp(d + vol * rnd.gauss(0, 1))
            rows.append({"ts": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", "open": o, "high": max(o, px),
                         "low": min(o, px), "close": px, "volume": 1e6})
        out[t] = rows
    return out


def run(ms, cash=100_000.0, positions=()):
    state = {"cash": cash, "positions": [{"ticker": t, "quantity": q, "avg_cost": 0} for t, q in positions],
             "last_prices": {t: r[-1]["close"] for t, r in ms.items()}}
    return agent.decide(ms, state, cash)


class DecideTest(unittest.TestCase):
    def test_uptrend_respects_caps(self):
        ms = market()
        orders = run(ms)
        self.assertTrue(orders)
        px = {t: r[-1]["close"] for t, r in ms.items()}
        value = {o["ticker"]: o["quantity"] * px[o["ticker"]] for o in orders if o["side"] == "buy"}
        self.assertLessEqual(sum(value.values()), 100_000.0)
        self.assertLess(max(value.values()) / 100_000.0, 0.30)
        self.assertLessEqual(sum(v * agent.beta(t) for t, v in value.items()) / 100_000.0, 1.5)
        for o in orders:
            self.assertIn(o["side"], ("buy", "sell"))
            self.assertTrue(math.isfinite(o["quantity"]) and o["quantity"] > 0)

    def test_downtrend_goes_to_cash(self):
        ms = market(drift=-0.002)
        self.assertEqual(run(ms), [])
        held = [("AMD", 100.0), ("TQQQ", 50.0)]
        orders = run(ms, cash=50_000.0, positions=held)
        self.assertEqual({(o["ticker"], o["side"]) for o in orders}, {("AMD", "sell"), ("TQQQ", "sell")})

    def test_sells_come_first(self):
        ms = market()
        orders = run(ms, cash=0.0, positions=[("XLU", 2000.0)])
        sides = [o["side"] for o in orders]
        self.assertEqual(sides, sorted(sides, key=lambda s: 0 if s == "sell" else 1))

    def test_deterministic_and_fast(self):
        ms = market(seed=7)
        t0 = time.perf_counter()
        a = run(ms)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(a, run(ms))

    def test_float_dust_in_cash(self):
        ms = market()
        first = run(ms)
        px = {t: r[-1]["close"] for t, r in ms.items()}
        held = [(o["ticker"], o["quantity"]) for o in first if o["side"] == "buy"]
        for cash in (-7.3e-12, 0.0, 1e-9):
            orders = run(ms, cash=cash, positions=held)
            spend = sum(o["quantity"] * px[o["ticker"]] for o in orders if o["side"] == "buy")
            freed = sum(o["quantity"] * px[o["ticker"]] for o in orders if o["side"] == "sell")
            self.assertLessEqual(spend, max(cash, 0.0) + freed + 1e-6)

    def test_missing_data_is_cash_not_crash(self):
        self.assertEqual(agent.decide({}, {"cash": 1000.0, "positions": [], "last_prices": {}}, 1000.0), [])
        ms = market(days=60)
        self.assertEqual(run(ms), [])
        ms = market(tickers=["SPY", "QQQ", "AAPL"])
        self.assertIsInstance(run(ms), list)


if __name__ == "__main__":
    unittest.main()
