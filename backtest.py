"""Own re-implementation of the published rules, for testing only (no builderr code is run).

    python backtest.py admission <sample_regimes.json.gz> # the three public sample windows, preview.py timing
    python backtest.py history <data_dir> [start] [end]   # live-engine rules on long daily history

admission mirrors preview.py: on each window day, yesterday's orders fill at today's open in the order given (5 bp
slippage, 10 bp for leveraged ETFs; a buy is cut to the cash available), the book is marked at today's close, gross
(beta-adjusted) and concentration are checked at that close, then decide() sees every bar up to and including today.
Admission needs: no errors, gross <= 1.5x, no single name >= 30% on more than 5 consecutive closes, drawdown < 50%.

history mirrors run_bot() in live_runner.py: decide() sees the bars before the session; orders fill at the session open,
sells before buys; a buy is cut to the cash, the 30% single-name room and the 1.5x beta-gross room at the open; the book
is marked at the close (a ticker without a bar that day counts as 0, as in the engine). Gross and concentration are also
checked at each close, which the live engine itself does not do.
"""
from __future__ import annotations

import bisect
import gzip
import importlib.util
import json
import math
import os
import sys
import time

BETA_3X = {"TQQQ", "SOXL", "UPRO", "SPXL", "TNA", "FAS", "TECL", "LABU", "CURE", "DRN", "UDOW", "NAIL"}
BETA_2X = {"QLD", "SSO", "DDM", "ROM", "UWM", "AGQ"}
HISTORY_BARS = 260


def beta(t):
    return 3.0 if t in BETA_3X else 2.0 if t in BETA_2X else 1.0


def load_agent(path="agent.py"):
    spec = importlib.util.spec_from_file_location("agent_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Book:
    """Close-time telemetry shared by both modes."""

    def __init__(self):
        self.curve, self.gross, self.held = [], [], []
        self.peak_gross = self.peak_name = 0.0
        self.streak, self.worst_streak = {}, 0

    def mark(self, cash, pos, close_px, tickers):
        values = {t: pos[t] * close_px.get(t, 0.0) for t in pos}
        equity = max(cash + sum(values.values()), 1e-9)
        g = sum(abs(v) * beta(t) for t, v in values.items()) / equity
        self.curve.append(equity)
        self.gross.append(g)
        self.held.append(sorted(t for t in pos if pos[t] > 0))
        self.peak_gross = max(self.peak_gross, g)
        for t in set(tickers) | set(values):
            frac = abs(values.get(t, 0.0)) / equity
            self.peak_name = max(self.peak_name, frac)
            if frac >= 0.30:
                self.streak[t] = self.streak.get(t, 0) + 1
                self.worst_streak = max(self.worst_streak, self.streak[t])
            else:
                self.streak[t] = 0


def simulate(decide, bars, eval_dates, history=HISTORY_BARS):
    """Live-engine rules (run_bot). bars: {t: [{ts, open, high, low, close, volume}, ...]} sorted by ts."""
    ts_index = {t: [b["ts"] for b in rows] for t, rows in bars.items()}
    cash, pos, trades, errors, slowest, max_day = 100_000.0, {}, 0, 0, 0.0, 0
    book = Book()
    for date in eval_dates:
        ms, open_px, close_px = {}, {}, {}
        for t, rows in bars.items():
            i = bisect.bisect_left(ts_index[t], date)
            ms[t] = rows[max(0, i - history):i]
            if i < len(rows) and rows[i]["ts"] == date:
                open_px[t], close_px[t] = rows[i]["open"], rows[i]["close"]
        prior = {t: r[-1]["close"] for t, r in ms.items() if r}
        state = {"cash": cash, "positions": [{"ticker": t, "quantity": q, "avg_cost": 0.0} for t, q in pos.items() if q > 0],
                 "last_prices": prior}
        t0 = time.perf_counter()
        try:
            orders = decide(ms, state, cash) or []
        except Exception as e:  # noqa: BLE001
            errors += 1
            print("  ! decide raised on", date, repr(e))
            orders = []
        slowest = max(slowest, time.perf_counter() - t0)
        norm = []
        for o in orders[:100] if isinstance(orders, list) else []:
            try:
                tk, side, q = str(o["ticker"]).strip().upper(), o["side"], float(o["quantity"])
            except (KeyError, TypeError, ValueError):
                continue
            if side in ("buy", "sell") and math.isfinite(q) and q > 0 and tk in open_px:
                norm.append((tk, side, q))
        before = trades
        for tk, side, q in sorted(norm, key=lambda x: 0 if x[1] == "sell" else 1):
            px = open_px[tk]
            slip = 0.0010 if beta(tk) > 1 else 0.0005
            if side == "buy":
                fill = px * (1 + slip)
                held = pos.get(tk, 0.0)
                eq_open = max(cash + sum(pos[t] * open_px.get(t, 0.0) for t in pos), 1e-9)
                conc_room = max(0.0, 0.30 * eq_open - held * fill)
                beta_used = sum(pos[t] * open_px.get(t, 0.0) * beta(t) for t in pos)
                room = min(cash, conc_room, max(0.0, 1.5 * eq_open - beta_used) / beta(tk))
                q = min(q, room / fill if fill > 0 else 0.0)
                if q <= 0:
                    continue
                pos[tk] = held + q
                cash -= fill * q
            else:
                q = min(q, pos.get(tk, 0.0))
                if q <= 0:
                    continue
                cash += px * (1 - slip) * q
                pos[tk] -= q
            trades += 1
        max_day = max(max_day, trades - before)
        for t in [t for t in pos if pos[t] <= 0]:
            del pos[t]
        book.mark(cash, pos, close_px, bars)
    return {"book": book, "curve": book.curve, "trades": trades, "errors": errors, "slowest_s": slowest,
            "max_day_trades": max_day}


def simulate_preview(decide, bars, eval_dates):
    """preview.py timing and fills: decide after the close on all bars up to and including the day, fill next open."""
    cash, pos, pending, trades, errors, slowest, max_day = 100_000.0, {}, [], 0, 0, 0.0, 0
    book = Book()
    by_date = {t: {b["ts"]: b for b in rows} for t, rows in bars.items()}
    for date in eval_dates:
        before = trades
        for o in pending:
            b = by_date[o["ticker"]].get(date)
            if b is None:
                continue
            px = b["open"]
            slip = 0.0010 if beta(o["ticker"]) > 1 else 0.0005
            if o["side"] == "buy":
                fill = px * (1 + slip)
                qty = o["quantity"]
                if fill * qty > cash:
                    qty = cash / fill if fill > 0 else 0
                if qty <= 0:
                    continue
                pos[o["ticker"]] = pos.get(o["ticker"], 0.0) + qty
                cash -= fill * qty
            else:
                qty = min(o["quantity"], pos.get(o["ticker"], 0.0))
                if qty <= 0:
                    continue
                cash += px * (1 - slip) * qty
                pos[o["ticker"]] -= qty
            trades += 1
        pending = []
        max_day = max(max_day, trades - before)
        close_px = {t: d[date]["close"] for t, d in by_date.items() if date in d}
        book.mark(cash, pos, close_px, bars)
        ms = {t: [b for b in rows if b["ts"] <= date] for t, rows in bars.items()}
        state = {"cash": cash, "positions": [{"ticker": t, "quantity": q, "avg_cost": 0.0} for t, q in pos.items() if q > 0],
                 "last_prices": close_px}
        t0 = time.perf_counter()
        try:
            orders = decide(ms, state, cash)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print("  ! decide raised on", date, repr(e))
            orders = []
        slowest = max(slowest, time.perf_counter() - t0)
        for o in orders or []:
            try:
                if o["side"] in ("buy", "sell") and float(o["quantity"]) > 0 and o["ticker"] in bars:
                    pending.append({"ticker": o["ticker"], "side": o["side"], "quantity": float(o["quantity"])})
            except (KeyError, TypeError, ValueError):
                errors += 1
    return {"book": book, "curve": book.curve, "trades": trades, "errors": errors, "slowest_s": slowest,
            "max_day_trades": max_day}


def stats(curve):
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    peak, mdd = -1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    years = n / 252
    cagr = (curve[-1] / 100_000.0) ** (1 / years) - 1 if years > 0 else 0.0
    return {"total": curve[-1] / 100_000.0 - 1, "cagr": cagr, "vol": sd * math.sqrt(252),
            "sharpe": mean / sd * math.sqrt(252) if sd > 0 else 0.0, "mdd": mdd}


def window_returns(curve, n):
    return [curve[i + n - 1] / curve[i - 1] - 1 for i in range(1, len(curve) - n + 1)]


def load_dir(d):
    bars = {}
    for f in os.listdir(d):
        if f.endswith(".json"):
            with open(os.path.join(d, f)) as fh:
                rows = json.load(fh)
            bars[f[:-5]] = [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
    return bars


def load_regimes(path):
    with gzip.open(path, "rb") as fh:
        regimes = json.loads(fh.read())
    out = {}
    for name, reg in regimes.items():
        bars = {t: [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
                for t, rows in reg["bars"].items()}
        dates = sorted({b["ts"] for rows in bars.values() for b in rows})
        out[name] = (bars, [d for d in dates if reg["eval_start"] <= d <= reg["eval_end"]])
    return out


def quantile(xs, p):
    return xs[int(p * (len(xs) - 1))]


def main():
    mode = sys.argv[1]
    agent_path = os.environ.get("AGENT", "agent.py")
    if mode == "admission":
        ok_all = True
        for name, (bars, ev) in load_regimes(sys.argv[2]).items():
            agent = load_agent(agent_path)  # fresh module per window, as the engine does
            r = simulate_preview(agent.decide, bars, ev)
            b, s = r["book"], stats(r["curve"])
            ok = r["errors"] == 0 and b.peak_gross <= 1.5 + 1e-6 and b.worst_streak <= 5 and s["mdd"] < 0.5
            ok_all = ok_all and ok
            print(f"{name:20s} ret {s['total'] * 100:6.2f}%  mdd {s['mdd'] * 100:5.1f}%  peak gross {b.peak_gross:.2f}x  "
                  f"conc streak {b.worst_streak}d  peak name {b.peak_name * 100:.1f}%  trades {r['trades']} "
                  f"(max {r['max_day_trades']}/day)  errors {r['errors']}  slowest {r['slowest_s'] * 1000:.0f} ms  "
                  f"{'PASS' if ok else 'FAIL'}")
        print("admission:", "PASS" if ok_all else "FAIL")
        return 0 if ok_all else 1
    agent = load_agent(agent_path)
    bars = load_dir(sys.argv[2])
    start = sys.argv[3] if len(sys.argv) > 3 else "2011-01-03"
    end = sys.argv[4] if len(sys.argv) > 4 else "9999"
    ev = [d for d in sorted({b["ts"] for b in bars["SPY"]}) if start <= d <= end]
    r = simulate(agent.decide, bars, ev)
    b, s = r["book"], stats(r["curve"])
    print(f"{ev[0]}..{ev[-1]} days {len(ev)} tickers {len(bars)}  total {s['total'] * 100:+.1f}%  CAGR {s['cagr'] * 100:.1f}%  "
          f"vol {s['vol'] * 100:.1f}%  Sharpe {s['sharpe']:.2f}  MaxDD {s['mdd'] * 100:.1f}%  trades {r['trades']}  "
          f"peak gross {b.peak_gross:.2f}x  conc streak {b.worst_streak}d  peak name {b.peak_name * 100:.1f}%  "
          f"max {r['max_day_trades']} trades/day  errors {r['errors']}  slowest {r['slowest_s'] * 1000:.0f} ms")
    inv = sum(1 for g in b.gross if g > 0.05) / len(b.gross)
    print(f"invested {inv * 100:.0f}% of days; mean gross {sum(b.gross) / len(b.gross):.2f}x")
    for n in (5, 7, 9):
        w = sorted(window_returns(r["curve"], n))
        p4 = sum(1 for x in w if x > 0.04) / len(w) * 100
        p6 = sum(1 for x in w if x > 0.06) / len(w) * 100
        print(f"{n}-day windows: median {quantile(w, .5) * 100:+.2f}%  p10 {quantile(w, .1) * 100:+.2f}%  "
              f"p90 {quantile(w, .9) * 100:+.2f}%  P(>4%) {p4:.1f}%  P(>6%) {p6:.1f}%")
    if os.environ.get("CURVE_OUT"):
        with open(os.environ["CURVE_OUT"], "w") as fh:
            json.dump({"dates": ev, "curve": r["curve"], "gross": b.gross, "held": b.held}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
