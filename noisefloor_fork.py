# noisefloor_fork.py - run-to-run variance baseline for the fork.
# Runs the pipeline N times on ONE date, same config, isolated memory each time.
# Purpose: measure how much the 5-tier signal moves with no code change at all,
# so a fork-vs-upstream difference can be judged against ordinary variance.
#
# Writes to noisefloor_results/, NOT backtest_results/, so backtest_score.py
# never sees these records (they would corrupt the 280-row four-ticker set).

import json, copy, sys, pathlib
import datetime as dt
from pathlib import Path
import time

# --- package guard: abort if `tradingagents` resolves to the site-packages
# snapshot (v0.2.5) or to the other repo. CWD precedence normally handles this,
# but a wrong resolution produces plausible-looking results with no error, which
# is the one failure mode that would silently invalidate the comparison.
import tradingagents
_expected = pathlib.Path(__file__).resolve().parent
_actual = pathlib.Path(tradingagents.__file__).resolve().parent.parent
if _actual != _expected:
    sys.exit(f"WRONG PACKAGE: imported {_actual}, expected {_expected}")
print(f"package OK: {_actual}")

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

RESULTS_ROOT = Path("noisefloor_results")

TICKER    = "NVDA"
DATE      = "2025-04-28"   # fork rated Overweight here (log 36g); just after the April 2025 bottom
N_RUNS    = 5
VARIANT   = "fork"


def deepseek_config():
    # Verbatim from backtest_generate.py. Must not drift - the noise floor has to
    # describe the same conditions the 312 backtest records were generated under.
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["llm_provider"]    = "deepseek"
    cfg["quick_think_llm"] = "deepseek-chat"
    cfg["deep_think_llm"]  = "deepseek-chat"
    cfg["backend_url"]     = "https://api.deepseek.com"
    return cfg


def run_noise_floor():
    base_cfg = deepseek_config()
    out_dir = RESULTS_ROOT / VARIANT
    out_dir.mkdir(parents=True, exist_ok=True)

    signals = []
    for i in range(1, N_RUNS + 1):
        # Run-indexed filename: without this the out_path.exists() skip pattern
        # would return one record and four no-ops.
        out_path = out_dir / f"{TICKER}_{DATE}_run{i}.json"
        if out_path.exists():
            print(f"skip run{i} (exists)")
            continue

        run_cfg = copy.deepcopy(base_cfg)
        mem_path = out_dir / ".mem" / f"{TICKER}_{DATE}_run{i}.md"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.unlink(missing_ok=True)   # stale .mem contaminates the next run
        run_cfg["memory_log_path"] = str(mem_path.resolve())
        run_cfg["use_drift"] = False       # fork-only key; dropped in the upstream twin

        graph = TradingAgentsGraph(config=run_cfg)
        try:
            final_state, signal = graph.propagate(TICKER, DATE)
            record = {
                "variant": VARIANT,
                "run": i,
                "ticker": TICKER,
                "date": DATE,
                "signal": signal,
                "trader_decision": final_state.get("trader_investment_decision"),
                "drawdown_forecast": final_state["drawdown_forecast"],   # fork-only
                "final_trade_decision": final_state["final_trade_decision"],
                "run_started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            out_path.write_text(json.dumps(record, indent=2))
            signals.append(signal)
            print(f"done run{i}: {signal}")
        except Exception as e:
            print(f"FAIL run{i}: {e}")

        # Yahoo pacing, not optional (log 34e, validated 36h). Outside the
        # try/except so a failed run still backs off.
        time.sleep(20)

    print("\n--- signals this session ---")
    for s in signals:
        print(f"  {s}")
    print(f"distinct: {sorted(set(signals))}")


if __name__ == "__main__":
    run_noise_floor()
