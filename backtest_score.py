# backtest_score.py - step 3f: score saved forecasts against realized outcomes.
# For each saved record, look up what NVDA actually did over the forecast
# horizon and compare against the forecast distribution.

import json, glob
import datetime as dt
from pathlib import Path

from tradingagents.dataflows.stockstats_utils import load_ohlcv, compute_max_drawdown

TICKER = "NVDA"
HORIZON_CAL_DAYS = 90        # ~63 trading days
TODAY = dt.date.today()

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

    prices = load_ohlcv(TICKER, end.isoformat())
    realized_price = float(prices["Close"].iloc[-1])

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

    hdr = f"{'date':<12}{'signal':<12}{'dd_real':>9}{'dd_exp':>9}{'dd_p95':>9}{'brch':>6}{'px_real':>9}{'px_exp':>9}{'in90':>6}"
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

if __name__ == "__main__":
    score_dir("deepseek")
