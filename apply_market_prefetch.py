"""
Method 3 implementation: convert the market analyst from a multi-call tool
loop into a single pre-fetched call, mirroring the sentiment analyst pattern.

WHAT IT DOES
  - Backs up the current market_analyst.py to market_analyst.py.bak
  - Replaces it with a version that:
      1. Calls get_stock_data, get_indicators (x7 fixed indicators), and
         get_verified_market_snapshot directly in Python (no LLM involved,
         no token cost per call).
      2. Builds ONE prompt with all that data already embedded.
      3. Makes a SINGLE LLM call with no bound tools.
  - No other file is touched. setup.py / conditional_logic.py do not need
    to change: with no tool_calls on the response, the existing
    should_continue_market check already routes straight to "Msg Clear
    Market" on the first pass.

FIXED INDICATOR SET (matches what past NVDA reports actually used):
  close_10_ema, close_50_sma, close_200_sma, rsi, macd, macds, atr

HOW TO USE
  python apply_market_prefetch.py

HOW TO REVERT
  python apply_market_prefetch.py --revert

SAFE TO RE-RUN: skips if already patched.
"""

import os
import sys

ROOT = "tradingagents"
TARGET_NAME = "market_analyst.py"

NEW_CONTENT = '''"""Market analyst - technical analysis for a target ticker.

Converted from a multi-call tool-loop pattern to a single pre-fetched call,
mirroring the sentiment analyst redesign. The old version let the LLM choose
indicators and call tools across up to 3 model calls per run, re-reading its
own accumulating context each pass. This version fetches a fixed, proven set
of indicators directly in Python (no LLM involved, no token cost per fetch)
and injects them into the prompt from turn 0, then makes a single LLM call.

Fixed indicator set (matches what prior reports actually used):
  close_10_ema, close_50_sma, close_200_sma, rsi, macd, macds, atr
"""

from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_indicators,
    get_language_instruction,
    get_stock_data,
    get_verified_market_snapshot,
)

INDICATOR_SET = [
    ("close_10_ema", "10 EMA - responsive short-term trend average"),
    ("close_50_sma", "50 SMA - medium-term trend indicator"),
    ("close_200_sma", "200 SMA - long-term trend benchmark"),
    ("rsi", "RSI - momentum, overbought/oversold"),
    ("macd", "MACD - momentum via EMA differences"),
    ("macds", "MACD Signal - smoothing of the MACD line"),
    ("atr", "ATR - volatility, for stop-loss / position sizing"),
]


def _lookback_start(trade_date: str, days: int = 90) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        start_date = _lookback_start(current_date)
        instrument_context = get_instrument_context_from_state(state)

        # --- Pre-fetch all data in plain Python. No LLM call, no token cost here. ---
        stock_data = get_stock_data.func(ticker, start_date, current_date)

        indicator_blocks = []
        for name, description in INDICATOR_SET:
            try:
                value = get_indicators.func(ticker, name, current_date, look_back_days=30)
            except Exception as exc:  # fail open - don't block the run on one indicator
                value = f"<unavailable: {exc}>"
            indicator_blocks.append(f"### {name} ({description})\\n{value}")
        indicators_text = "\\n\\n".join(indicator_blocks)

        snapshot = get_verified_market_snapshot.func(ticker, current_date)

        system_message = f"""You are a trading assistant analyzing financial markets for {ticker}. The following market data has already been collected for you - do not request additional data, analyze what is provided.

## Stock price data (OHLCV), {start_date} to {current_date}
<start_of_price_data>
{stock_data}
<end_of_price_data>

## Technical indicators (as of {current_date})
<start_of_indicators>
{indicators_text}
<end_of_indicators>

## Verified market snapshot (source of truth for exact figures)
Treat this as the source of truth for any exact OHLCV, price-level, or indicator-value claim. If another block above conflicts with it, flag the discrepancy rather than inventing a reconciled number.
<start_of_snapshot>
{snapshot}
<end_of_snapshot>

Write a detailed and nuanced report of the trends you observe, covering trend direction (moving averages), momentum (RSI, MACD), and volatility (ATR). Provide specific, actionable insights with supporting evidence to help traders make informed decisions. Do not claim historical validation, support/resistance bounces, or exact percentage moves unless directly supported by the data above with concrete dates and prices. Append a Markdown table at the end of the report to organize key points, organized and easy to read.""" + get_language_instruction()

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\\n{system_message}\\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # No bind_tools - the data is already in the prompt. Single call.
        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "market_report": result.content,
        }

    return market_analyst_node
'''


def find(basename):
    for dirpath, _, files in os.walk(ROOT):
        if basename in files:
            return os.path.join(dirpath, basename)
    return None


def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak):
        with open(path) as f:
            open(bak, "w").write(f.read())


def revert():
    path = find(TARGET_NAME)
    bak = (path + ".bak") if path else None
    if not bak or not os.path.exists(bak):
        print("No backup found - nothing to revert.")
        return
    open(path, "w").write(open(bak).read())
    os.remove(bak)
    print("Reverted", path)


def main():
    if not os.path.isdir(ROOT):
        print(f"ERROR: run this from the TradingAgents project root (no {ROOT}/ here)")
        sys.exit(1)

    if "--revert" in sys.argv:
        revert()
        return

    path = find(TARGET_NAME)
    if not path:
        print(f"ERROR: {TARGET_NAME} not found")
        sys.exit(1)

    current = open(path).read()
    if "Converted from a multi-call tool-loop pattern" in current:
        print("Already patched, skipping:", path)
        return

    backup(path)
    open(path, "w").write(NEW_CONTENT)
    print("Patched", path)
    print("\\nNow run:  python token_count.py")
    print("Compare the Market Analyst row to its old range (17,306 - 30,876) and call count (was 3-4, should now be 1).")
    print("To undo:  python apply_market_prefetch.py --revert")


if __name__ == "__main__":
    main()
