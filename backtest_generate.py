# backtest_generate.py - step 3a: headless generate loop.
# Runs the pipeline once per weekly date for one model, saves decision + forecast.
# Memory OFF (weeks independent, models isolated). Generating only; scoring is a
# separate pass over these files.

import os, json, copy
import datetime as dt
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

TICKER = "NVDA"
RESULTS_ROOT = Path("backtest_results")   # one subdir per model tag

# --- model configs: start from DEFAULT_CONFIG, override only what differs ---
def openai_config():
    return copy.deepcopy(DEFAULT_CONFIG)   # DEFAULT_CONFIG is already your OpenAI setup

def deepseek_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["llm_provider"]    = "deepseek"
    cfg["quick_think_llm"] = "deepseek-chat"       # non-thinking, cheap, structured-output safe
    cfg["deep_think_llm"]  = "deepseek-chat"   # was deepseek-reasoner; reasoner fails structured output on manager nodes
    cfg["backend_url"]     = "https://api.deepseek.com"
    return cfg

MODELS = {"openai": openai_config, "deepseek": deepseek_config}

def weekly_dates(start, num_weeks, step_days=7):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=step_days * i)).isoformat() for i in range(num_weeks)]

def generate(model_tag, start, num_weeks):
    base_cfg = MODELS[model_tag]()
    out_dir = RESULTS_ROOT / model_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    for date in weekly_dates(start, num_weeks):
        out_path = out_dir / f"{TICKER}_{date}.json"
        if out_path.exists():
            print(f"skip {date} (exists)")
            continue

        # Memory OFF + isolation: fresh unique memory file per run.
        run_cfg = copy.deepcopy(base_cfg)
        mem_path = (out_dir / ".mem" / f"{TICKER}_{date}.md")
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        run_cfg["memory_log_path"] = str(mem_path.resolve())

        graph = TradingAgentsGraph(config=run_cfg)   # rebuilt per run so the mem path takes effect
        try:
            final_state, signal = graph.propagate(TICKER, date)
            record = {
                "ticker": TICKER,
                "date": date,
                "model_tag": model_tag,
                "signal": signal,                                            # PM rating (e.g. Overweight)
                "trader_decision": final_state.get("trader_investment_decision"),  # 3-tier Buy/Hold/Sell
                "drawdown_forecast": final_state["drawdown_forecast"],
                "final_trade_decision": final_state["final_trade_decision"], # PM text, for audit
            }
            out_path.write_text(json.dumps(record, indent=2))
            print(f"done {date}: {signal}")
        except Exception as e:
            print(f"FAIL {date}: {e}")   # log and continue; rerun fills the gap

if __name__ == "__main__":
    # generate("openai", start="2025-01-06", num_weeks=10)          # 3b - already done
    generate("deepseek", start="2025-01-06", num_weeks=10)           # 3c - test 1 week first
