"""Forward maximum-drawdown forecast via Monte Carlo simulation.

Estimates the distribution of the maximum drawdown a symbol is likely to
experience over a forward horizon, by simulating many possible price paths
from historical return behaviour.

Look-ahead safe: return parameters are estimated only from data on or before
curr_date, because load_ohlcv already filters rows to <= curr_date. That makes
this usable inside a backtest without leaking future prices.

Two simulation methods:
  - "gbm":       geometric Brownian motion. Daily log returns drawn from a
                 Normal(mu, sigma) fitted to the estimation window. Textbook
                 baseline; assumes normally distributed returns.
  - "bootstrap": resamples actual historical daily returns with replacement.
                 Captures the real return distribution, including fat tails,
                 without assuming normality. Usually the more realistic of the two.

The drawdown forecast is a deterministic quant computation - no LLM, no tokens.
"""

import numpy as np
from .stockstats_utils import load_ohlcv
from functools import lru_cache
import pandas as pd

@lru_cache(maxsize=1)
def _irx_history():
    """13-week T-bill yield history, annualized percent. One network call per process."""
    import yfinance as yf
    s = yf.Ticker("^IRX").history(period="10y", auto_adjust=False)["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s


def risk_free_annual(curr_date: str) -> tuple[float, bool]:
    """Annualized risk-free rate (as a fraction) as of curr_date.

    Look-ahead safe: filters to <= curr_date, mirroring load_ohlcv. Returns
    (rate, ok); ok=False means the fetch failed and 0.0 is a placeholder -
    that must stay visible in the output, not silently pass as a real rate.
    """
    try:
        s = _irx_history()
        s = s[s.index <= pd.Timestamp(curr_date)]
        if len(s) == 0:
            return 0.0, False
        return float(s.iloc[-1]) / 100.0, True
    except Exception:
        return 0.0, False

def forecast_max_drawdown(
    symbol: str,
    curr_date: str,
    horizon_days: int = 63,        # ~3 trading months
    estimation_days: int = 252,    # ~1 year of history to fit mu/sigma
    n_sims: int = 10000,
    method: str = "bootstrap",     # "bootstrap" or "gbm"
    use_drift: bool = True,        # include the historical trend, or zero it out
    seed: int | None = 42,
) -> dict:
    """Monte Carlo forecast of the forward maximum drawdown over horizon_days.

    Returns a distribution summary of the worst peak-to-trough drop expected
    over the next horizon_days, expressed as negative percentages. The
    "expected" figure is the median across simulated paths; the p95/p99 figures
    are conservative tail estimates (e.g. p95 = "95% of paths were no worse
    than this").
    """
    data = load_ohlcv(symbol, curr_date)  # already filtered to <= curr_date
    closes = data["Close"].astype(float).dropna()
    if len(closes) < max(30, estimation_days // 4):
        return {"error": "not enough price history", "as_of": curr_date, "symbol": symbol}

    # Daily log returns over the trailing estimation window
    recent = closes.iloc[-estimation_days:]
    log_rets = np.log(recent / recent.shift(1)).dropna().values
    if len(log_rets) < 20:
        return {"error": "not enough returns in window", "as_of": curr_date, "symbol": symbol}

    mu = float(log_rets.mean())          # daily drift
    sigma = float(log_rets.std(ddof=1))  # daily volatility

    rng = np.random.default_rng(seed)

    if method == "bootstrap":
        pool = log_rets if use_drift else (log_rets - log_rets.mean())
        shocks = rng.choice(pool, size=(n_sims, horizon_days), replace=True)
    elif method == "gbm":
        drift = mu if use_drift else 0.0
        shocks = rng.normal(drift, sigma, size=(n_sims, horizon_days))
    else:
        raise ValueError(f"method must be 'bootstrap' or 'gbm', got {method!r}")

    # Build relative price paths starting at 1.0 (today's price = the first peak)
    log_paths = np.cumsum(shocks, axis=1)
    price_paths = np.exp(log_paths)
    price_paths = np.hstack([np.ones((n_sims, 1)), price_paths])

    # Maximum drawdown per simulated path
    running_peak = np.maximum.accumulate(price_paths, axis=1)
    drawdowns = (price_paths - running_peak) / running_peak   # <= 0 everywhere
    path_max_dd = drawdowns.min(axis=1)                       # most negative per path

    dd_pct = path_max_dd * 100.0
    # ---- forward return distribution, from the SAME simulated paths ----
    spot = float(closes.iloc[-1])
    terminal = price_paths[:, -1]        # relative to 1.0
    rets = terminal - 1.0                # fractional return over the horizon

    R_mean = float(np.mean(rets))
    R_med = float(np.median(rets))
    s_ret = float(np.std(rets, ddof=1))

    rf_annual, rf_ok = risk_free_annual(curr_date)
    r_horizon = rf_annual * (horizon_days / 252.0)
    ratio = (R_mean - r_horizon) / s_ret if s_ret > 0 else None
    return {
        "symbol": symbol,
        "as_of": curr_date,
        "horizon_days": horizon_days,
        "method": method,
        "use_drift": use_drift,
        "expected_max_drawdown_pct": round(float(np.median(dd_pct)), 2),   # typical
        "mean_max_drawdown_pct": round(float(np.mean(dd_pct)), 2),
        "p95_worst_drawdown_pct": round(float(np.percentile(dd_pct, 5)), 2),   # 95% no worse than this
        "p99_worst_drawdown_pct": round(float(np.percentile(dd_pct, 1)), 2),   # 99% no worse than this
        "est_annualized_vol_pct": round(sigma * np.sqrt(252) * 100.0, 1),
        "spot_price": round(spot, 2),
        "expected_return_pct": round(R_mean * 100.0, 2),
        "median_return_pct": round(R_med * 100.0, 2),
        "return_vol_pct": round(s_ret * 100.0, 2),
        "risk_free_pct_horizon": round(r_horizon * 100.0, 3),
        "risk_free_available": rf_ok,
        "return_over_risk": round(ratio, 3) if ratio is not None else None,
        "expected_price": round(spot * (1.0 + R_mean), 2),
        "price_p5": round(spot * float(np.percentile(terminal, 5)), 2),
        "price_p95": round(spot * float(np.percentile(terminal, 95)), 2),
        "prob_loss": round(float(np.mean(rets < 0)), 3),
        "n_sims": n_sims,
    }
