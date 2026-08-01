# backtest_score.py - step 3f.
# Part A: score saved drawdown/price forecasts against realized outcomes.
# Part B: test whether the pipeline's 5-tier signal carries information, using
#         per-ticker EXCESS returns so tickers can be pooled fairly.

import json, glob, re
import datetime as dt
from pathlib import Path
from collections import defaultdict

from tradingagents.dataflows.stockstats_utils import load_ohlcv, compute_max_drawdown

HORIZON_CAL_DAYS = 90        # ~63 trading days
TODAY = dt.date.today()

# 5-tier scale, most to least bullish. Order matters for the monotonicity read.
TIERS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def discover_tickers(model_tag):
    """Which tickers have records saved for this model."""
    found = set()
    for f in glob.glob(f"backtest_results/{model_tag}/*_*.json"):
        m = re.match(r"([A-Z.\-]+)_\d{4}-\d{2}-\d{2}\.json$", Path(f).name)
        if m:
            found.add(m.group(1))
    return sorted(found)


def _close_on_or_before(ticker, date_iso):
    """Last close at or before date_iso. load_ohlcv already filters to <= date_iso."""
    px = load_ohlcv(ticker, date_iso)
    if px.empty:
        return None
    return float(px["Close"].iloc[-1])


def _horizon_end(date_iso):
    return dt.date.fromisoformat(date_iso) + dt.timedelta(days=HORIZON_CAL_DAYS)


def _mean(xs):
    return sum(xs) / len(xs)


def _median(xs):
    g = sorted(xs)
    m = len(g) // 2
    return g[m] if len(g) % 2 else (g[m - 1] + g[m]) / 2


# ---------------------------------------------------------------- Part A ----

def score_one(path):
    rec = json.loads(Path(path).read_text())
    ticker = rec["ticker"]
    fc = rec.get("drawdown_forecast") or {}
    if "error" in fc or not fc:
        return {"date": rec["date"], "skip": "no forecast"}

    end = _horizon_end(rec["date"])
    if end >= TODAY:
        return {"date": rec["date"], "skip": "horizon not complete"}

    # lookback 91 so the window includes the prediction date itself
    realized = compute_max_drawdown(ticker, end.isoformat(), lookback_days=91)
    if realized.get("max_drawdown_pct") is None:
        return {"date": rec["date"], "skip": "no realized data"}

    realized_price = _close_on_or_before(ticker, end.isoformat())
    if realized_price is None:
        return {"date": rec["date"], "skip": "no realized price"}

    r_dd = realized["max_drawdown_pct"]
    return {
        "ticker": ticker,
        "date": rec["date"],
        "signal": rec.get("signal"),
        "dd_realized": r_dd,
        "dd_expected": fc["expected_max_drawdown_pct"],
        "dd_p95": fc["p95_worst_drawdown_pct"],
        "breach_p95": r_dd < fc["p95_worst_drawdown_pct"],
        "price_realized": round(realized_price, 2),
        "price_expected": fc["expected_price"],
        "in_p5_p95": fc["price_p5"] <= realized_price <= fc["price_p95"],
        "ratio": fc.get("return_over_risk"),
    }


def _print_calibration(rows, label):
    n = len(rows)
    breaches = sum(r["breach_p95"] for r in rows)
    inside = sum(r["in_p5_p95"] for r in rows)
    worse = sum(r["dd_realized"] < r["dd_expected"] for r in rows)
    print(f"\n[{label}] n={n}")
    print(f"  p95 breach rate:            {breaches}/{n} = {breaches/n:.0%}   (well-calibrated ~5%)")
    print(f"  price inside p5-p95:        {inside}/{n} = {inside/n:.0%}   (expected ~90%)")
    print(f"  realized worse than median: {worse}/{n} = {worse/n:.0%}   (expected ~50%)")


def score_ticker(model_tag, ticker, verbose=True):
    """Score one ticker. Returns the scored rows so the caller can pool them."""
    files = sorted(glob.glob(f"backtest_results/{model_tag}/{ticker}_*.json"))
    rows, skipped = [], []
    for f in files:
        r = score_one(f)
        (skipped if "skip" in r else rows).append(r)

    print(f"\n--- {model_tag} / {ticker}: {len(rows)} scored, {len(skipped)} skipped ---")
    if not rows:
        return rows

    if verbose:
        hdr = (f"{'date':<12}{'signal':<12}{'dd_real':>9}{'dd_exp':>9}{'dd_p95':>9}"
               f"{'brch':>6}{'px_real':>9}{'px_exp':>9}{'in90':>6}")
        print(hdr)
        for r in rows:
            print(f"{r['date']:<12}{str(r['signal'])[:11]:<12}{r['dd_realized']:>9.2f}"
                  f"{r['dd_expected']:>9.2f}{r['dd_p95']:>9.2f}{str(r['breach_p95']):>6}"
                  f"{r['price_realized']:>9.2f}{r['price_expected']:>9.2f}"
                  f"{str(r['in_p5_p95']):>6}")

    _print_calibration(rows, label=ticker)
    return rows


# ---------------------------------------------------------------- Part B ----

def forward_return(rec):
    """Fractional price change over the forecast horizon. None if not yet closed.

    Start price comes from the forecast's own spot_price so the return is measured
    from exactly the price the forecast was built on.
    """
    end = _horizon_end(rec["date"])
    if end >= TODAY:
        return None
    ticker = rec["ticker"]
    fc = rec.get("drawdown_forecast") or {}
    start = fc.get("spot_price") or _close_on_or_before(ticker, rec["date"])
    finish = _close_on_or_before(ticker, end.isoformat())
    if not start or not finish:
        return None
    return finish / start - 1.0


def evaluate_signal(model_tag, tickers):
    """Do more bullish signals precede better forward returns?

    Returns are converted to EXCESS over each ticker's own buy-and-hold mean before
    pooling. Without that, pooling raw returns would measure which ticker rose most,
    not whether the signals carried information.

    This is the only check here that involves the agents at all - the drawdown
    forecast is deterministic numpy and would be identical with no LLM in the pipeline.
    """
    by_ticker = defaultdict(list)
    for ticker in tickers:
        for f in sorted(glob.glob(f"backtest_results/{model_tag}/{ticker}_*.json")):
            rec = json.loads(Path(f).read_text())
            r = forward_return(rec)
            if r is not None:
                by_ticker[ticker].append((rec["date"], str(rec.get("signal")), r))

    if not by_ticker:
        print("\nno records with a closed horizon")
        return

    # Per-ticker baselines, then excess returns pooled across tickers.
    pooled = []
    print(f"\n=== per-ticker buy-and-hold baselines ({HORIZON_CAL_DAYS}-day forward) ===")
    for ticker in sorted(by_ticker):
        rets = [r for _, _, r in by_ticker[ticker]]
        base = _mean(rets)
        print(f"  {ticker:<6} n={len(rets):>3}  mean {base*100:+6.1f}%")
        for date, sig, r in by_ticker[ticker]:
            pooled.append((ticker, date, sig, r - base))

    print(f"\n=== signal vs EXCESS forward return: {model_tag}, "
          f"{len(by_ticker)} ticker(s), n={len(pooled)} ===")
    print("excess = this week's return minus that ticker's own average. "
          "0.0% means 'no better than holding'.")
    print(f"\n{'signal':<14}{'n':>4}{'mean':>9}{'median':>9}{'worst':>9}{'best':>9}")

    for tier in TIERS:
        g = [e for _, _, s, e in pooled if s == tier]
        if not g:
            continue
        print(f"{tier:<14}{len(g):>4}{_mean(g)*100:>8.1f}%{_median(g)*100:>8.1f}%"
              f"{min(g)*100:>8.1f}%{max(g)*100:>8.1f}%")

    unknown = sorted({s for _, _, s, _ in pooled} - set(TIERS))
    for s in unknown:
        n = sum(1 for _, _, sig, _ in pooled if sig == s)
        print(f"  unrecognised signal value {s!r} on {n} week(s) - not bucketed")

    bull = [e for _, _, s, e in pooled if s in ("Buy", "Overweight")]
    caut = [e for _, _, s, e in pooled if s in ("Hold", "Underweight", "Sell")]
    if bull and caut:
        print(f"\nbullish  (Buy/Overweight)   n={len(bull):>3}: {_mean(bull)*100:+.1f}%")
        print(f"cautious (Hold/Under/Sell)  n={len(caut):>3}: {_mean(caut)*100:+.1f}%")
        print(f"spread: {(_mean(bull)-_mean(caut))*100:+.1f} pp  "
              f"(positive = signal added information)")
    else:
        print("\nonly one bucket populated - no spread to compute")

    print("\nread with care: 90-day windows stepping 7 days overlap ~83 days, so these are\n"
          "far fewer independent observations than n suggests (log 34c). With 4 tickers at\n"
          "avg correlation ~0.14 the effective count is ~17, not the raw n above (log 35f).")


if __name__ == "__main__":
    MODEL = "deepseek"
    tickers = discover_tickers(MODEL)
    print(f"tickers found: {', '.join(tickers)}")

    all_rows = []
    for t in tickers:
        all_rows += score_ticker(MODEL, t, verbose=True)

    if len(tickers) > 1 and all_rows:
        print("\n" + "=" * 60)
        _print_calibration(all_rows, label="ALL TICKERS POOLED")

    evaluate_signal(MODEL, tickers)
