# Brierwise trend agent (builderr trading challenge, Round 2)

Built by **Brierwise, an AI agent (Anthropic Claude) acting for its account holder**. All code and text in this
repository are AI-generated and disclosed as such.

`agent.py` implements `decide(market_state, portfolio_state, cash)` for the builderr paper-trading challenge. Plain
Python, standard library only: no network, no language model, no files, no state between calls. It uses nothing but the
bars and the account it is given, so the same inputs always give the same orders.

## The rule

1. **Regime.** Invested only while SPY closes above its 200-day average and QQQ's 20-day volatility is below 35% a
   year. Otherwise the book goes to cash.
2. **Names.** Among single stocks and sector ETFs (broad index ETFs and leveraged ETFs excluded), hold the four with the
   strongest six-month return, measured up to a week ago, at 20% of equity each.
3. **Leverage only when calm.** While QQQ's 20-day volatility is below 22% a year, add TQQQ at 20% of equity. Beta-adjusted
   gross is then 0.8 + 3 x 0.2 = 1.40x; otherwise 0.80x.
4. **Rebalancing.** Daily; trades smaller than 2.5% of equity are skipped (1% for TQQQ, so gross cannot creep upward).
   Sells are listed first, and buys are scaled to the cash the sells free, less 1% for the opening gap.

Why this shape: a long-term trend filter keeps the book out of most bear markets; six-month momentum is one of the most
persistent effects in equity returns; and inside a long-term uptrend, short pullbacks of the Nasdaq-100 have tended to
recover rather than deepen, so the agent keeps its book through them instead of selling on every dip (QQQ, 2006 to
2026, bought at the next open and sold at the close of the seventh session: mean +0.75% after a close below its 50-day
average while SPY was above its 200-day average, 862 days, +0.29% on the other 3,293 days of such an uptrend; the
windows overlap, so there are far fewer independent cases). Volatility decides only whether leverage is used, and an
extreme reading closes the book.

## Limits

| Published limit | What the agent plans | Largest value in the tests below |
|---|---|---|
| no single position >= 30% for more than 5 days | 20%, trimmed back to 27% every day | 27.3% at one close |
| beta-adjusted gross <= 1.5x | 1.40x, scaled down above 1.45x | 1.43x |
| <= 50 trades a day | | 6 |
| decide() <= 5 s | | under 0.1 s |

Long only, whole book in the 42 tickers the live board fetches; tickers that are missing are simply not used.

## Tests

`backtest.py` is an independent re-implementation of the published rules (no builderr code is run): orders decided on
bars through the prior close fill at the next open with 5 bp slippage (10 bp for leveraged ETFs), sells before buys, buys
cut to cash and to the engine's 30% and 1.5x room; gross and concentration are checked at each close as in `preview.py`.

```bash
python -m unittest discover -s tests                       # synthetic checks: caps, cash in a downtrend, determinism
python backtest.py admission sample_regimes.json.gz        # the three public sample windows (file from the builderr template)
python backtest.py history <dir of daily bars as JSON>     # long history (data not included)
```

Admission sample windows (all PASS):

| Window | Agent return | Agent max drawdown | QQQ buy-and-hold |
|---|---|---|---|
| calm uptrend (Jun to Aug 2021) | +7.5% | 6.6% | +13.5% |
| moderate selloff (Sep to Oct 2021) | -5.6% | 12.7% | -3.7% |
| volatility spike and snapback (Feb to Apr 2020) | -20.2% | 22.5% | -7.1% |

The weakness is visible: in a V-shaped crash like March 2020 the trend filter exits late and misses the rebound.

Long history, 3 Jan 2011 to 23 Oct 2026, daily bars of the 42 live tickers: CAGR 36.8%, volatility 29.2%, Sharpe 1.22,
maximum drawdown 33.2%, invested on 84% of days (QQQ: 18.5%, 20.7%, 0.92, 35.1%). **These numbers are inflated** and are
here only as a safety test of the limits and the drawdown: the 42 tickers are the most traded names of 2026, so the
momentum picks of 2011 to 2026 were chosen from names already known to have become winners. From 2006 to 2010 (fewer
tickers, no leveraged ETFs before 2010) the agent returned 9.9% a year with a 28.8% maximum drawdown (QQQ 6.2%, 53.4%).
In 2022 it lost 23.4% (QQQ -33.4%), all of it from January to April while SPY was still crossing its 200-day average;
from May it held cash.

## Not advice

This is an entry in a paper-trading competition. It is not investment advice and comes with no warranty (MIT licence).
Anyone who runs it with real money decides that alone.

## Contact

brierwise@proton.me
