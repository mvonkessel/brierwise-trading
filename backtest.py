"""Own re-implementation of the published rules, for testing only (no builderr code is run).

    python backtest.py history <data_dir> [start] [end]   # live-engine rules on long daily history
    python backtest.py admission <sample_regimes.json.gz> # the three published admission windows

Live-engine rules as published in the template's live_runner.py: the agent decides on bars through the
prior close; orders fill at the session open with 5 bp slippage (10 bp for leveraged ETFs); sells run
before buys; a buy is cut to the cash available, the 30% single-name room and the 1.5x beta-gross room
at the open. Admission checks as in preview.py: gross <= 1.5x and concentration (>= 30% for more than
5 days) at each close, drawdown below 50%, no errors.
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


def simulate(decide, bars, eval_dates, enforce_engine_caps=True, history=HISTORY_BARS):
    """bars: {t: [{ts, open, high, low, close, volume}, ...]} sorted by ts."""
    ts_index = {t: [b["ts"] for b in rows] for t, rows in bars.items()}
    cash, pos, curve, trades = 100_000.0, {}, [], 0
    peak_gross, conc_streak, worst_streak, errors, slowest = 0.0, {}, 0, 0, 0.0
    peak_name, max_day_trades = 0.0, 0
    gross_series, held_series = [], []
    for date in eval_dates:
        ms, open_px, close_px = {}, {}, {}
        for t, rows in bars.items():
            i = bisect.bisect_left(ts_index[t], date)
            if i > 0:
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
        norm, trades_before = [], trades
        for o in orders[:100]:
            tk, side, q = str(o["ticker"]).upper(), o["side"], float(o["quantity"])
            if side in ("buy", "sell") and math.isfinite(q) and q > 0 and tk in open_px:
                norm.append((tk, side, q))
        for tk, side, q in sorted(norm, key=lambda x: 0 if x[1] == "sell" else 1):
            px = open_px[tk]
            slip = 0.0010 if beta(tk) > 1 else 0.0005
            if side == "buy":
                fill = px * (1 + slip)
                held = pos.get(tk, 0.0)
                room = cash
                if enforce_engine_caps:
                    eq_open = cash + sum(pos[t] * open_px.get(t, 0.0) for t in pos)
                    conc_room = max(0.0, 0.30 * eq_open - held * fill)
                    beta_used = sum(pos[t] * open_px.get(t, 0.0) * beta(t) for t in pos)
                    room = min(cash, conc_room, max(0.0, 1.5 * eq_open - beta_used) / beta(tk))
                q = min(q, room / fill)
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
        max_day_trades = max(max_day_trades, trades - trades_before)
        # mark at close (a ticker without a bar today keeps its last close)
        for t in list(pos):
            if pos[t] <= 1e-9:
                del pos[t]
        last = {t: close_px.get(t, prior.get(t, 0.0)) for t in pos}
        values = {t: pos[t] * last[t] for t in pos}
        equity = max(cash + sum(values.values()), 1e-9)
        curve.append(equity)
        g = sum(v * beta(t) for t, v in values.items()) / equity
        peak_gross = max(peak_gross, g)
        gross_series.append(g)
        held_series.append(sorted(pos))
        for t in set(conc_streak) | set(values):
            peak_name = max(peak_name, values.get(t, 0.0) / equity)
            if values.get(t, 0.0) / equity >= 0.30:
                conc_streak[t] = conc_streak.get(t, 0) + 1
                worst_streak = max(worst_streak, conc_streak[t])
            else:
                conc_streak[t] = 0
    return {"curve": curve, "trades": trades, "peak_gross": peak_gross, "worst_conc_streak": worst_streak,
            "errors": errors, "slowest_s": slowest, "gross": gross_series, "held": held_series,
            "peak_name": peak_name, "max_day_trades": max_day_trades}


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
    return {"cagr": cagr, "vol": sd * math.sqrt(252), "sharpe": mean / sd * math.sqrt(252) if sd > 0 else 0.0, "mdd": mdd}


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


def quantile(xs, p):
    return xs[int(p * (len(xs) - 1))]


def main():
    mode = sys.argv[1]
    agent_path = os.environ.get("AGENT", "agent.py")
    if mode == "admission":
        with gzip.open(sys.argv[2], "rb") as fh:
            regimes = json.loads(fh.read())
        ok_all = True
        for name, reg in regimes.items():
            agent = load_agent(agent_path)  # fresh module per window, as the engine does
            bars = {t: [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
                    for t, rows in reg["bars"].items()}
            dates = sorted({b["ts"] for rows in bars.values() for b in rows})
            ev = [d for d in dates if reg["eval_start"] <= d <= reg["eval_end"]]
            # admission fills are cash-capped only (preview.py), so test without the engine's extra caps
            r = simulate(agent.decide, bars, ev, enforce_engine_caps=False, history=400)
            s = stats(r["curve"])
            ret = r["curve"][-1] / 100_000 - 1
            ok = r["errors"] == 0 and r["peak_gross"] <= 1.5 + 1e-6 and r["worst_conc_streak"] <= 5 and s["mdd"] < 0.5
            ok_all = ok_all and ok
            verdict = "PASS" if ok else "FAIL"
            print(f"{name:20s} ret {ret * 100:6.2f}%  mdd {s['mdd'] * 100:5.1f}%  peak gross {r['peak_gross']:.2f}x  "
                  f"conc streak {r['worst_conc_streak']}d  peak name {r['peak_name'] * 100:.1f}%  trades {r['trades']} "
                  f"(max {r['max_day_trades']}/day)  errors {r['errors']}  slowest {r['slowest_s'] * 1000:.0f} ms  {verdict}")
        print("admission:", "PASS" if ok_all else "FAIL")
        return 0 if ok_all else 1
    agent = load_agent(agent_path)
    bars = load_dir(sys.argv[2])
    start = sys.argv[3] if len(sys.argv) > 3 else "2011-01-03"
    end = sys.argv[4] if len(sys.argv) > 4 else "9999"
    dates = sorted({b["ts"] for b in bars["SPY"]})
    ev = [d for d in dates if start <= d <= end]
    r = simulate(agent.decide, bars, ev)
    s = stats(r["curve"])
    print(f"{ev[0]}..{ev[-1]} days {len(ev)}  CAGR {s['cagr'] * 100:.1f}%  vol {s['vol'] * 100:.1f}%  "
          f"Sharpe {s['sharpe']:.2f}  MaxDD {s['mdd'] * 100:.1f}%  trades {r['trades']}  peak gross {r['peak_gross']:.2f}x  "
          f"conc streak {r['worst_conc_streak']}d  peak name {r['peak_name'] * 100:.1f}%  max {r['max_day_trades']} "
          f"trades/day  errors {r['errors']}  slowest {r['slowest_s'] * 1000:.0f} ms")
    inv = sum(1 for g in r["gross"] if g > 0.05) / len(r["gross"])
    print(f"invested {inv * 100:.0f}% of days; mean gross {sum(r['gross']) / len(r['gross']):.2f}x")
    for n in (5, 7, 9):
        w = sorted(window_returns(r["curve"], n))
        p4 = sum(1 for x in w if x > 0.04) / len(w) * 100
        p6 = sum(1 for x in w if x > 0.06) / len(w) * 100
        print(f"{n}-day windows: median {quantile(w, .5) * 100:+.2f}%  p10 {quantile(w, .1) * 100:+.2f}%  "
              f"p90 {quantile(w, .9) * 100:+.2f}%  P(>4%) {p4:.1f}%  P(>6%) {p6:.1f}%")
    if os.environ.get("CURVE_OUT"):
        with open(os.environ["CURVE_OUT"], "w") as fh:
            json.dump({"dates": ev, "curve": r["curve"], "gross": r["gross"], "held": r["held"]}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
