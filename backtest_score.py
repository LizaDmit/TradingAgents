# backtest_score.py - step 3f.
# Part A: score saved drawdown/price forecasts against realized outcomes.
# Part B: test whether the pipeline's 5-tier signal carries any information,
#         by comparing forward returns across signal buckets.

import json, glob
import datetime as dt
from pathlib import Path

from tradingagents.dataflows.stockstats_utils import load_ohlcv, compute_max_drawdown

TICKER = "NVDA"
HORIZON_CAL_DAYS = 90        # ~63 trading days
TODAY = dt.date.today()

# 5-tier scale, most to least bullish. Order matters for the monotonicity read.
TIERS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def _close_on_or_before(date_iso):
    """Last close at or before date_iso. load_ohlcv already filters to <= date_iso."""
    px = load_ohlcv(TICKER, date_iso)
    if px.empty:
        return None
    return float(px["Close"].iloc[-1])


# ---------------------------------------------------------------- Part A ----

def score_one(path):
    rec = json.loads(Path(path).read_text())
    fc = rec.get("drawdown_forecast") or {}
    if "error" in fc or not fc:
        return {"date": rec["date"], "skip": "no forecast"}

    pred = dt.date.fromisoformat(rec["date"])
    end = pred + dt.timedelta(days=HORIZON_CAL_DAYS)
    if end >= TODAY:
        return {"date": rec["date"], "skip": "horizon not complete"}

    # lookback 91 so the window includes the prediction date itself
    realized = compute_max_drawdown(TICKER, end.isoformat(), lookback_days=91)
    if realized.get("max_drawdown_pct") is None:
        return {"date": rec["date"], "skip": "no realized data"}

    realized_price = _close_on_or_before(end.isoformat())
    r_dd = realized["max_drawdown_pct"]
    return {
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


def score_dir(model_tag):
    files = sorted(glob.glob(f"backtest_results/{model_tag}/{TICKER}_*.json"))
    rows, skipped = [], []
    for f in files:
        r = score_one(f)
        (skipped if "skip" in r else rows).append(r)

    print(f"\n{model_tag}: {len(rows)} scored, {len(skipped)} skipped")
    for s in skipped:
        print(f"  skip {s['date']}: {s['skip']}")
    if not rows:
        return

    hdr = (f"{'date':<12}{'signal':<12}{'dd_real':>9}{'dd_exp':>9}{'dd_p95':>9}"
           f"{'brch':>6}{'px_real':>9}{'px_exp':>9}{'in90':>6}")
    print("\n" + hdr)
    for r in rows:
        print(f"{r['date']:<12}{str(r['signal'])[:11]:<12}{r['dd_realized']:>9.2f}"
              f"{r['dd_expected']:>9.2f}{r['dd_p95']:>9.2f}{str(r['breach_p95']):>6}"
              f"{r['price_realized']:>9.2f}{r['price_expected']:>9.2f}{str(r['in_p5_p95']):>6}")

    n = len(rows)
    breaches = sum(r["breach_p95"] for r in rows)
    inside = sum(r["in_p5_p95"] for r in rows)
    worse = sum(r["dd_realized"] < r["dd_expected"] for r in rows)
    print(f"\np95 breach rate: {breaches}/{n} = {breaches/n:.0%}  (well-calibrated ~5%)")
    print(f"price inside p5-p95: {inside}/{n} = {inside/n:.0%}  (expected ~90%)")
    print(f"realized worse than median forecast: {worse}/{n} = {worse/n:.0%}  (expected ~50%)")


# ---------------------------------------------------------------- Part B ----

def forward_return(rec):
    """Fractional price change over the forecast horizon. None if not yet closed.

    Start price is taken from the forecast's own spot_price so the return is
    measured from exactly the price the forecast was built on. Falls back to a
    lookup for older records that predate that field.
    """
    pred = dt.date.fromisoformat(rec["date"])
    end = pred + dt.timedelta(days=HORIZON_CAL_DAYS)
    if end >= TODAY:
        return None
    fc = rec.get("drawdown_forecast") or {}
    start = fc.get("spot_price") or _close_on_or_before(rec["date"])
    finish = _close_on_or_before(end.isoformat())
    if not start or not finish:
        return None
    return finish / start - 1.0


def _mean(xs):
    return sum(xs) / len(xs)


def _median(xs):
    g = sorted(xs)
    m = len(g) // 2
    return g[m] if len(g) % 2 else (g[m - 1] + g[m]) / 2


def evaluate_signal(model_tag):
    """Do more bullish signals precede better forward returns?

    This is the only check here that involves the agents at all - the drawdown
    forecast is deterministic numpy and would be identical with no LLM in the
    pipeline. The baseline matters: NVDA rose over this window, so a bullish
    bucket looking good proves nothing unless it beats simply holding.
    """
    files = sorted(glob.glob(f"backtest_results/{model_tag}/{TICKER}_*.json"))
    rows = []
    for f in files:
        rec = json.loads(Path(f).read_text())
        r = forward_return(rec)
        if r is None:
            continue
        rows.append((rec["date"], str(rec.get("signal")), r))

    if not rows:
        print(f"\n{model_tag}: no records with a closed horizon")
        return

    baseline = _mean([r for _, _, r in rows])

    print(f"\n=== signal vs forward {HORIZON_CAL_DAYS}-day return: {model_tag}, n={len(rows)} ===")
    print(f"{'signal':<14}{'n':>4}{'mean':>9}{'median':>9}{'worst':>9}{'best':>9}{'vs hold':>9}")

    for tier in TIERS:
        g = [r for _, s, r in rows if s == tier]
        if not g:
            continue
        print(f"{tier:<14}{len(g):>4}{_mean(g)*100:>8.1f}%{_median(g)*100:>8.1f}%"
              f"{min(g)*100:>8.1f}%{max(g)*100:>8.1f}%{(_mean(g)-baseline)*100:>+8.1f}%")

    unknown = sorted({s for _, s, _ in rows} - set(TIERS))
    for s in unknown:
        n = sum(1 for _, sig, _ in rows if sig == s)
        print(f"  unrecognised signal value {s!r} on {n} week(s) - not bucketed")

    print(f"\nbuy-and-hold baseline (mean over all {len(rows)} weeks): {baseline*100:+.1f}%")

    bull = [r for _, s, r in rows if s in ("Buy", "Overweight")]
    caut = [r for _, s, r in rows if s in ("Hold", "Underweight", "Sell")]
    if bull and caut:
        spread = _mean(bull) - _mean(caut)
        print(f"bullish  (Buy/Overweight)      n={len(bull):>3}: {_mean(bull)*100:+.1f}%")
        print(f"cautious (Hold/Under/Sell)     n={len(caut):>3}: {_mean(caut)*100:+.1f}%")
        print(f"spread: {spread*100:+.1f} pp  (positive = signal added information)")
    else:
        print("only one bucket populated - no spread to compute")

    print("\nread with care: 90-day windows stepping 7 days overlap ~83 days, so these\n"
          "are far fewer independent observations than n suggests (see log 34c).\n"
          "One ticker, one mostly-rising window. Direction is suggestive, not significant.")


if __name__ == "__main__":
    score_dir("deepseek")
    evaluate_signal("deepseek")
