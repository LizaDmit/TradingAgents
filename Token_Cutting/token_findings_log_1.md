# Token Reduction - Findings Log

Working notes for the TradingAgents token-reduction analysis. Source: setup.py, conditional_logic.py, default_config.py (graph layer). This is an interim record to feed the later report, not the report itself.

## 1. Where the round counts live and what they mean

The two deliberation loops are controlled by config values:
- max_debate_rounds (default 1) - bull/bear debate
- max_risk_discuss_rounds (default 1) - aggressive/conservative/neutral risk debate

Both are defined in default_config.py and passed into ConditionalLogic, which decides when each loop stops.

The stop conditions in conditional_logic.py:
- Debate stops when count >= 2 * max_debate_rounds
- Risk stops when count >= 3 * max_risk_discuss_rounds

Translated to actual agent turns at the default of 1:
- Debate = 2 turns total (one Bull, one Bear), then Research Manager
- Risk = 3 turns total (one Aggressive, one Conservative, one Neutral), then Portfolio Manager
- Total deliberation at default = 5 LLM calls

## 2. Warning: the code comments are stale

Next to the stop conditions, comments say "3 rounds of back-and-forth." The arithmetic does not support that at the default (2 * 1 = 2 turns, 3 * 1 = 3 turns). The comments are leftovers from an older, higher default. Trust the math, not the comment.

## 3. Key inference about the 222k token / ~97 page NVDA run

The sample NVDA output shows:
- Bull speaks 3 times, Bear speaks 3 times = 6 debate turns
- Aggressive, Conservative, Neutral each speak 3 times = 9 risk turns

Running that back through the formulas:
- 6 debate turns means 2 * max_debate_rounds = 6, so max_debate_rounds = 3
- 9 risk turns means 3 * max_risk_discuss_rounds = 9, so max_risk_discuss_rounds = 3

Conclusion: the bloated run used 3 rounds each, not the current default of 1. That raises deliberation from 5 calls to 15. Token cost grows faster than 3x because each later turn re-reads all prior turns (input re-reading was already ~8.5x the output in that run: 222k in vs 26k out).

Most likely explanation: that run came from an older version where 3 was the default. The uploaded config now shows 1. So part of the token problem may already be reduced just by running today's config. This is why a fresh, measured run is needed before deciding where to cut further.

## 4. Other token levers found in default_config.py

These inject raw text straight into analyst prompts. The config file itself notes they can be lowered to reduce token usage:
- news_article_limit: 20 (full articles per ticker)
- global_news_article_limit: 10
- global_news_lookback_days: 7
- global_news_queries: 5 macro search queries

Trimming these is a cheap, safe cut that does not touch pipeline logic.

## 5. Rounds and other settings are changeable for free (no code edit)

default_config.py exposes env-var overrides via the _ENV_OVERRIDES map. Relevant ones:
- TRADINGAGENTS_MAX_DEBATE_ROUNDS
- TRADINGAGENTS_MAX_RISK_ROUNDS
- TRADINGAGENTS_QUICK_THINK_LLM and TRADINGAGENTS_DEEP_THINK_LLM
- TRADINGAGENTS_TEMPERATURE

Set these in a .env file to change behavior without editing code.

## 6. Graph structure facts (from setup.py) useful for the report

Model assignment:
- quick_thinking_llm runs: the 4 analysts, both researchers, the trader, and all 3 risk debators
- deep_thinking_llm runs: only 2 nodes - Research Manager and Portfolio Manager

This matters for cost because the cheaper/quicker model already covers most calls; the expensive deep model is used sparingly.

Flow:
- Analysts run sequentially (analyst_concurrency_limit default 1). Each analyst loops with its own tool node until it stops requesting tools, then hits a "Msg Clear" node that wipes its message history before the next stage.
- Pipeline order: analysts -> Bull/Bear debate -> Research Manager -> Trader -> risk debate (Aggressive/Conservative/Neutral) -> Portfolio Manager -> END

Note: the "Msg Clear" nodes already discard each analyst's intermediate tool-call messages, so analyst tool chatter does not carry forward into later stages. This is relevant - it means the debate/manager stages are fed the finished reports, not the raw tool transcripts.

## 7. Open question / next step

Unresolved: what max_debate_rounds was set to when the 222k number was measured (likely 3). This decides the strategy:
- If that run already used 1 round each, the tokens come from data and report re-feeding -> target news limits and full-reports-passed-downstream.
- If it used 3, dropping to 1 is the single biggest and free cut.

Still required: one instrumented run to get the per-stage token breakdown (item #1 from the information-gathering list). The static files cannot provide this.
