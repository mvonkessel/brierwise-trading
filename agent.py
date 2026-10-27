"""Brierwise trend agent for the builderr trading challenge, Round 2.

Written by an AI (Anthropic's Claude, entering as "Brierwise"). Plain Python, standard
library only: no network, no LLM, no files, no state between calls. decide() uses nothing
but the bars and the account it is given, so the same inputs always give the same orders.

The rule in one paragraph: while SPY is above its 200-day average, hold the four names with
the strongest six-month momentum at 20% each; while that uptrend is also calm (QQQ's 20-day
volatility under 22% a year), add a 20% TQQQ sleeve, which brings beta-adjusted gross to
1.4x. When SPY is below its 200-day average, or QQQ's volatility is above 35% a year, hold
cash. Every position stays under the 30% cap and gross under 1.5x, with room for drift.
"""
from __future__ import annotations

import math

BETA_3X = {"TQQQ", "SOXL", "UPRO", "SPXL", "TNA", "FAS", "TECL", "LABU", "CURE", "DRN", "UDOW", "NAIL"}
BETA_2X = {"QLD", "SSO", "DDM", "ROM", "UWM", "AGQ"}
BROAD = {"SPY", "QQQ", "DIA", "IWM", "VOO", "IVV", "VTI", "RSP"}   # market proxies, not picks

P = {
    "trend_days": 200,      # risk-on only while SPY closes above its 200-day average
    "crash_vol": 0.35,      # ... and QQQ's 20-day annualised volatility is below 35%
    "mom_days": 126,        # momentum = six-month return ...
    "skip_days": 5,         # ... skipping the last week (short-term reversal)
    "top_k": 4,             # names held
    "name_weight": 0.20,    # 4 x 20% = 80%
    "calm_vol": 0.22,       # QQQ volatility below this = calm uptrend
    "sleeve": "TQQQ",       # leveraged sleeve in calm uptrends (3x)
    "sleeve_weight": 0.20,  # 80% names + 3 x 20% = 1.40x beta-adjusted gross
    "gross_cap": 1.45,      # never plan above this (engine limit 1.5x)
    "name_cap": 0.27,       # guard: never plan above this (engine limit 30%)
    "band": 0.025,          # skip trades smaller than 2.5% of equity ...
    "band_levered": 0.01,   # ... or 1% for the leveraged sleeve, so gross cannot creep up
}


def beta(t: str) -> float:
    return 3.0 if t in BETA_3X else 2.0 if t in BETA_2X else 1.0


def _closes(bars):
    out = []
    for b in bars:
        try:
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(c) and c > 0:
            out.append(c)
    return out


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _vol(xs, n=20):
    if len(xs) < n + 1:
        return None
    r = [xs[i] / xs[i - 1] - 1 for i in range(len(xs) - n, len(xs))]
    m = sum(r) / n
    return math.sqrt(sum((a - m) ** 2 for a in r) / (n - 1)) * math.sqrt(252)


def targets(market_state: dict):
    """Target weights {ticker: fraction of equity} from the bars alone, or None when SPY or QQQ history is missing."""
    closes = {t: _closes(b) for t, b in market_state.items() if b}
    spy, qqq = closes.get("SPY", []), closes.get("QQQ", [])
    trend, qv = _sma(spy, P["trend_days"]), _vol(qqq)
    if trend is None or qv is None:
        return None                                 # regime unknown: no trades (a data gap must not empty the book)
    if spy[-1] <= trend or qv > P["crash_vol"]:
        return {}                                   # downtrend or crash-level volatility: cash
    need = P["mom_days"] + 1
    scored = []
    for t, xs in closes.items():
        if t in BROAD or beta(t) > 1 or len(xs) < need:
            continue
        mom = xs[-1 - P["skip_days"]] / xs[-need] - 1
        if mom > 0:
            scored.append((mom, t))
    scored.sort(reverse=True)
    w = {t: P["name_weight"] for _, t in scored[:P["top_k"]]}
    if len(w) == P["top_k"] and qv < P["calm_vol"] and P["sleeve"] in closes:
        w[P["sleeve"]] = P["sleeve_weight"]
    return w


def decide(market_state, portfolio_state, cash):
    w = targets(market_state)
    if w is None:
        return []
    prices = {}
    for t, bars in market_state.items():
        xs = _closes(bars[-1:]) if bars else []
        if xs:
            prices[t] = xs[-1]
    for t, px in (portfolio_state.get("last_prices") or {}).items():
        try:
            if float(px) > 0:
                prices[t] = float(px)
        except (TypeError, ValueError):
            pass
    held = {}
    for p in portfolio_state.get("positions") or []:
        q = float(p.get("quantity") or 0)
        if q > 0:
            held[p["ticker"]] = held.get(p["ticker"], 0.0) + q
    cash = float(cash)
    equity = cash + sum(q * prices.get(t, 0.0) for t, q in held.items())
    if equity <= 0:
        return []
    # Guards below the published limits; with the settings above they never bind (plan: 20% a name, 1.40x gross).
    w = {t: min(x, P["name_cap"]) for t, x in w.items() if prices.get(t, 0.0) > 0}
    gross = sum(x * beta(t) for t, x in w.items())
    if gross > P["gross_cap"]:
        w = {t: x * P["gross_cap"] / gross for t, x in w.items()}
    total = sum(w.values())
    if total > 0.995:
        w = {t: x * 0.995 / total for t, x in w.items()}
    sells, buys = [], []
    for t in sorted(set(held) | set(w)):
        px = prices.get(t, 0.0)
        if px <= 0:
            continue
        cur = held.get(t, 0.0) * px
        diff = w.get(t, 0.0) * equity - cur
        if t not in w:
            sells.append({"ticker": t, "side": "sell", "quantity": held[t]})
        elif abs(diff) >= (P["band"] if beta(t) == 1 else P["band_levered"]) * equity:
            side = "buy" if diff > 0 else "sell"
            (buys if diff > 0 else sells).append({"ticker": t, "side": side, "quantity": abs(diff) / px})
    # Sells fill first; buys are scaled to the cash they free, less a margin for the opening gap.
    free = max(0.0, cash) + sum(o["quantity"] * prices[o["ticker"]] for o in sells)   # cash can be -1e-12
    cost = sum(o["quantity"] * prices[o["ticker"]] for o in buys)
    if cost > 0 and cost > 0.99 * free:
        f = 0.99 * free / cost
        buys = [dict(o, quantity=o["quantity"] * f) for o in buys]
    return [o for o in sells + buys if o["quantity"] > 1e-9]   # unrounded, so a full exit leaves no remainder
