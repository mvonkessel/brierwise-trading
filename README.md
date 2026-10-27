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
   strongest return from six months ago (126 sessions) to one week ago (5 sessions), at 20% of equity each, as long as
   that return is positive.
3. **Leverage only when calm.** While QQQ's 20-day volatility is below 22% a year and all four name slots are filled, add
   TQQQ at 20% of equity. Beta-adjusted gross is then 0.8 + 3 x 0.2 = 1.40x; otherwise at most 0.80x.
4. **Rebalancing.** Daily, back to the targets (0.995 of them, leaving cash for the opening gap) whenever a position is
   2.5% of equity away from its target (1% for TQQQ, so gross cannot creep upward). Sells are listed first, and buys are
   scaled to the cash the sells free, less 1%.
5. **Data gaps.** If SPY or QQQ history is missing or too short, the agent sends no orders and keeps its book.

Why this shape: a long-term trend filter keeps the book out of slow bear markets; six-month momentum is one of the most
persistent effects in equity returns; and the agent deliberately does not require QQQ to be above its 50-day average,
because inside a long-term uptrend such pullbacks have tended to recover rather than deepen. On QQQ from 2006 to
October 2026 (bought at the next open, sold at the close of the seventh session), days with a close below the 50-day
average while SPY was above its 200-day average were followed by +0.75% on average (862 days in 173 separate pullbacks;
+0.83% per pullback with a standard error of 0.23; +0.52% counting only the first day of each pullback), against +0.29%
on the other 3,308 days of such an uptrend. That is a weak but consistent sign, not proof. Volatility decides only
whether leverage is used, and an extreme reading closes the book.

## Limits

| Published limit | What the agent plans | Largest value in the tests below |
|---|---|---|
| no single position >= 30% for more than 5 days | 19.9 to 20% a name | 27.3% at one close (AMD jumped 52% on 22 April 2016); trimmed at the next open, the only close above 25% |
| beta-adjusted gross <= 1.5x | 1.39x | 1.43x at a close |
| <= 50 trades a day | | 6 |
| decide() <= 5 s | | under 0.1 s |

Hard guards (27% a name, 1.45x gross) sit below the published limits; with these settings they never bind. Long only.
The live board fetches 42 tickers; the agent uses whatever it is given.

## Tests

`backtest.py` is an independent re-implementation of the published rules (no builderr code is run):

- `admission` follows `preview.py`: decide after the close on all bars up to that day, fill at the next open with 5 bp
  slippage (10 bp for leveraged ETFs), buys cut to cash, gross and concentration checked at each close.
- `history` follows `run_bot()` in `live_runner.py`: decide on bars through the prior close, fill at the session open,
  sells before buys, buys cut to cash and to the engine's 30% and 1.5x room at the open.

```bash
python -m unittest discover -s tests                       # synthetic checks: caps, cash in a downtrend, data gaps
python backtest.py admission sample_regimes.json.gz        # the three public sample windows (file from the builderr template)
python backtest.py history <dir of daily bars as JSON>     # long history (data not included)
```

Admission sample windows, `preview.py` timing (all PASS: no errors, gross at most 1.42x, no name above 21.7%):

| Window | Agent return | Agent max drawdown | QQQ over the same sessions |
|---|---|---|---|
| calm uptrend (Jun to Aug 2021) | +7.1% | 6.5% | +14.2% |
| moderate selloff (Sep to Oct 2021) | -6.2% | 12.8% | -3.9% |
| volatility spike and snapback (Feb to Apr 2020) | -20.7% | 22.0% | -7.2% |

The weakness is visible in 2020: the trend filter left late (the long-history book lost 24% from the close of 19 February
to the close of 28 February, when it went to cash) and, after a one-day re-entry on 3 March, sat in cash through the
whole rebound (QQQ +29.6% from 16 March to 29 April) until 27 May.

Long history with daily bars of the 42 live tickers (adjusted prices), 3 January 2011 to 23 October 2026: CAGR 36.1%,
volatility 29.3%, Sharpe 1.20, maximum drawdown 33.1%, invested on 84% of days (QQQ buy-and-hold: 18.5%, 20.7%, 0.92,
35.1%). **These numbers are inflated** and are here only as a test of the limits and the drawdown: the 42 tickers are
names that the board picked in 2026, so the momentum picks of 2011 to 2026 were chosen from names already known to have
done well. Changing each setting by about 20% (calm and crash thresholds, number of names, momentum and trend windows)
gives a Sharpe between 1.15 and 1.34 on the same data, and every variant passes the three sample windows.

- 2006 to 2010 (TQQQ, and with it the sleeve, only from February 2010): 10.2% a year, maximum drawdown 28.8% (QQQ 6.2%,
  53.4%).
- 2022 (from cash on 3 January): -23.5% (QQQ -32.8% from the same open). Almost all of it came from January to April,
  while SPY crossed its 200-day average nine times; from May the book was in cash except on two single days.

Risks a reader should know: TQQQ is held on about 74% of all days (87% of invested days), so the sleeve is the normal
state of a calm uptrend, not an exception; three or four of the four names are semiconductor stocks or ETFs on 29% of
invested days; and fast breaks hurt more than slow ones.

## Not advice

This is an entry in a paper-trading competition. It is not investment advice and comes with no warranty (MIT licence).
Anyone who runs it with real money decides that alone.

## Contact

brierwise@proton.me
