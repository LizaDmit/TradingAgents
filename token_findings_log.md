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

## 8. Debate history and report re-injection (from bull_researcher.py, bear_researcher.py, agent_states.py)

This is the core driver of the high input-to-output token ratio (~8.5:1 in the 222k run).

### 8a. The debate history is re-fed in full and grows every turn
Both researcher prompts contain the line "Conversation history of the debate: {history}", where history is the entire running transcript. After each turn the node does history + "\n" + argument and writes it back to investment_debate_state. So each later turn re-reads all earlier turns. At higher round counts this grows faster than linearly.

### 8b. Biggest finding: all four full analyst reports are re-injected on every debate turn
Each bull and bear turn pulls in the finished analyst reports from AgentState:
- market_report
- sentiment_report
- news_report
- fundamentals_report

These reports are large (pages each in the NVDA run) and never change during the debate, yet every turn pays their full token cost again. If the four reports total R tokens and the debate runs T turns, roughly R * T tokens are spent on identical material. This is a constant tax independent of the history handling.

### 8c. How this interacts with round count (#2)
- At 1 round (2 debate turns): history barely grows (first turn has no history, second carries one argument). Dominant debate cost is the four full reports read twice.
- At 3 rounds (6 turns, what the NVDA run used): reports read 6 times AND history balloons to 5 prior arguments by the last turn.
- Takeaway: report re-feeding persists even after cutting rounds; history growth only appears once rounds go above 1. So lowering rounds does not fix the report re-injection cost.

### 8d. Small redundancy
The prompt feeds both full history and current_response ("Last bear/bull argument"). But current_response is already the last line of history (the previous turn appended it), so the latest argument is sent twice on every turn after the first. Minor, but trivially removable.

### 8e. Where it comes from and what is well-behaved
- Reports come from AgentState (finished analyst outputs).
- Debate history accumulates inside investment_debate_state, which is a SEPARATE channel from the messages list inherited from MessagesState.
- The "Msg Clear" nodes (setup.py) wipe the messages channel between analysts, so analyst tool chatter does not leak forward. The debate history is a deliberately persisted string, not cleared. So the re-feeding is by design, not a message-mechanism accident.

### 8f. Loose ends to confirm later
- bull_history and bear_history are accumulated separately in state but are NOT used in the debate prompt (prompt only uses combined history and current_response). Either consumed downstream or dead weight - confirm when reviewing the research manager.
- agent_states.py has a past_context field: memory-log context injected at run start. Another input source to track down - confirm which prompts read it.
- RiskDebateState has the same shape (combined history, per-agent histories, current responses, count), so the risk debate likely repeats this pattern. Confirm when reviewing the risk debator files.

### 8g. Clearest lever from #3
Feed the debators condensed report digests instead of the full reports. That cost is paid on every turn and is independent of round count, so it is the highest-value structural cut found so far.

## 9. Downstream decision agents do NOT re-inject full reports (from research_manager.py, trader.py, portfolio_manager.py)

This corrects an earlier worry that downstream agents each read all four analyst reports in full. For the decision chain, that is false.

### 9a. What each decision agent actually reads
- Research Manager: only instrument_context and the investment debate history. It never touches market_report, sentiment_report, news_report, or fundamentals_report. The history it reads is the accumulated bull/bear arguments (model prose), not the raw reports.
- Trader: the leanest node in the pipeline. Reads only investment_plan (manager's output) plus company_name and instrument_context. No reports, no debate history.
- Portfolio Manager: reads the risk debate history, research_plan (investment_plan), trader_plan, past_context, and instrument_context. No raw reports.

### 9b. Why this matters for the report
The expensive full-report re-injection is localized to the bull/bear debate (section 8), and possibly the risk debate (still unconfirmed). The managers and trader do not do it. So the "condense the reports" digest lever helps the DEBATORS specifically, not the manager/trader/PM.

### 9c. Refined input-cost ranking (from results so far)
1. Bull/bear debate - four full reports times number of turns, plus growing history. Dominant cost.
2. Risk debate - to be confirmed, likely similar shape.
3. Research Manager - one pass over the full debate history.
4. Portfolio Manager - one pass over risk history plus two plans plus past_context.
5. Trader - one investment plan. Cheapest in the pipeline.

### 9d. Loose ends now resolved
- past_context (from agent_states.py): read by the Portfolio Manager, injected as "Lessons from prior decisions and outcomes." Memory-log context, consumed once at the final decision.
- All three decision agents use structured output (bind_structured / invoke_structured_or_freetext), which constrains their OUTPUT to a compact typed shape. Part of why total output stayed at 26k. Reinforces that the problem is input, not output.

### 9e. Minor note (not a token issue)
The Trader's system prompt says to "anchor your reasoning in the analysts' reports," but the reports are never passed to it. Prompt/data mismatch, not a cost problem - the trader works off the plan only.

### 9f. Still open
- bull_history and bear_history are written to state but still not read by any prompt seen so far (research manager reads combined history only). Still possibly dead weight.
- KEY OPEN QUESTION: do the three risk debators re-inject the four full reports the way the bull/bear debators do? The Portfolio Manager does not, but the risk debators might. This decides whether the risk debate is a second copy of the expensive pattern or something lighter. Need the risk debator files (aggressive_debator.py, conservative_debator.py, neutral_debator.py) to close this.

## 10. Raw tool output sizes (item #4) - measured directly in the running environment

Method: called each data tool for NVDA and counted tokens with tiktoken (o200k_base, the GPT-4o/5 family encoding). Token counts are exact; the active vendor is yfinance per data_vendors config.

### 10a. Measured tools (clean numbers)
| Tool | Chars | Tokens |
|---|---|---|
| get_balance_sheet | 6041 | 2503 |
| get_cashflow | 4485 | 1822 |
| fetch_stocktwits_messages | 5205 | 1761 |
| get_income_statement | 3330 | 1388 |
| get_global_news_yfinance | 2013 | 699 |
| get_fundamentals | 708 | 272 |

### 10b. Key reading of the numbers
- The fundamentals analyst pulls four tools: the curated get_fundamentals summary (272, trivial) plus the three statement tools. The statement trio (income 1388 + cashflow 1822 + balance sheet 2503) is about 5,700 tokens together - the heaviest cluster measured. So the lean summary masked the real fundamentals input cost; the statements are 5-9x larger each.
- get_balance_sheet at 2503 is the single heaviest tool measured so far.
- StockTwits (1761) is a moderate single hit feeding the sentiment analyst.
- get_global_news (macro) is modest at 699.

### 10c. Why tool output is second-order (but not zero)
Tool results land in the producing analyst's messages channel, are read while it writes its report, then wiped by the Msg Clear node before the next stage. So raw tool text is paid roughly once (slightly more, because steps within an analyst's tool loop re-read earlier results as the loop accumulates) and never propagates downstream. Contrast with the finished reports, which are re-injected on every debate turn (section 8). So a tool output of size X is paid ~once; a report of size R is paid R times the number of debate turns. Tool size is second-order vs report re-injection - but ~6k per analyst across four analysts is not negligible.

### 10d. Reddit: invalid measurement AND a real bug
fetch_reddit_posts("NVDA", "2025-06-02") returned only 114 chars / 53 tokens, but that number is NOT valid:
- The failed subreddit names in the errors were r/2, r/0, r/5, r/-, r/6 - i.e. the characters of the date string "2025-06-02". The function treated the date as the subreddit LIST and iterated it character by character. The second positional argument is the subreddit list, not the date - the call signature was wrong.
- Separately, Reddit fails live: HTTP 403 (Blocked) on JSON, then 404/429 on the RSS fallback. This matches the reliability problem noted in the trial-run notes (Reddit 403s).
- Conclusion: log Reddit as a known-failing source, not a token figure. The 53 tokens is an empty error fallback.

### 10e. Still open (gaps in #4)
- get_news_yfinance (ticker news): not yet measured. Needs (ticker, start_date, end_date). Prime suspect for the largest raw input because news_article_limit = 20 allows up to 20 full articles. IMPORTANT: the NVDA 2025-06-02 window had no news ("no news found"), so measuring that date gives the FLOOR, not the ceiling. A date with real headline flow could be far larger - measure a well-covered date to see the realistic maximum.
- get_YFin_data_online (OHLCV market data): not yet measured. Needs (ticker, start_date, end_date) and must be imported from the same module as get_fundamentals.
- These gaps do not change the report's main recommendations (tool output is second-order to the debate re-injection), but are needed for a complete per-tool ranking.

### 10f. Estimation accuracy note
Earlier eyeball estimate for get_fundamentals (~200 tokens) was low; actual was 272. Long integer fields (market cap, revenue) tokenize into more pieces than expected. Lesson: measure, do not estimate, for anything with long numeric strings.

## 11. Closing the #4 gaps: ticker news and OHLCV measured

Two remaining tools from section 10e now measured (NVDA, tiktoken o200k_base):

| Tool | Chars | Tokens |
|---|---|---|
| get_YFin_data_online (OHLCV) | 3709 | 2274 |
| get_news_yfinance (ticker news) | 56 | 22 |

### 11a. OHLCV is a heavy tool (2,274)
get_YFin_data_online for the ~3-month window (2025-03-01 to 2025-06-02) is 2,274 tokens - second only to the balance sheet among all tools measured. This feeds the market analyst. It scales with the date range, so a longer lookback would be larger. Relevant for the backtest design: the OHLCV pull is one of the bigger per-run raw inputs.

### 11b. Ticker news measured but NOT representative (22 tokens)
get_news_yfinance returned only 56 chars / 22 tokens for the 2025-05-26 to 2025-06-02 window. This is the empty/near-empty case - NVDA had no news in that window ("no news found" in the run). This is the FLOOR, not the ceiling. With news_article_limit = 20, a date with real headline flow could be dramatically larger (up to 20 full articles). The realistic maximum is still unmeasured. Do not treat 22 tokens as the typical ticker-news cost - treat it as "what an empty window costs."

### 11c. Complete measured tool ranking (NVDA, single run)
| Rank | Tool | Tokens | Feeds |
|---|---|---|---|
| 1 | get_balance_sheet | 2503 | Fundamentals analyst |
| 2 | get_YFin_data_online (OHLCV) | 2274 | Market analyst |
| 3 | get_cashflow | 1822 | Fundamentals analyst |
| 4 | fetch_stocktwits_messages | 1761 | Sentiment analyst |
| 5 | get_income_statement | 1388 | Fundamentals analyst |
| 6 | get_global_news_yfinance | 699 | News analyst |
| 7 | get_fundamentals | 272 | Fundamentals analyst |
| 8 | get_news_yfinance (ticker news) | 22* | News analyst |
| - | fetch_reddit_posts | failing | Sentiment analyst (403/429, see 10d) |

*empty-window floor; real ceiling unmeasured (up to 20 articles).

### 11d. Per-analyst raw input totals (this NVDA run)
- Fundamentals analyst: 272 + 2503 + 1822 + 1388 = ~5,985 tokens (heaviest analyst by raw tool input)
- Market analyst: ~2,274 (OHLCV) plus indicator tools (not yet measured)
- Sentiment analyst: 1,761 (StockTwits) + Reddit (failing) = ~1,761 usable
- News analyst: 699 (macro) + 22 (ticker, empty) = ~721 this run, but ticker news could balloon on a news-heavy date

Note: these are floor figures. Indicator tools (get_stockstats_indicator, get_stock_stats_indicators_window) for the market analyst remain unmeasured. Ticker news is an empty-window floor.

### 11e. Standing conclusion for the report
Tool output remains second-order to the debate report re-injection (section 8/9), but it is not trivial: the fundamentals analyst alone ingests ~6k tokens of raw statements, and OHLCV adds ~2.3k for the market analyst. Preprocessing/trimming this data before the run (the "preprocess data download" requirement) would cut these one-time costs and is especially valuable for the backtest, where the same NVDA series would otherwise be re-fetched every week across ~78 weeks.

## 12. Per-stage token breakdown (item #1) - ROUNDS=1 baseline, measured

Captured with a token-logging callback over a real NVDA run on 2025-06-02 at the current default (max_debate_rounds=1, max_risk_discuss_rounds=1). This is the last of the five diagnostic items.

### 12a. Headline: the current default already more than halved the cost
- This run: 91,308 input + 14,639 output = ~105,947 total, 16 LLM calls, ratio 6.2:1
- Old bloated run: 222,000 input + 26,300 output = ~248,300 total, 25 LLM calls, ratio 8.5:1
- Current default is ~43% of the old run. This CONFIRMS the section-3 hypothesis: the 222k run used 3 rounds, and just running today's config (1 round) is the single biggest saving, already banked. The old 222k run effectively IS the 3-round data point, so a dedicated 3-round rerun is optional.

### 12b. Full per-node table (ROUNDS=1)
| Node | Calls | Input | Output |
|---|---|---|---|
| Market Analyst | 3 | 26512 | 2573 |
| Neutral Analyst | 1 | 11250 | 655 |
| Bear Researcher | 1 | 10097 | 2055 |
| Conservative Analyst | 1 | 9531 | 871 |
| Aggressive Analyst | 1 | 7435 | 1061 |
| Bull Researcher | 1 | 7206 | 1438 |
| Fundamentals Analyst | 2 | 4737 | 2827 |
| Research Manager | 1 | 4033 | 272 |
| Sentiment Analyst | 1 | 3857 | 1032 |
| Portfolio Manager | 1 | 3543 | 491 |
| News Analyst | 2 | 2358 | 1209 |
| Trader | 1 | 749 | 155 |
| TOTAL | 16 | 91308 | 14639 |

### 12c. Surprise finding: at 1 round, the Market Analyst is the single biggest cost, not the debate
- Market Analyst = 26,512 input = 29% of all input, more than any debate node.
- Cause: its tool loop ran 3 LLM calls. Each call re-reads a long system prompt (the market analyst's indicator list is large) plus the accumulating OHLCV (2,274 tokens, section 11) and indicator results. Big fixed prompt read 3 times, data piling up each pass.
- Reframe: sections 8-11 established that debate re-injection dominates, but that is true mainly at HIGH round counts. At the current default of 1 round, the cost center shifts to the analyst tool loops.

### 12d. Input grouped by stage (ROUNDS=1)
- Four analysts (tool loops): 26512 + 4737 + 3857 + 2358 = 37,464 (41%)
- Risk debate trio: 7435 + 9531 + 11250 = 28,216 (31%)
- Bull/bear pair: 7206 + 10097 = 17,303 (19%)
- Research Mgr + Portfolio Mgr + Trader: 4033 + 3543 + 749 = 8,325 (9%)
Deliberation total (everything except the 4 analysts) = 53,844 (59%), but spread across 8 nodes.

### 12e. Risk debate now visibly repeats the accumulation pattern
The three risk speakers climb in input: Aggressive 7,435 -> Conservative 9,531 -> Neutral 11,250. The ~2k step each time is the history accumulating across speakers (same mechanism as section 8, now seen in the risk debate). The FIRST speaker (Aggressive) already costs 7,435 with no prior risk history to carry, which means risk debators pull in heavy context - very likely the reports or the plans. Strong indirect evidence the risk debate repeats the expensive re-injection pattern. STILL want the three risk debator files to confirm exactly what they read (open since sections 8/9).

### 12f. Confirmations of earlier sections
- Trader 749 input: confirms section 9 (reads only the investment plan). Cheapest node.
- Research Manager (4,033) and Portfolio Manager (3,543): modest, confirms section 9 (debate history + plans, not full reports).
- News Analyst 2,358: consistent with the empty news window (section 11 floor). Would be larger on a news-heavy date.
- Ratio fell 8.5:1 -> 6.2:1: consistent with less debate accumulation at 1 round.

### 12g. Refined lever ranking (post-baseline)
1. Round count 3 -> 1: already done in current config. Biggest single cut (~248k -> ~106k). Free (env var).
2. Market Analyst tool loop (26,512, 29%): trim its system prompt (indicator list), reduce tool-loop iterations, or pre-compute indicators so fewer passes are needed. This is now the top remaining single-node target.
3. Risk debate trio (28,216, 31%): if/when it re-injects full reports, feed digests instead (section 8g lever, now likely applies to risk too). Confirm with the debator files.
4. Bull/bear digests (section 8g): still valid, biggest impact returns if rounds ever go above 1.
5. Preprocess/trim raw data (sections 10-11): second-order per-run, but compounds hugely across the ~78-week backtest.

### 12h. Status: all five diagnostic items now complete
#1 per-stage breakdown (this section), #2 graph wiring (sec 1-7), #3 debate re-injection (sec 8), #4 tool sizes (sec 10-11), #5 downstream agents (sec 9). Remaining small gaps: the three risk debator files (to confirm 12e), market analyst indicator tools (unmeasured), and the ticker-news ceiling on a busy date (section 11b). None block the report.

## 13. Closing the last open questions: risk debators + market analyst

Source files: aggressive_debator.py, conservative_debator.py, neutral_debator.py, market_analyst.py. These resolve the two items left open in sections 8/9/12.

### 13a. CONFIRMED: risk debators re-inject all four full analyst reports
All three risk prompts contain the same block:
- Market Research Report: {market_research_report}
- Social Media Sentiment Report: {sentiment_report}
- Latest World Affairs Report: {news_report}
- Company Fundamentals Report: {fundamentals_report}
plus the trader plan (trader_investment_plan), the running history, and the other two analysts' last responses. So the risk debate repeats the section-8 re-injection pattern. It is worse than bull/bear because THREE speakers re-read the four reports three times per round (vs twice for the two-speaker bull/bear debate).

Token confirmation: first risk speaker (Aggressive, empty history) = 7,435 input, nearly identical to first debate speaker (Bull) = 7,206. The match is the four-report bundle present in both. The bundle is therefore ~6.5k tokens and is the bulk of every debator's input.

### 13b. Same duplication redundancy as section 8d
Each risk prompt passes current_aggressive_response / current_conservative_response / current_neutral_response separately, even though those arguments are already inside history. The most recent arguments are sent twice. Minor, removable. (The ~2k step between consecutive risk speakers in section 12e is largely this duplication: each new speaker adds the prior speaker's argument once in history and once as current_X_response.)

### 13c. Report re-injection count and cost (the single biggest structural cost)
The four-report bundle (~6.5k) is injected into: Bull, Bear, Aggressive, Conservative, Neutral.
- At ROUNDS=1: 5 injections = ~32k tokens (about 36% of the 91k input).
- At ROUNDS=3: 15 injections = ~97k tokens.
Managers and Trader do NOT read the reports (section 9), so the fix is contained to these 5 debator prompts. HIGHEST-IMPACT LEVER: replace the four full reports with compact digests in the 5 debator prompts. Now confirmed to apply to all five.

### 13d. Market analyst: why it is the biggest single node (26,512 over 3 calls)
Two compounding causes, both visible in market_analyst.py:
1. Large system prompt re-sent every call. The system_message is the full catalog of 11 indicators, each with Usage + Tips text (~700-900 tokens). The ChatPromptTemplate rebuilds it on each of the 3 passes, so the catalog is read 3 times.
2. Tool loop accumulates. The analyst calls get_stock_data (OHLCV CSV ~2.3k), then get_indicators, then get_verified_market_snapshot across separate passes. Each new pass re-reads all data pulled by previous passes, so OHLCV + indicator outputs are read 2-3 times.
Result: fat catalog x3 + accumulating data re-reads = 26,512.

Note on tool overlap: get_verified_market_snapshot is described as the "source of truth" for OHLCV/indicator values, but get_stock_data also returns OHLCV. Some redundancy between the three market tools - candidate for consolidation.

### 13e. The fix is already proven in this codebase
The Sentiment Analyst runs in a SINGLE call for 3,857 tokens because it was redesigned to pre-fetch its data and inject it once, with no tool loop (documented bug fix). The Market Analyst still uses the old tool-loop pattern. Converting it to the sentiment-analyst approach (pre-compute OHLCV + indicators, inject once, drop the multi-pass loop) would collapse the 3 calls toward 1 and remove the re-reads. This is the SAME work as the "preprocess the data download" requirement - the token fix and that requirement coincide.

### 13f. Updated concrete lever ranking (all evidence now in)
1. Run at 1 round, not 3: already banked. ~248k -> ~106k. Free (env var). [sections 2, 12]
2. Replace the 4 full reports with digests in the 5 debator prompts: ~6.5k -> target ~1.5k each. Saves ~25k at ROUNDS=1, ~75k at ROUNDS=3. Confirmed to apply to bull, bear, and all 3 risk debators. [sections 8, 13a-13c]
3. Convert the Market Analyst to pre-fetch (single call) and trim the indicator catalog prompt: targets the 26,512 top node; aligns with the preprocess directive. [section 13d-13e]
4. Preprocess/trim raw data generally (OHLCV, statements): second-order per run, compounds across the ~78-week backtest. [sections 10, 11]
5. Remove duplicate current_response passing in all 5 debators: minor cleanup. [sections 8d, 13b]

### 13g. Status: all five diagnostic items complete and all open questions closed
- #1 per-stage breakdown: section 12 (measured)
- #2 graph wiring / round counts: sections 1-7
- #3 debate re-injection: section 8 (bull/bear) + section 13a-13c (risk, now confirmed)
- #4 tool sizes: sections 10-11
- #5 downstream agents: section 9
Remaining minor gaps (none block the report): market analyst indicator-tool sizes (unmeasured), ticker-news ceiling on a busy date (section 11b). The bull_history/bear_history and per-agent risk histories remain written-but-unread in the prompts seen.

## 14. Round-count reasoning: does cutting 3 rounds to 1 hurt accuracy?

This section captures the reasoning behind the round-count recommendation, since it is the first question likely to be raised in review. Important caveat up front: token diagnosis measures COST, not accuracy. The accuracy claim below is an evidence-based expectation, not a proven result. The backtest is the instrument to confirm it.

### 14a. Why 3 rounds existed
The framework's premise is adversarial debate: a bull and bear (and three risk analysts) argue so the final call is more balanced than a single model's. Multiple rounds is the natural expression - round 2 rebuts round 1, round 3 sharpens further. That is where real deliberation happens between people. The stale "3 rounds" code comments suggest 3 used to be the default before it was lowered to 1. Cannot tell from the files whether the 222k run inherited an old default or set it explicitly.

### 14b. Why cutting to 1 likely costs little accuracy FOR THIS SYSTEM
The evidence base (the four analyst reports) is FIXED before the debate starts. The debaters fetch no new data between rounds. So by the end of round 1, both sides have already seen all the evidence and stated their reading of it. Rounds 2 and 3 can only re-interpret the same material - there is nothing new to reason about.

This shows up directly in the NVDA output: the bull's three speeches are largely the same case restated (same fundamentals, same "toll booth for AI", same moat points). The trial-run notes say it explicitly - the wordiness came from "repetition of many of the same points", "wasted on repeated arguments rather than something new". So rounds 2-3 produced repetition, not new analysis. When evidence is static, debate returns diminish fast.

Defensible position: for this architecture, on inputs like NVDA, 1 round likely loses very little because the later rounds were recycling rather than discovering.

### 14c. The honest caveat
On a genuinely balanced, borderline name, a second round MIGHT let the manager weigh an evolving argument - but only if that round adds real content, which the NVDA run did not. This is a minority of cases, not the norm.

### 14d. How to verify (turn the judgment into a number)
The backtest is exactly the instrument: run the same weeks at 1, 2, and 3 rounds and compare outcomes against the price-target and max-drawdown accuracy metrics. Recommendation: default to 1 round for cost, and let the backtest show whether 2 ever earns its keep.

### 14e. General effects of the reduction (beyond cost)
- Speed: fewer calls (~25 -> ~16 per run) means faster wall-clock, a large saving across a 78-week backtest.
- Stability: fewer rounds means fewer places for a probabilistic model to wander, so slightly more consistent runs.
- Structure preserved: cutting to 1 does NOT remove the debate. Full bull case, full bear case, all three risk perspectives, and the adversarial structure all remain. It removes the REPETITION of the debate, not the debate itself.
- Main residual risk: borderline cases where a single exchange is genuinely insufficient. Expected to be a minority; the backtest tells us if it is real or only theoretical.

### 14f. One-line summary for the report
3 rounds was meant to deepen the debate, but because the evidence is fixed it mostly repeated, so cutting to 1 saves heavily while keeping the structure intact - and the backtest is how the accuracy cost gets confirmed rather than assumed.

## 15. Controlled 3-round run: the clean before/after (resolves the apples-to-apples gap)

Ran the same token_count.py script at ROUNDS=3, same name (NVDA) and date (2025-06-02) as the ROUNDS=1 baseline. Only the round setting changed. This is the controlled comparison the earlier 248k-vs-106k claim lacked.

### 15a. Headline (now measured, not inferred)
| Setting | Input | Output | Total | Calls | Ratio |
|---|---|---|---|---|---|
| 3 rounds | 247,668 | 29,299 | 276,967 | 26 | 8.5:1 |
| 1 round | 91,308 | 14,639 | 105,947 | 16 | 6.2:1 |

Cutting 3 rounds -> 1 removes ~62% of total tokens (~63% of input). This is a controlled result (same script both times), so the causal claim "the round setting drove the difference" is now measured, not inferred. Directly addresses the top rigor gap raised in review.

### 15b. Confirms the original bloated run was 3 rounds
- Original trial run: 222k in / 26.3k out, 25 calls, 8.5:1
- This controlled 3-round run: 247,668 in / 29,299 out, 26 calls, 8.5:1
Ratio matches exactly, call count matches (25 vs 26), totals within ~10% (normal run-to-run variance). The section-3 inference is now backed by a direct match.

### 15c. Full ROUNDS=3 per-node table
| Node | Calls | Input | Output |
|---|---|---|---|
| Bear Researcher | 3 | 46964 | 6051 |
| Neutral Analyst | 3 | 41195 | 2210 |
| Bull Researcher | 3 | 37528 | 6802 |
| Conservative Analyst | 3 | 37141 | 2802 |
| Aggressive Analyst | 3 | 32414 | 3350 |
| Market Analyst | 3 | 17306 | 1909 |
| Research Manager | 1 | 13389 | 307 |
| Portfolio Manager | 1 | 9991 | 655 |
| Fundamentals Analyst | 2 | 4737 | 2554 |
| Sentiment Analyst | 1 | 3861 | 1085 |
| News Analyst | 2 | 2358 | 1407 |
| Trader | 1 | 784 | 167 |
| TOTAL | 26 | 247668 | 29299 |

### 15d. Mechanism confirmed
- Five debators = 195,242 input = 79% of all input at 3 rounds (was 50% at 1 round). The debate re-injection is what explodes with rounds.
- Each debator grew FASTER than the 3x round increase (superlinear), confirming the compounding history predicted in sections 3/8:
  Bull 7,206 -> 37,528 (5.2x); Bear 10,097 -> 46,964 (4.7x); Aggressive 7,435 -> 32,414 (4.4x); Conservative 9,531 -> 37,141 (3.9x); Neutral 11,250 -> 41,195 (3.7x).
- Managers grew because they read the longer transcript: Research Manager 4,033 -> 13,389 (3.3x), Portfolio Manager 3,543 -> 9,991 (2.8x). Confirms section 9 (they read history, scale with rounds).
- Analysts stayed flat (do not depend on rounds): Fundamentals 4,737 = 4,737, Sentiment 3,857 ~ 3,861, News 2,358 = 2,358.
- Deliberation share: ~89% of input at 3 rounds (219,406) vs 59% at 1 round.

### 15e. Accuracy nuance for the report: Market Analyst is variable, not fixed
Market Analyst was 26,512 at ROUNDS=1 but 17,306 here. It does not depend on rounds, so that ~9k swing is run-to-run noise in its non-deterministic tool loop. Implication for the report: describe it as a large, UNSTABLE node (~17k-27k across runs) rather than fixing it at 26,500/29%. At 1 round it can be the single biggest node; at 3 rounds the debators dwarf it. The variance itself is a minor finding (tool-loop instability).

### 15f. Updated framing for the report
- 106k (1 round) is the current banked baseline. 277k (3 rounds) is the old regime.
- The round cut is the largest single lever and is measured: ~62% off, same script.
- Remaining levers (digests, market-analyst pre-fetch, data preprocessing) cut further from the 106k baseline.

## 16. Replication: second 1-round run confirms consistency

A second ROUNDS=1 run (same script) was measured to check consistency against section 12.

### 16a. New 1-round run
| Node | Calls | Input | Output |
|---|---|---|---|
| Market Analyst | 4 | 21851 | 2458 |
| Bear Researcher | 1 | 11529 | 2059 |
| Neutral Analyst | 1 | 11486 | 917 |
| Conservative Analyst | 1 | 9909 | 800 |
| Aggressive Analyst | 1 | 7861 | 1037 |
| Bull Researcher | 1 | 7620 | 1947 |
| Portfolio Manager | 1 | 5154 | 591 |
| Fundamentals Analyst | 2 | 4737 | 2886 |
| Research Manager | 1 | 4546 | 312 |
| Sentiment Analyst | 1 | 3861 | 1193 |
| News Analyst | 2 | 2358 | 1443 |
| Trader | 1 | 789 | 167 |
| (untagged) | 1 | 776 | 98 |
| TOTAL | 18 | 92477 | 15908 |

Total: 108,385 tokens, ratio 5.8:1.

### 16b. Comparison against section 12's first 1-round run
| Run | Input | Output | Total | Ratio |
|---|---|---|---|---|
| 1-round, run A (sec 12) | 91,308 | 14,639 | 105,947 | 6.2:1 |
| 1-round, run B (this) | 92,477 | 15,908 | 108,385 | 5.8:1 |

Difference is ~2%, well within normal run-to-run variance (non-deterministic LLM calls, and here Market Analyst made 4 tool-loop calls instead of 3). CONFIRMS the ~106k baseline is stable and reproducible, not a one-off.

### 16c. Updated 3-vs-1 round reduction, using this run
- 3 rounds: 276,967 total (section 15)
- 1 round (run B): 108,385 total
- Reduction: 60.9%, consistent with the 62% found in section 15. The "roughly 60%+ token reduction from cutting rounds" claim is now supported by two independent 1-round measurements against one 3-round measurement.

### 16d. Minor note
One call came back with an untagged node ("?"), 776 in / 98 out - langgraph_node metadata wasn't captured for that call. Small (0.8% of input), does not affect conclusions. Likely a retry or a call outside the main graph nodes.

## 17. Method 2 implemented and measured (report digests to the 5 debators)

The digest patch was applied (debators now receive a digest of each report instead of the full four reports) and a 1-round run was measured.

### 17a. Effect on the 5 debators (the targeted nodes)
| Debator | Input before | Input after |
|---|---|---|
| Bull | 7,206 | 1,588 |
| Bear | 10,097 | 5,417 |
| Aggressive | 7,435 | 1,808 |
| Conservative | 9,531 | 3,692 |
| Neutral | 11,250 | 5,297 |
| TOTAL | 45,519 | 17,802 |

Debators cut by 27,717 input tokens = -61%. Method 2 is now MEASURED, not projected. (Projection in section 8g/report was ~25k; actual ~28k, slightly better.)

### 17b. Whole-run effect
- This run (1 round + digests): 71,588 in / 14,339 out = 85,927 total, ratio 5.0:1.
- vs 1-round baseline ~106k: whole run down ~19% this time. The debator saving (~28k) exceeds the total input drop (~20k) because the Market Analyst happened to run high this run (30,876, top of its 17k-31k range) - unrelated tool-loop noise, the target of method 3.
- vs old 3-round regime (276,967): combined with method 1, ~69% total reduction, now from two measured levers.

### 17c. Cost nuance (important, avoid overstating)
The token drop is large but the dollar saving is modest: ~$0.02 per run on OpenAI. Reason: the debators run on the CHEAP model (gpt-5.4-mini), so the removed tokens are cheap. The expensive gpt-5.5 manager nodes were untouched. Method 2 = big token win, small cost win at 1 round; the cost win grows at higher round counts (reports re-read 15x at 3 rounds vs 5x at 1).

### 17d. Non-targeted nodes behaved as expected
- Managers (read history/plans, not reports): Research Manager 4,350, Portfolio Manager 5,544 - within normal range, unaffected.
- Analysts still produce FULL reports (only debators get digests), so the human-facing document is unchanged. Fundamentals 4,737 (deterministic), Sentiment 4,382, News 2,358.
- Debator OUTPUT tokens barely moved (e.g. Bull 1,907, Bear 1,903), so the debate still runs at full length on the digests. Good structural sign.

### 17e. Open caveat
Tokens confirm the mechanical change works. Output QUALITY on digests is not verified by token counts - a human should read one debate to confirm the digests keep enough detail for grounded arguments. The digest keeps ~1400 chars + any final proposal line; max_chars is tunable.

## 18. Method 3 implemented and measured (market analyst pre-fetch, single call)

The market analyst was converted from the multi-call tool loop to a single pre-fetched call (data fetched in Python, injected once, no bound tools, fixed 7-indicator set). No graph changes were needed - with no tool_calls on the response, should_continue_market routes straight to Msg Clear Market on the first pass. A 1-round run was measured.

### 18a. Effect on the market analyst (the targeted node)
| Metric | Before | After |
|---|---|---|
| Calls | 3 to 4 | 1 |
| Input tokens | 17,306 to 30,876 (variable) | 7,768 |

Drop of ~55% to ~75% depending on which prior run is used as the baseline. The node is now a single deterministic call, which also removes its run-to-run variance (the noise that masked method 2's saving in section 17b).

### 18b. Whole-run trajectory (all measured, 1 round unless noted)
| Configuration | Total tokens |
|---|---|
| 3 rounds, full reports, old market analyst | 276,967 |
| 1 round, full reports | ~106,000 |
| 1 round + digests (method 2) | ~86,000 |
| 1 round + digests + market pre-fetch (method 3) | 63,413 |

This run: 49,486 input + 13,927 output = 63,413 total, 15 calls, ratio 3.6:1. About 77% below the original bloated run, with three levers now measured rather than projected. Ratio fell 8.5:1 -> 3.6:1 across the three changes, consistent with steadily removing re-read input rather than changing output.

### 18c. Expected side effects, not concerns
- Market analyst OUTPUT also dropped (777 vs ~1,900-2,570 before). Expected: the old report padded itself with an "indicator selection rationale" section explaining its choices, which no longer applies now that indicators are fixed.
- Portfolio Manager 6,289 - within its normal range (3,543 to 9,991 seen before), history-length variance, not a new issue.

### 18d. Open quality check (same caveat as method 2, higher stakes)
Tokens confirm the mechanical change works. QUALITY not yet verified: the model no longer chooses its own indicators, so a human should read one market report from this run and confirm it still covers trend / momentum / volatility and ends with a markdown table. This matters more than method 2's check because indicator selection was removed, not just report length.

### 18e. Fixed indicator set used
close_10_ema, close_50_sma, close_200_sma, rsi, macd, macds, atr (matches what prior NVDA reports actually used). Trade-off: no per-ticker indicator choice; a name needing different indicators than NVDA gets a slightly less tailored set. Tunable in market_analyst.py if needed.

## 19. Quality check on method 3 (market analyst pre-fetch) - PASSED with one noted trade-off

Verified by pulling the market report directly from a live run of the patched code (not from the stale ~/.tradingagents/logs file, which predated the patch and was a red herring).

### 19a. Confirms no fabrication
The stale log file had shown a "Bollinger Middle" value (129.06) despite that indicator never being fetched by the pre-fetch code, raising a fabrication concern. The live patched run does NOT include Bollinger Middle anywhere in its output - confirms the model only reports on the 7 indicators actually provided (10 EMA, 50 SMA, 200 SMA, RSI, MACD, MACD Signal, ATR) and does not invent values for data it wasn't given. Fabrication concern resolved: negative result (no fabrication found).

### 19b. Structure preserved
Live output still includes: FINAL TRANSACTION PROPOSAL line, separate Trend / Momentum / Volatility sections, actionable interpretation for existing holders and new entries, a "what would weaken the case" section, and a closing markdown table. Matches the format the report has always used.

### 19c. Trade-off identified: less historical narrative depth
The original (pre-patch) NVDA market report told a historical price-action story with specific dates (e.g. "recovered from early-April weakness... low/mid-90s to mid-130s by late May"). The new pre-fetched version reports current indicator values accurately but does not narrate the multi-month historical move the same way, despite still receiving 90 days of OHLCV in the prompt. Likely cause: the new system message explicitly scopes the report to trend/momentum/volatility, which may have nudged the model away from the longer narrative style used before.
This is a real, minor content difference, not a bug. Given the market report now largely feeds into digested debate prompts (method 2), the loss of narrative color is likely immaterial downstream, but it is a deliberate trade-off worth being aware of, not an accidental regression.

### 19d. Verdict
Method 3 PASSES the quality check: structurally sound, no fabrication, transaction line and table preserved. Recommend keeping the patch. If richer historical narrative is wanted later, the system message can be broadened to explicitly request it without re-adding tool loops.

### 19e. Unrelated finding surfaced during this check
Reddit fetch still fails live (403 then 429), consistent with section 10d. Not related to this patch - affects the sentiment analyst's Reddit source, not the market analyst.

## 20. Quality check on method 2 (report digests) - PASSES DIRECTIONALLY, with a real granularity trade-off

Verified by pulling a live bull/bear debate from the current patched code and comparing it against the original full-report debate shared earlier in this project.

### 20a. What held up
- Full debate structure preserved: rebuttal format, both sides engaging point by point, organized headers.
- Numbers that ARE cited are accurate and grounded in the underlying reports (revenue $44.06B, net income $18.78B, RSI 65.29, MACD 6.15 vs signal, ATR 4.89, moving averages). No fabrication observed.
- Overall investment stance direction unchanged: bull constructive on growth/moat/technicals, bear cautious on valuation/crowding/macro - same balanced framing as before digests.

### 20b. What got thinner, and the mechanism why
Compared to the original full-report debate, granular fundamentals and sentiment detail is missing:
- Old bear cited: profit margin 62.97%, operating margin 65.60%, ROE 114%, current ratio 3.44, forward P/E 16.1, PEG 0.63, gross profit $188B. New bear reaches only revenue and net income.
- Old debate cited the exact StockTwits split (13 bullish vs 3 bearish). New debate only says sentiment "leans bullish," no breakdown.

Root cause: digest_report (agent_utils.py) truncates each report to its first max_chars (1400) characters, whatever content happens to be there. If a report front-loads revenue/net income and puts margins/ratios/valuation later, those later details are cut - not because they are less important, but purely due to their position in the text. Same for sentiment: the exact bullish/bearish counts apparently sit later in the sentiment report, past the cutoff.

### 20c. Key finding: fixed-length truncation is a blunt instrument
Digest quality currently depends on where information sits in each report, not on what is actually decision-relevant. This is a real weakness in the CURRENT digest_report implementation specifically, separate from whether digesting reports is a good idea in general.

### 20d. Verdict
Method 2 passes directionally: no fabrication, same overall stance reached, structure intact. But debators now argue with materially less fundamental/sentiment granularity than before. Whether this is acceptable depends on how much decision-quality depends on debate richness vs. just reaching a reasonable final call - a judgment call, not something token counts alone can answer.

### 20e. Suggested future improvement (not yet implemented)
Instead of truncating by character count, extract specific fields (revenue, key margins, 2-3 headline ratios, sentiment split) directly from each report by pattern/parsing, so the digest keeps decision-relevant numbers regardless of where they appear in the source text. Would preserve more of the original debate quality at similar or better token cost. Not implemented - a candidate for later refinement if the current digest proves too lossy in the backtest.

## 21. Updated task scope (post-meeting)

> SUPERSEDED IN PART - see §25b (output format is the 5-tier signal, not "price instead of buy/sell/hold"), §26 (price prediction and confidence added back), and §27 (current plan). Task 1 is done, so item 1 below (continued token cuts) is now optional, not required. Kept for history.

Revised 2-week plan:
1. Continue token cuts (methods 4, 5, and possibly the field-extraction digest from section 20e).
2. Extract a stock-level PRICE prediction (horizon ~3 months) instead of buy/sell/hold, and backtest.
Medium term: QQQ predictions, then portfolio exposure levels.

Max drawdown decision: compute REALIZED max drawdown from OHLCV (backward-looking, deterministic), NOT forecast it. The "technical analyst predicts drawdown" interpretation was set aside as a much harder, separate task and not what is wanted.

Changes from the original brief: horizon 6 months -> 3 months; drawdown moved from firm deliverable to a realized computation; QQQ / portfolio exposure explicitly medium-term; token cuts continue in parallel rather than being finished.

## 22. Data-layer investigation (for task 2 backtest, method 4, realized drawdown)

Files reviewed: interface.py (vendor router), y_finance.py, utils.py, config.py, stockstats_utils.py.

### 22a. Two separate OHLCV paths
1. get_YFin_data_online (y_finance.py) - used for the raw OHLCV text block the market analyst shows. NO caching: live yfinance call every time. Bounded by explicit start/end, so no look-ahead leak when end = curr_date, but it re-downloads on every run.
2. load_ohlcv (stockstats_utils.py) - used by the indicator path. CACHES to disk: one CSV per symbol in data_cache_dir, downloaded once and reused on later calls.

### 22b. load_ohlcv is already backtest-safe (important)
It filters rows to Date <= curr_date, preventing look-ahead bias, and filter_financials_by_date does the same for statement columns. Look-ahead protection is the single most important backtest-correctness property, and it already exists in the codebase.

### 22c. Efficiency: method 4's caching is largely already done for the indicator path
load_ohlcv downloads one multi-year window per symbol and slices it per curr_date. So running all ~78 weekly predictions in one sitting downloads NVDA once and reuses it, giving each week only its historical slice. Method 4 does not need to build OHLCV caching from scratch for this path.

### 22d. MUST-FIX before backtesting: the history window is too short (RESOLVED in §23a - see note)
STATUS: this was fixed. §23a records the change to years=15 and verified it returns rows from
2011. Runtime evidence agrees: the 2026-07-31 run log shows the download window as
2011-07-31 -> 2026-07-31, exactly 15 years. One loose end - a copy of load_ohlcv read during
the §34 session still showed DateOffset(years=5), which contradicts both. Most likely a stale
copy, but confirm the live value with:
grep -n "DateOffset" tradingagents/dataflows/stockstats_utils.py
and delete whichever of these two lines is wrong. Original text below, kept for history.

load_ohlcv fetches only 5 years back (pd.DateOffset(years=5)), despite a docstring claiming "15 years". From mid-2026 that reaches back only to ~mid-2021, which does NOT cover the wanted training start of 1 Jan 2020. The window must be widened (>= 6-7 years, or set explicitly) or the 2020 to mid-2021 portion of the training data will be silently missing. Concrete fix required before the backtest.

### 22e. Cache filename is keyed to today's date (CONFIRMED HARMFUL - see §34g)
The cache filename embeds start and end derived from today, so a new file is created each calendar day. Fine for a backtest run completed within a single day; just be aware across days.
UPDATE: this stopped being a "just be aware" note. The first 78-week attempt crossed midnight,
the filename changed, and the full 15-year series was re-downloaded mid-run straight into a
rate-limited Yahoo. The rollover and the rate limit compounded. See §34e/§34g.

### 22f. Implications for the two features
- Realized max drawdown: compute directly from the load_ohlcv DataFrame (clean, date-filtered OHLCV), inheriting look-ahead safety for free. Short function on top of existing data.
- Method 4 remaining work: (a) widen the load_ohlcv window to cover 2020 (see 22d), (b) optionally route the market analyst's raw OHLCV text through load_ohlcv so it caches too and gains look-ahead safety, (c) trim the news article limits.

## 23. Task 2 progress and reordered backtest plan (checkpoint before new chat)

### 23a. Built and tested since section 22
- compute_max_drawdown (stockstats_utils.py): realized/backward-looking max drawdown from cached OHLCV. This is the SCORER. Tested on NVDA 2025-06-02: -22.49% over 90 days, -36.88% over 180 days (trough 94.18 matches the verified early-April 2025 low).
- forecast_max_drawdown (dataflows/drawdown_forecast.py): Monte Carlo FORWARD drawdown forecast. Two methods (bootstrap = resample real returns, captures fat tails; gbm = normal-returns baseline) plus a use_drift toggle. Look-ahead safe (uses load_ohlcv). NVDA 2025-06-02: expected ~-23%, p95 worst ~-44%, p99 ~-53%, est annualized vol ~59%. Methods agree within ~1-2 points.
- Data window fix: load_ohlcv changed from DateOffset(years=5) to years=15, so history now reaches ~2011 and covers the Jan-2020 training start. Verified: load_ohlcv('NVDA','2024-06-01') returns earliest 2011-07-18, latest 2024-05-31 (look-ahead still holds), 3240 rows.
- Wiring: forecast_max_drawdown now attaches to propagate() output as final_state["drawdown_forecast"], computed after the agents finish (option 1: deterministic, no LLM, no tokens). Verified: a full NVDA run returns DECISION: Overweight AND the drawdown dict together.

### 23b. Output format decision (SUPERSEDED - see §25b and §26a)
This originally read: "buy/sell/hold + forecast max drawdown, price target dropped." That is no longer current. Corrected decision: the output is the pipeline's own 5-tier PM signal (Buy/Overweight/Hold/Underweight/Sell) + forecast max drawdown, and the price prediction is being ADDED BACK via Monte Carlo (§26a). "buy/sell/hold" was internal shorthand, not an external requirement. Full reasoning in §25.

### 23c. Reordered step-3 backtest plan (cost-driven)
Rationale: a full 78-week OpenAI backtest costs ~$15 and hours. Better to compare OpenAI vs DeepSeek on a SHORT window first, then run the one expensive full backtest only on the winning (cheaper if acceptable) model. This also folds in the added OpenAI-vs-DeepSeek accuracy task early.

- 3a: build the generate loop, parameterized by model + date range (separate generate phase from scoring phase - generating is slow/expensive, scoring is fast/free; don't re-run pipelines to re-score).
- 3b: run ~10 weeks on OpenAI, save results.
- 3c: run the same 10 weeks on DeepSeek, save results.
- 3d: compare - agreement on the 5-tier PM signal across the two models. Drawdown is NOT a comparison axis: the forecast is deterministic and seeded, so it is identical for both models on the same date (see §24d). Decide if DeepSeek is acceptable. Set the "acceptable" bar BEFORE running, not after.
- 3e: pick the winner, run the full ~78-week backtest once on that model.
- 3f: score the full run against realized drawdown via compute_max_drawdown.

Caveat to state explicitly in reporting: 10 weeks (and even 78) is a small sample - the short run shows whether the two models AGREE (is the cheaper one trustworthy), not whether either is statistically ACCURATE vs reality. Don't oversell.

### 23d. Scoring metric (to finalize in 3f)
Forecast gives a distribution (expected/p95/p99); realized gives one number. Candidate metric: does realized breach the p95 "worst case"? A well-calibrated p95 should be breached ~5% of weeks. Also compare realized vs expected directly. Define precisely in 3f.

### 23e. Immediate next action (stopping point) - DONE
Both checks were run. Results save as one JSON per run at ~/.tradingagents/logs/NVDA/TradingAgentsStrategy_logs/full_states_log_<date>.json; main.py is the runner; no pre-existing backtest harness existed. 3a was then built and 3b run. See §24 onward for what happened next.

### 23f. Persistent environment/workflow notes
- Always confirm the shell prompt shows (tradingagents); use `python`, not `python3` (python3 hits system Python without the packages).
- Edits are made directly in a local editor (TextEdit/VS Code), not pasted via nano (indentation issues) and not applied as wholesale downloaded files.
- Reddit (403/429) and StockTwits (403) fetch failures are pre-existing, fail open, affect only the sentiment analyst - not bugs from recent work (see 10d).
- Code + report are in the GitHub repo (github.com/LizaDmit/TradingAgents); this log is the durable project record.

## 24. Backtest harness built + first OpenAI run (this session)

### 24a. Run mechanics confirmed (from trading_graph.py)
- propagate(company, date) wraps _run_graph and returns a tuple: (final_state, signal), where signal = process_signal(final_state["final_trade_decision"]) - i.e. the PM's final rating extracted from its prose into a clean label.
- KEY ORDER BUG in the built-in log: inside _run_graph, self._log_state(...) writes full_states_log_<date>.json FIRST, and final_state["drawdown_forecast"] = forecast_max_drawdown(...) is attached several lines LATER, just before return. So the auto-written log can NEVER contain the drawdown forecast (confirmed empirically: the saved 2025-06-02 JSON keys have no drawdown_forecast). Consequence: the backtest must save its OWN record from what propagate() returns in memory, and ignore full_states_log.
- main.py (repo root) is the interactive CLI. It builds state and streams the graph MANUALLY (comment at ~line 1118: "the CLI builds state directly rather than going through propagate()"), so it bypasses propagate entirely and is NOT the base for the harness. It also has questionary menus / rich live display / typer save prompts - unusable for an unattended loop.
- Minor naming note for the scorer later: the on-disk state key is trader_investment_decision (the CLI uses trader_investment_plan internally). Whatever parses saved files must use the on-disk name.

### 24b. Model switching is clean (config-driven)
TradingAgentsGraph(config=...) takes a config dict. Model is fully set by config keys: llm_provider, deep_think_llm, quick_think_llm, backend_url (fed to create_llm_client). So switching OpenAI vs DeepSeek is a per-model config dict passed to the constructor - no env-var juggling, no code edits between runs.

### 24c. Memory OFF for the backtest (decision + mechanism)
Decision: run the backtest with the memory component OFF. Why: (1) fair model comparison - if memory is on, the second model runs with the first model's reflections in the PM's context, so you'd be measuring "DeepSeek reading OpenAI's notes," not DeepSeek; (2) reproducibility and independent weeks - with memory on, weeks are path-dependent and re-running won't reproduce.
Mechanism: there is NO on/off flag. Memory persists to a single markdown file at config["memory_log_path"] (default under _TRADINGAGENTS_HOME/memory/trading_memory.md). Disable it by pointing memory_log_path at a FRESH, UNIQUE file per run. Empty memory means get_past_context finds nothing (no injection) and _resolve_pending_entries has nothing to reflect on (which also skips an LLM call - small token saving). Unique-per-run also isolates OpenAI from DeepSeek automatically.
Note for later: a "desk that learns week to week" (memory ON) is a separate, more realistic configuration - not this backtest.

### 24d. Forecast is deterministic -> reshapes 3d
forecast_max_drawdown is seeded (seed: int | None = 42 default at line 35 of drawdown_forecast.py; rng = np.random.default_rng(seed) at line 59) and the pipeline calls it with only (ticker, date), no model, after the agents finish. So the drawdown forecast is IDENTICAL for OpenAI and DeepSeek on the same date, by construction. Therefore 3d's "drawdown within a few %" test is trivially satisfied and tells you nothing - 3d reduces to SIGNAL agreement only.
Minor: the same seed 42 is reused for every week (each week still resamples different history, so forecasts differ). Fine for reproducibility; consider per-week seeds later if you want the cleanest aggregate calibration stats in 3f.

### 24e. 3a built: backtest_generate.py (headless generate loop)
Written and saved at repo root. Properties: headless (no prompts/display), memory OFF (unique mem file per run), model-parameterized (openai_config / deepseek_config built from DEFAULT_CONFIG), resumable (skips dates whose output file already exists; a crashed run just reruns to fill gaps), generate-only (scoring is a separate later pass). Saves one JSON per model+date under backtest_results/<model_tag>/NVDA_<date>.json. Saved fields: signal, trader_decision, drawdown_forecast (full dict), final_trade_decision (PM text, for audit).

### 24f. 3b DONE: OpenAI 10-week run
Ran 10 weekly dates on OpenAI. NOTE the window is 2025-01-06 -> 2025-03-10, NOT 2025-06-02 (the script kept an old placeholder start date; script error, flagged). This does NOT matter for the model comparison - any 10 weeks work, since 3d only tests whether the two models agree, not accuracy. All 10 weeks returned "Overweight". The 403/429 StockTwits/Reddit fetch errors printed on every week are the known pre-existing fail-open failures (see §23f / §10d), not bugs from this work.
Watch (not a bug): the signal never varied across the 10 weeks (always Overweight). Worth noting for whether the strategy discriminates week to week.

### 24g. Saved record verified
signal ("Overweight") and drawdown_forecast (expected -16.16%, p95 -31.15%, p99 -39.72%, 63-day horizon, 10000 sims, seeded) are clean and correct. BUT trader_decision saved as null - the wrong state key was used in the save script. It is NOT used for 3d (which runs on signal), so the script is being LEFT UNCHANGED so the OpenAI and DeepSeek runs are byte-identical. Fix the key name before 3e (the full run), where the record must be complete.

## 25. Decision-tier architecture clarified + output-format decision

### 25a. Three sequential decisions, not one number flowing 5->3->5
The pipeline makes three separate decisions, each feeding the next (sequential, not independent):
- Research Manager - 5-tier rating (Buy/Overweight/Hold/Underweight/Sell): a graded opinion on attractiveness.
- Trader - 3-tier call (Buy/Hold/Sell): direction of the trade only; sizing deliberately deferred.
- Portfolio Manager - 5-tier rating + sizing + time horizon, AFTER the risk debate: the final, most-informed call.
"signal" is not a 4th decision - it is the PM's final 5-tier decision, extracted from the final_trade_decision prose by process_signal. The two 5-tier stages exist because grading attractiveness (start) and setting final conviction/size (end) are both graded judgments; the 3-tier Trader in the middle is just the yes/no/which-way action.
This whole tier structure is INHERITED base architecture from TauricResearch. None of the commits made in this project (all token-reduction + drawdown/backtest) touched the Trader, managers, or the schema enums. If challenged, prove it with: git log --oneline on the manager/schema/signal-processing files - they do not appear in the project's commits.

### 25b. OUTPUT FORMAT DECISION: keep the 5-tier signal (reverses §23b)
The weekly output is the pipeline's own 5-tier PM signal, NOT collapsed to buy/sell/hold. Reason: the 5-tier carries strictly more information (conviction/sizing); collapsing throws that away for no gain. "buy/sell/hold" in earlier notes was internal shorthand, not an external requirement - confirmed no external 3-tier constraint exists. Record this as a deliberate choice ("kept the PM's post-risk-debate 5-tier rating rather than collapsing to buy/sell/hold"). If 3-tier is ever needed downstream, map 5->3: Buy+Overweight->Buy, Hold->Hold, Underweight+Sell->Sell (trivial to add later).
Overweight/Underweight meaning: benchmark-relative sizing labels - hold more/less than the stock's weight in the index. They are the same direction as Buy/Sell at lower conviction. Caveat: the pipeline uses this vocabulary to express conviction, it does not compute an actual benchmark weight (that's the deferred portfolio-exposure work).

## 26. Additional requirements raised in review: price prediction + confidence

### 26a. Expected price prediction - feasible, cheap, deterministic
Read the terminal prices off the SAME Monte Carlo paths forecast_max_drawdown already simulates -> expected price, median, and a range (e.g. p5/p95) over the 63-day horizon. Zero tokens, seeded, consistent with the drawdown methodology. This revives the "price target" that was dropped from the brief, now via Monte Carlo rather than the LLM. Do NOT ask the agents for a price number (costs tokens, no distribution behind it, harder to defend). One small function + a new saved field.

### 26b. Confidence/probability - reframed as RISK DECOMPOSITION
None of the three options offered (upcoming-event / historical / systematic-vs-unsystematic) was adopted. The requirement was reframed as splitting risk by source and routing each through the analyst suited to it:
- Fundamental analyst leg (company-specific / idiosyncratic risk): assess upcoming company events on their own, then add their potential impact; size impact from past price behavior.
- Market/industry analyst leg (systematic risk): estimate the impact of non-company events on the overall market/industry, then apply to the company via a beta-like factor - explicitly CAPM-style beta. Historical data applied here too.
- Later factors: momentum, liquidity, etc. (deferred).
This is a factor-based / CAPM-toward-multifactor risk model, built incrementally (beta first, more factors later). More grounded than bare self-reported LLM confidence.

### 26c. Feasibility assessment
- Beta leg is cheap and deterministic: regress NVDA returns on the market (SPY). The drawdown Monte Carlo already contains TOTAL historical risk; beta splits it into systematic (beta^2 x market variance) vs idiosyncratic residual. Extends existing work.
- The FULL risk-weighted confidence model is a much larger piece - effectively a Task 3, not a one-line schema field. Two open gaps: (1) the COMBINATION FORMULA that turns the components into a single confidence number is undefined - must be defined before building; (2) risk != confidence-in-direction - the link (more idiosyncratic risk -> lower confidence) must be defined, and the backtest can later test whether the confidence number is calibrated.
- Ensemble agreement (run N times, measure agreement) would give a truer probability but multiplies cost by N - against Task 1's whole point. Not feasible at budget; mention as the rigorous-but-expensive option considered.

## 27. Revised implementation timing + current plan

Ordering rationale: anything that changes the LLM pipeline must NOT land between the two model-comparison runs (it would invalidate 3d). Deterministic, pipeline-independent additions (price, beta) can slot in without a rerun. The full confidence model is too large for the Saturday target and belongs after the backtest.

1. 3c - run DeepSeek on the UNCHANGED backtest_generate.py, same 10-week window, byte-identical to the OpenAI run. Blocked only on the four DeepSeek config values (llm_provider, deep_think_llm, quick_think_llm, backend_url).
2. 3d - compare OpenAI vs DeepSeek on 5-tier SIGNAL agreement (drawdown identical by construction, §24d). Set the acceptance bar before comparing. Decide if DeepSeek is acceptable.
3. Between 3d and 3e - implement the expected price prediction (off the MC paths, §26a) AND fix the trader_decision key name (§24g). Add price to the saved record. (Price can even be backfilled onto the already-saved OpenAI runs, since it's deterministic and pipeline-independent.)
4. 3e - run the full ~78-week backtest ONCE on the winning model, capturing signal + drawdown + price.
5. 3f - score: realized drawdown (compute_max_drawdown) vs the forecast distribution (does realized breach p95 ~5% of weeks?), and realized price vs predicted price.
6. Post-backtest (Task 3) - build the risk-weighted confidence model in the specified order: systematic/beta leg first, fundamental-event leg next, momentum/liquidity later. Blocked on the combination formula being defined.

Immediate next action: SUPERSEDED - 3c and 3d are now DONE. See section 28 for the DeepSeek config, the comparison result, and the revised next action.

## 28. 3c + 3d DONE: DeepSeek run and OpenAI-vs-DeepSeek comparison

### 28a. DeepSeek wiring (all four config values resolved)
DeepSeek is a first-class provider in this codebase, not an OpenAI-compatible fallback. Evidence from grep:
- factory.py:7 lists "openai", "xai", "deepseek" as providers -> llm_provider = "deepseek".
- openai_client.py:158 hard-codes "deepseek": "https://api.deepseek.com"; lines 243-244 swap in a dedicated DeepSeekChatOpenAI client for that provider. backend_url is set explicitly anyway.
- api_key_env.py:23 maps "deepseek" -> DEEPSEEK_API_KEY. Key goes in the repo-root .env alongside OPENAI_API_KEY. Never hard-coded, never pasted into chat/screenshots.
- model_catalog.py:131-140 lists the tiers: deep = deepseek-v4-pro / deepseek-reasoner, quick = deepseek-chat / deepseek-v4-flash.
- capabilities.py:55-115 has explicit profiles for DeepSeek thinking models (supports_tool_choice=False; comments note thinking models accept `tools` but reject `tool_choice`, and 400 if reasoning_content is echoed back). The compatibility layer was already anticipated in the codebase.

Final deepseek_config(): llm_provider="deepseek", quick_think_llm="deepseek-chat", deep_think_llm="deepseek-chat", backend_url="https://api.deepseek.com".

### 28b. FINDING: deepseek-reasoner cannot do the manager nodes' structured output
First test week used deep_think_llm="deepseek-reasoner" (mirroring the OpenAI strong-model-on-managers setup). Result: the run completed but printed
  "Portfolio Manager: structured-output invocation failed ('NoneType' object has no attribute 'rating'); retrying once as free text"
The Pydantic/structured call failed on the PM node and the pipeline fell open to a free-text retry, still recovering "Overweight". Working as designed, but it means the PM's final decision came from the fallback path, plus an extra LLM call per week.
Decision: switched deep_think_llm to "deepseek-chat" (non-thinking, structured-output safe). Rerun was clean - no failure line. Rationale: comparing OpenAI-on-structured-path against DeepSeek-on-freetext-fallback would be a confound; deepseek-chat on both tiers makes DeepSeek run the same structured path OpenAI uses. The reasoner incompatibility is a real reportable finding, not a failure.
Process note: testing ONE week before committing to ten is what surfaced this. Keep that habit for 3e.

### 28c. Model-name deprecation warning (time-sensitive)
Per DeepSeek's docs, the classic names deepseek-chat / deepseek-reasoner are deprecated on 2026/07/24 and map to the non-thinking / thinking modes of deepseek-v4-flash. The 10-week run used the classic names before that date. Any later DeepSeek run may need deepseek-v4-flash / deepseek-v4-pro. Not an issue for the OpenAI backtest.

### 28d. 3d RESULT: DeepSeek fails the agreement test (0/10)
Compared via compare_models.py (repo root; reads saved JSONs, no API calls, free and instant). Window 2025-01-06 to 2025-03-10, 10 weekly dates, both models on identical code and identical window.

  date         openai       deepseek      gap
  2025-01-06   Overweight   Hold          +1
  2025-01-13   Overweight   Hold          +1
  2025-01-20   Overweight   Hold          +1
  2025-01-27   Overweight   Hold          +1
  2025-02-03   Overweight   Underweight   +2
  2025-02-10   Overweight   Hold          +1
  2025-02-17   Overweight   Hold          +1
  2025-02-24   Overweight   Hold          +1
  2025-03-03   Overweight   Hold          +1
  2025-03-10   Overweight   Hold          +1

- exact agreement: 0/10 (0%)
- within one tier: 9/10
- mean tier gap (openai - deepseek): +1.10
- identical drawdown forecast: 10/10 (empirically confirms the determinism claimed in 24d)

Interpretation: the disagreement is SYSTEMATIC, not erratic - DeepSeek sits consistently one tier more bearish than OpenAI on every week, same direction every time. Report it as "DeepSeek is systematically ~1 tier more conservative", not "the models disagree".
Acceptance bar honesty note: the 70% exact-agreement threshold was articulated AFTER the outputs were visible, not before as section 23c required. State that when reporting. It does not change the conclusion (0% fails any reasonable bar), but the process deviation should be disclosed rather than hidden.
Do NOT "correct" DeepSeek by adding a constant tier offset - that is fitting a constant to 10 points and would not survive scrutiny.

### 28e. DECISION: run 3e on OpenAI
Rationale: the acceptance test failed outright, so the cost saving (~$15 for 78 weeks) does not justify accepting an unresolved systematic bias in the headline deliverable.

### 28f. BIGGER FINDING: the weekly signal barely varies (flag in reporting)

> PARTIALLY SUPERSEDED - see section 30. Root cause was an anchoring example in the schema's time_horizon field description. After pinning the horizon, DeepSeek varies across four tiers and OpenAI moved off its constant. The invariance was largely an artefact, though OpenAI is still 9/10 Overweight so the concern is reduced, not eliminated.
Across 10 different dates, with different prices, news and technicals: OpenAI returned Overweight 10/10; DeepSeek returned Hold 9/10 (one Underweight). The signal is carrying almost no week-specific information - the pipeline is producing a stable stance on NVDA rather than a genuinely weekly prediction.
Implications:
- 3d effectively measured the models' BIAS LEVEL, not their ability to discriminate between weeks, because neither discriminated.
- For 3e/3f, a near-constant signal has little to score. The drawdown forecast DOES vary week to week (data-driven), so that dimension still carries information - and this strengthens the case for adding the price prediction (26a), another varying, scoreable output.
- Possible causes to investigate later: 1-round debates (method 1) may have flattened deliberation; digest truncation (method 2) may have thinned the week-specific detail the debators see; or the pipeline may simply be stance-stable by design on a mega-cap. NOT yet investigated - do not assert a cause without evidence.

### 28f-i. Alternative hypothesis TESTED AND REJECTED: "the market was just quiet"
Challenge raised: maybe the signal was flat because Jan-Mar 2025 was genuinely a stable period for NVDA, not because the pipeline is insensitive. Good methodology - checked before claiming the finding. It does not hold, for two independent reasons.

(1) The window contains the largest single-day market cap loss in history. On 27 Jan 2025 NVDA fell ~17% in one day (closing ~$118.5, ~$589B of market cap), its worst day since March 2020, triggered by DeepSeek's R1 release raising doubts about AI capex. NVDA continued declining through Feb into March. The window was the OPPOSITE of quiet.

(2) The pipeline's OWN price-derived measure detected the event. The drawdown forecast is computed from price history with no LLM, so it is a free control variable - if the market were stable, it would be flat too. Pulled from the 10 saved OpenAI records (date / expected_max_drawdown_pct / est_annualized_vol_pct):

  2025-01-06  -16.16  52.3
  2025-01-13  -17.20  52.5
  2025-01-20  -17.23  52.7
  2025-01-27  -19.53  56.1   <- crash day: largest single-week move in the series
  2025-02-03  -20.09  57.0
  2025-02-10  -20.19  56.9
  2025-02-17  -20.01  56.9
  2025-02-24  -20.25  54.8
  2025-03-03  -21.81  56.6
  2025-03-10  -23.58  56.8

The three pre-crash weeks are near-flat (vol 52.3 -> 52.7, drift 0.4). On 27 Jan vol jumps +3.4 and expected drawdown worsens 2.3 points - the largest single-week move in both series - and neither recovers. Expected drawdown ends ~46% worse than it started.

CONCLUSION, in its strongest form: the same pipeline, on the same dates, produced a risk measure that moved ~46% and a decision signal that never moved at all. The market was not quiet; one component noticed and the other did not. This is the defensible version of 28f - cite the numbers, not the impression.

Related detail worth keeping: the ONE week DeepSeek moved off Hold was 2025-02-03 (to Underweight) - the week immediately following the crash, i.e. the first run whose data window certainly includes it. DeepSeek responded to the event; OpenAI did not. Suggestive, not conclusive on n=1, but it is the most informative row in the comparison table.

### 28g. Revised immediate next action (SUPERSEDED - see §29e)
Before 3e (both are cheap, deterministic, and do not touch the LLM pipeline):
1. Fix the trader_decision key name in backtest_generate.py (see 24g) so the full run's records are complete.
2. Add the expected price prediction: read terminal prices off the SAME Monte Carlo paths forecast_max_drawdown already simulates (expected, median, p5/p95 over the 63-day horizon). Zero tokens. Needs a look at drawdown_forecast.py to find where the simulated paths are available.
Then 3e: 78 weeks, OpenAI, once, capturing signal + drawdown + price.

## 29. Review feedback on the model comparison + analysis

### 29a. Feedback received (substance)
Without access to the code, the hypothesis offered is that OpenAI and DeepSeek put different weight on recent price momentum. Three concrete suggestions:
1. Explicitly prompt the "investment horizon" instead of letting the system pick it - use something shorter, e.g. 3 months.
2. When price-target output is added, don't output a bare price level. Use a Sharpe-ratio-style structure: (R - r) / s, where R = expected return, r = risk-free return over the same period, s = volatility.
3. Max drawdown is important right now specifically - flagged: current macro risk from China AI competition and the Iran war worsening as a live reason to take drawdown seriously.

### 29b. Point 1 (momentum weighting) - plausible, testable, not yet verified
Both models see IDENTICAL raw technical data for a given date (indicators are computed once from cached OHLCV, not per-model), so any momentum-weighting difference would come from how each model's market analyst INTERPRETS that data in its report, not from different inputs. Diagnostic (free, already-saved data, no reruns): compare the market_report field between an OpenAI and a DeepSeek file for the same date, check for differences in how each discusses recent price action vs longer trend.

### 29c. Point 2 (explicit 3-month horizon) - ties directly to an open question
Every saved PM output so far (both models, all weeks) says "3-6 months" - constant across every date. It is currently UNKNOWN whether this is a fixed schema default or the model freely converging on the same text every time - nobody has checked. Must resolve before implementing the requirement:
  grep -n "time_horizon\|Time Horizon\|horizon" tradingagents/schemas.py tradingagents/agents/managers/portfolio_manager.py
Useful alignment point: the Monte Carlo drawdown forecast already runs on a 63-trading-day (~3-month) horizon. If the PM's stated horizon is currently free-text, pinning it to 3 months would make the LLM's stated horizon and the deterministic forecast's horizon actually match (currently only coincidentally close).

### 29d. Point 3 (Sharpe-like ratio) - SUPERSEDES §26a's simple price-prediction plan
(R - r) / s, feasibility per component:
- R (expected return): derivable from the SAME Monte Carlo paths already used for the drawdown forecast - (E[terminal price] - current price) / current price. No new simulation.
- s (volatility): est_annualized_vol_pct already exists in every saved record - BUT it is annualized while R would be a 3-month return. Mixing periods without converting is the most common implementation mistake here. Must de-annualize s (s_period ~= s_annual * sqrt(63/252)) or annualize R instead - pick one consistently.
- r (risk-free rate): the one genuinely NEW dependency. Not in the codebase. Natural source: 13-week T-bill yield via yfinance ticker ^IRX (same library already used for company lookups elsewhere in agent_utils.py). Also quoted annualized - needs the same period conversion as s.
Net: 2 of 3 inputs come free from work already built (24d, 26a groundwork); the 3rd is one small new fetch; the real engineering risk is period-consistency across R, r, and s, not the math itself.

### 29e. Point 4 (macro context) - checked, confirmed accurate, and unusually on-point
Verified via web search rather than accepted on faith, since it's a live geopolitical claim:
- Iran war: real and actively escalating as of the date of this conversation (a fragile June 2026 ceasefire fractured; US-Iran exchanging strikes around the Strait of Hormuz; oil prices rising on shipping-disruption fears).
- China AI competition: also live right now, and structurally the SAME pattern as the NVDA case study in 28f-i - a Chinese lab (this time "Moonshot AI", not DeepSeek) released a model narrowing the gap with US frontier labs, triggering a semiconductor selloff (chip ETF down >4% same day, NVDA down >2%) within days of this entry.
Conclusion: this concern is not abstract - current conditions plausibly resemble a live rerun of the 27 Jan 2025 case study already documented in this log. Strengthens the case for keeping drawdown as a first-class output, and is relevant because the eventual full backtest (predictions through mid-2026) will very plausibly run into this exact period.

### 29f. Revised immediate next action (supersedes 28g)
1. Diagnostic: compare OpenAI vs DeepSeek market_report text for one shared date, check for momentum-language differences (29b).
2. Verify how the PM's time horizon field is currently produced (schema default vs free text) before deciding how to implement the "explicit 3-month horizon" requirement (29c).
3. Fix the trader_decision key name in backtest_generate.py (24g).
4. Build the Sharpe-like ratio (R-r)/s as the price/return output, replacing the simpler price-prediction plan - needs drawdown_forecast.py to locate the simulated paths, plus one new risk-free-rate fetch (29d).
Then 3e: 78 weeks, OpenAI, once, capturing signal + drawdown + the new (R-r)/s ratio.


## 30. Horizon pinned - ROOT CAUSE FOUND, earlier conclusions substantially revised

### 30a. CORRECTION to a claim made in this project's own analysis
Earlier working claim: "OpenAI converged on 3-6 months every week." WRONG - it was generalised from a handful of files. Full grep of the free-horizon runs shows OpenAI split:
- 3-6 months: 01-06, 01-13, 01-20, 02-03, 02-10, 03-10 (6 weeks)
- 6-12 months: 01-27, 02-17, 02-24, 03-03 (4 weeks)
DeepSeek varied far more, and mostly used catalyst-based horizons ("Next catalyst (earnings, Fed meeting...)", "Until next earnings", "Through February earnings (~8 weeks)", "Next earnings report"), with some 3-6 months.

### 30b. The "horizon confound" hypothesis was RAISED then REFUTED (before the fix)
Hypothesis: the models disagreed because they were answering over different horizons (DeepSeek weeks, OpenAI months), so the 1-tier gap was an artefact, not a quality difference.
Refuted by the free-horizon data itself - on the three dates where BOTH models stated 3-6 months, the gap persisted with no counterexample:
  01-13  OpenAI 3-6mo Overweight | DeepSeek 3-6mo Hold        (+1)
  02-03  OpenAI 3-6mo Overweight | DeepSeek 3-6mo Underweight (+2)
  03-10  OpenAI 3-6mo Overweight | DeepSeek 3-6mo Hold        (+1)
Also, within OpenAI the rating did not move when its own horizon changed (Overweight at both 3-6 and 6-12 months). So the horizon was NOT the cause of the disagreement. Do not report it as such.

### 30c. ROOT CAUSE of the signal invariance: an anchoring example in the schema
tradingagents/agents/schemas.py (~line 203), PortfolioDecision:
  time_horizon: Optional[str] = Field(default=None,
      description="Optional recommended holding period, e.g. '3-6 months'.")
The module docstring states explicitly that "Schema field descriptions become the model's output instructions". So the example '3-6 months' was being fed to the model as guidance - the models were being ANCHORED, not converging.
Fix applied (one edit, to the imported copy at /Users/.../TradingAgents/tradingagents/agents/schemas.py, NOT the stale build/lib copy):
  description=("The evaluation horizon for this rating. Always exactly '3 months'. "
      "Assess the position over a 3-month forward window and do not substitute a "
      "different horizon or a catalyst-based one such as 'next earnings'.")
Verified in output: all 10 OpenAI weeks now print "3 months". Pin confirmed working, not assumed.
Side benefit: the PM's stated horizon now matches the Monte Carlo drawdown forecast's 63-trading-day (~3 month) horizon. Previously only coincidentally close.

### 30d. RESULT after pinning: both models now vary. Invariance was largely an artefact.
Rerun of the same 10 weeks, both models, on the pinned schema (old runs preserved as backtest_results/openai_freehorizon/ and deepseek_freehorizon/ - keep for the before/after):

  date        openai        deepseek      gap
  2025-01-06  Overweight    Hold          +1
  2025-01-13  Overweight    Hold          +1
  2025-01-20  Overweight    Hold          +1
  2025-01-27  Overweight    Underweight   +2
  2025-02-03  Overweight    Hold          +1
  2025-02-10  Overweight    Hold          +1
  2025-02-17  Overweight    Underweight   +2
  2025-02-24  Overweight    Hold          +1
  2025-03-03  Overweight    Buy           -1
  2025-03-10  Underweight   Overweight    -2

  exact agreement: 0/10 (0%)      [was 0/10]
  within one tier: 7/10           [was 9/10]
  mean tier gap:   +0.70          [was +1.10]
  identical drawdown forecast: 10/10 (determinism re-confirmed)

Changes vs the free-horizon runs:
- DeepSeek now uses FOUR tiers (Buy / Overweight / Hold / Underweight) where it previously used Hold 9/10.
- OpenAI moved off its constant for the first time ever (Underweight on 03-10, a two-tier move) - on the week of deepest technical damage (~$106.82, below all three MAs). Shorter horizon = less time for the fundamental thesis to play out = current breakdown weighs more.
- OpenAI is still 9/10 Overweight, so ITS invariance is reduced but NOT eliminated. Section 28f is qualified, not overturned.

### 30e. The "systematic bias" finding no longer holds
Previously: "DeepSeek is systematically ~1 tier more conservative, same direction every week." After pinning, the direction REVERSES on 03-03 and 03-10 (DeepSeek more bullish). So it is no longer a consistent offset. Must be corrected in reporting - it was stated in the first summary.

### 30f. They now disagree hardest at the extremes
03-10: NVDA ~$106.82, deepest point of the drawdown. OpenAI = Underweight, DeepSeek = Overweight. Opposite directions on identical data. Harder to dismiss than a constant offset.
Context (n=1, NOT a conclusion): NVDA recovered from ~$107 in mid-March to ~$135 by early June 2025 (per the NVDA report already in the project files). On that single week DeepSeek's bottom-buying call looks right and OpenAI's looks wrong. This undercuts any assumption that OpenAI is obviously the default choice - which is exactly what the full backtest exists to settle. Do NOT present this as evidence DeepSeek is better.

### 30g. CAVEAT not yet ruled out
The schema edit changed TWO things at once: (1) removed the anchoring example, and (2) added an explicit directive. It is therefore NOT established whether the variation returned because of the 3-month SEMANTICS specifically, or simply because perturbing the description text shook the models out of a rut. Separating those would need a third run with a different-but-neutral description. Worth stating rather than over-claiming causality.

### 30h. Two other findings from the schema read
- price_target ALREADY EXISTS in PortfolioDecision (Optional[float]). That is why DeepSeek emitted "Price Target: 180.0" (01-06) and "95.0" (02-03) while OpenAI emitted none - optional field, inconsistently filled, LLM-guessed with no distribution behind it. When building the (R-r)/s metric, EXTEND or bypass this field rather than adding a duplicate; the Monte Carlo route remains the defensible one.
- Memory contamination (free-horizon DeepSeek 01-06 only): its write-up cited "the [2025-01-06] Overweight call generated -9.6% alpha", but DeepSeek never produced Overweight - that was the discarded deepseek-reasoner run. Cause: the .mem file is keyed by date, so deleting the JSON and rerunning left the old .mem in place to be read. Week-to-week isolation held; only that one record was affected. Now MOOT for current results because the folder rename created fresh directories. STILL FIX in backtest_generate.py before the 78-week run: the resume/skip logic must clear the .mem file alongside the JSON.

### 30i. Revised next actions
1. DONE - .mem clearing fixed in backtest_generate.py (30h). See §31d.
2. CLOSED BY DECISION, not by fix - trader_decision (24g) saves as null and is unused; the
   5-tier PM signal is the deliverable. No key rename needed.
3. Corrections required in reporting: the systematic-bias claim (30e) and the invariance framing (30d).
4. Open question, now sharper: with both models varying and disagreeing at the extremes, model choice is genuinely unresolved - the earlier "just use OpenAI" recommendation was based on the pre-fix data.
5. Then build (R-r)/s (29d, 30h) and run 3e.

## 31. Return-over-risk (R-r)/s implemented + integration-tested

### 31a. Code added to dataflows/drawdown_forecast.py
- _irx_history(): fetches 13-week T-bill yield (^IRX) via yfinance, @lru_cache so it downloads once per process.
- risk_free_annual(curr_date): returns (rate, ok) as of curr_date, filtered to <= curr_date - LOOK-AHEAD SAFE, mirrors load_ohlcv. ok=False keeps a failed fetch visible (0.0 placeholder, not a silent fake 0% rate).
- Inside forecast_max_drawdown, after dd_pct: return distribution read off the SAME simulated price_paths (no second Monte Carlo). terminal = price_paths[:,-1]; rets = terminal-1; R_mean, R_med, s_ret; r_horizon = rf_annual*(horizon/252); ratio=(R_mean-r_horizon)/s_ret.
- New output fields: spot_price, expected_return_pct, median_return_pct, return_vol_pct, risk_free_pct_horizon, risk_free_available, return_over_risk, expected_price, price_p5, price_p95, prob_loss.

### 31b. Integration test PASSED (NVDA 2025-03-17, one real pipeline run)
Math verified by hand: (13.08 - 1.047)/32.23 = 0.373 OK; 119.36*1.1308 = 134.98 OK; rf 1.047%/63d ~= 4.19% annualized, correct for a Mar-2025 13-week T-bill. risk_free_available=true. All fields present in the saved JSON.
Bonus: the 3-month horizon pin propagated into the PM's REASONING, not just the label ("over the next 3 months", "in a 3-month window") - stronger confirmation than the label alone that the schema edit works.

### 31c. Interpretation caveats to keep in the report (not bugs)
- use_drift=True makes expected_return = trailing-year drift extrapolated forward (~+13% per 3mo ~= +52%/yr for NVDA in early 2025). That is a momentum bet, not a neutral forecast, and it is the most challengeable number. It also SOFTENS the drawdown (up-trend draws down less), so drift cuts in OPPOSITE directions for the two outputs.
- mean vs median return diverge (13.08 vs 9.28): lognormal right tail. Arithmetic mean is standard for Sharpe (code correct), but at ~56% vol the gap is large; median is the conservative alternative.
- price_p5..p95 = $80..$205 from spot $119 (-33% to +72%). Honest but too wide for a point price target - argues FOR the ratio framing over a bare price.
- return_over_risk is DETERMINISTIC (post-agent, price-only) -> IDENTICAL across OpenAI and DeepSeek. It is a better deliverable but CANNOT break a model-choice tie; only the signal can.
- One-off PM text garble seen once ("NVDA is above the 10 EMA (119.36 vs. price 117.10)" - 117.10 is below 119.36). Not a pattern yet; watch for recurrence.

### 31d. backtest_generate.py: memory isolation fix
Added mem_path.unlink(missing_ok=True) right after mem_path.parent.mkdir(...), so a rerun cannot inherit a stale .mem file (the bug that contaminated free-horizon DeepSeek 2025-01-06). Fixes the class of problem for the 78-week run.

## 32. Decisions taken independently

### 32a. use_drift = False for the primary run
The deliverable is fundamentally a RISK forecast (max drawdown + return-over-risk). Drift-off gives the honest risk picture with no bet that NVDA's past-year climb continues, and "no directional bias assumed" is trivially defensible vs defending a ~52%/yr assumption. Optionally save both drift settings per week (cheap, deterministic) and report drift-off as primary.
NOTE: use_drift is not yet exposed in backtest_generate.py - the pipeline calls forecast_max_drawdown with its default. Must thread the flag through (or change the call site) before the run.

### 32b. Full 78-week backtest on DeepSeek (Option B, cost-driven)
Budget is tight. Cost: optimized OpenAI ~= $0.19/week -> ~$15 for 78 weeks once; DeepSeek is a small fraction. The SCORED deliverable (drawdown + (R-r)/s) is deterministic and IDENTICAL across models, so for 3f's core question - are the risk forecasts calibrated vs realized - the model is irrelevant. Only the buy/sell/hold signal differs, and the existing 10-week OpenAI set already covers the signal comparison.
So: run all 78 weeks on DeepSeek (near-free) for the full drawdown/ratio series and score it; keep the paid 10-week OpenAI set for signal. Decide the $15 OpenAI spend AFTER, only if the signal specifically turns out to matter. This is a cost choice, logged as such, not a claim DeepSeek is better - the model question stays formally unresolved.

### 32c. Smoke test before the big run
Run 5 weeks first, score those 5, confirm 3f works, THEN run 78. Debugging the scorer on 5 records is free; discovering it is broken after 78 is not.

### 32d. Scoring-window constraint (important for 3e/3f)
Each date needs ~63 trading days (~90 calendar days) of REALIZED data after it to score. Today is 2026-07-25, so the last scoreable date is ~late April 2026. A 78-week run from 2025-01-06 ends ~early July 2026, whose final ~10-13 weeks cannot be scored yet. Pick the window deliberately: either start earlier, accept the tail is unscoreable for now, or shorten. Do not silently score dates that lack full forward data.
RESOLVED in §34d: window stays 2025-01-06 to 2026-06-29 (78 dates), because 78 IS the brief -
"2025 through mid-2026" converted to weeks. Shifting the start to make everything scoreable was
considered and rejected as a departure from the spec. The tail is accepted as unscoreable for
now and handled by the scorer's automatic skip, not by trimming. As of 2026-07-31: 69 of 78
scoreable, last scoreable grid date 2026-04-27, final date scoreable ~late September 2026.

## 33. Handoff state (for the next chat)
- DONE: schema horizon pinned to 3 months; (R-r)/s built + integration-tested; memory isolation fix; compare_models.py; 10-week comparison on both models (free-horizon AND pinned).
- PENDING COMMIT: MOOT. The test files were already gone and the code was already committed
  and pushed as c2273f1.
- NEXT: SUPERSEDED by §34j. All of use_drift threading, the 10-week regeneration, and the
  scorer are done; the 5-week smoke test was replaced by scoring the existing 10 (same test,
  free). Current blocker is the interrupted 78-week run - 38/78 complete, rerun pending.
- STILL UNMAPPED (for the separate codebase-study chat, NOT the backtest): graph-assembly code (add_node/add_edge, debate loop counts, stop conditions) and the data-vendor layer (route_to_vendor indirection).

## 34. use_drift threaded, scorer built, first full-run attempt (partial)

### 34a. use_drift=False now actually in effect (was only a logged decision)
§32a flagged that use_drift was never passed - trading_graph.py line 414 called
forecast_max_drawdown(company_name, trade_date) with no flag, so every run silently
took the function default of True. Threaded via config, matching the memory_log_path
pattern:
- trading_graph.py: use_drift=self.config.get("use_drift", False). Default False, so a
  caller that forgets the config gets the risk-neutral behaviour, not the momentum bet.
- backtest_generate.py: run_cfg["use_drift"] = False alongside memory_log_path.
VERIFIED not assumed: forecast_max_drawdown echoes use_drift into its output dict, so the
saved JSON was read back and confirms false. Visible in results: expected_price now sits
~4% above spot on every record (lognormal convexity of a zero-mean sim), not the large
trend extrapolation drift-on produced.

### 34b. 3f scorer built: backtest_score.py
Reads saved JSONs, compares forecast vs realized, prints per-record table plus three
calibration summaries. Key mechanic: compute_max_drawdown looks BACKWARD from curr_date,
so it is called with curr_date = prediction_date + 90 days, which lands the window on the
forward period. lookback_days=91 not 90, because the filter is strictly Date > (end - N),
which would otherwise exclude the prediction date itself - the forecast treats spot on that
day as the starting peak, so the realized calc must include it.
Records whose horizon has not closed are skipped automatically ("horizon not complete"),
which is the §32d guard implemented rather than remembered.

### 34c. MAJOR METHODOLOGICAL FINDING: weekly windows are not independent
Scoring windows are 90 days long but step forward only 7 days, so consecutive windows
overlap by 83 days and re-measure the same event. Visible directly in the first scored
output: dd_realized repeated -35.93 twice, -32.68 three times, -22.49 twice - all the same
spring-2025 NVDA decline counted repeatedly.
CONSEQUENCE: 78 weekly dates are NOT 78 independent calibration tests. Independence needs
13 weeks of separation, so 78 weeks yields ~6 independent windows.
HANDLING: report headline calibration on a non-overlapping subset (every 13th week);
keep the full weekly series as the descriptive series and for the signal.
Independent subset on this grid: 2025-01-06, 2025-04-07, 2025-07-07, 2025-10-06,
2026-01-05, 2026-04-06.
HONEST FRAMING for the writeup: at 6 independent windows, a 0/6 and a 1/6 breach rate are
both consistent with a correct 5% forecast. The claim available is "no evidence of
miscalibration", NOT "calibration confirmed". Extending the span does not rescue this -
distinguishing 5% from 15% with real power would need dozens of independent windows, i.e.
decades of weekly data on one ticker. State the limitation; do not collect more overlap.

### 34d. Why the window is 2025-01-06 to 2026-06-29 (78 weeks)
Chosen deliberately, not inherited from a spec. 78 weeks = 18 months of weekly dates, which is long
enough to cover several market regimes while ending late enough that most dates can still be
scored. 2025-01-06 plus 77 weeks = 2026-06-29.
Considered and rejected: shifting the start back to 2024-11-04 so every date would be
scoreable today. Rejected because the tail being temporarily unscoreable costs nothing - the
scorer skips those records automatically - whereas moving the start would have been a change
made purely for scoring convenience.
Scoring status as of 2026-07-31: 69 of 78 are scoreable now (last scoreable grid date
2026-04-27, since 90 days back from today is 2026-05-01). The final 9 generate normally
but cannot be scored until their horizons close, the last around late September 2026.
This is handled by the scorer's skip, not by trimming the window.

### 34e. First full-run attempt: 38/78 completed, TWO distinct failure modes
Run was interrupted, not corrupted. 38 records exist and are valid; 40 dates failed and are
recoverable because the if out_path.exists() skip makes reruns resume-only.
1. Connection error. x22 (2025-09-29 to 2026-02-23). This is the OpenAI SDK's message,
   which DeepSeek uses via the same client - so it is the LLM API dropping, NOT Yahoo.
2. No market data for 'NVDA': Yahoo Finance returned no rows x18 (2026-03-02 to 2026-06-29).
   This is the project's own NoMarketDataError. NVDA is not delisted - yfinance's "possibly delisted"
   text is its generic empty-response message. Cause: ~28 consecutive runs with no pause
   tripped Yahoo rate limiting.
NOT POISONED: load_ohlcv explicitly refuses to cache an empty frame, so the block did not
write a bad CSV that would persist.
RESOLUTION of the block: it cleared on its own after a few hours' wait. Nothing was fixed
in code; time did it. Confirmed clear by a direct yf.Ticker('NVDA').history(period='5d')
returning real rows.
PREVENTIVE FIX ADDED, NOT YET VALIDATED: time.sleep(20) at the end of the for-loop body in
backtest_generate.py, deliberately OUTSIDE the try/except so a failed date still pauses
before the next request - otherwise a failure cascade hammers the API exactly when it
should back off. Adds ~20 min across 68 dates. Whether it prevents recurrence is UNTESTED;
the rerun is the test.
Note this contradicts the general "never edit mid-run" rule, deliberately: that rule guards
against pipeline logic changing model outputs mid-series. A sleep alters pacing only, so
records stay comparable.

### 34f. NEW: deepseek-chat fails structured output on the Trader node
Observed in the run log: "Trader: structured-output invocation failed ('NoneType' object
has no attribute 'action'); retrying once as free text". The codebase's
invoke_structured_or_freetext fallback caught it.
This EXTENDS §28b, which found deepseek-reasoner failing structured output on the MANAGER
nodes. This is a different model (deepseek-chat) on a different node (Trader), so the
structured-output weakness is broader than §28b established.
Does not threaten the scored deliverable: max drawdown and (R-r)/s are computed post-agent
from prices only, so no LLM output touches them. Affects the 3-tier trader call, which is
already unused (saves as null, §33). Worth counting after the rerun -
grep -c "Trader: structured-output invocation failed" backtest_run.log - because "DeepSeek's
structured trader output was unreliable" is a real model finding worth reporting if it is most runs
rather than a handful.

### 34g. §22e CONFIRMED in practice, not theoretical
The cache filename embeds today's date. The run crossed midnight, the filename changed from
the 2026-07-30 form to the 2026-07-31 form, and the full 15-year series was re-downloaded
mid-run - landing on an already rate-limited Yahoo. So the cache-date rollover and the rate
limit compounded each other. For long runs, either finish within one calendar day or
pre-seed the new day's cache file by copying the previous day's.

### 34h. Signal observations from the 38 completed records (observation, not conclusion)
Mar-May 2025 varies well (Hold, Buy, Underweight, Overweight, Buy). Then 2025-06-02 through
2025-09-08 is 15 consecutive Holds, before Overweight / Underweight in mid-late September.
Do NOT read this as the §28f invariance problem returning - a long Hold streak during a
genuinely trending, low-drama stretch may be correct behaviour. But it is worth checking
against realized volatility for those weeks once the run completes, since §30d's claim that
invariance was "largely an artefact" was established on a 10-week window only.

### 34i. Corrections to earlier sections
- §33 "PENDING COMMIT: delete test files" - MOOT. NVDA_2025-03-17.json and its .mem were
  already gone, and the code was already committed and pushed as c2273f1.
- §30i item 1 (.mem clearing) - DONE, see §31d.
- §30i item 2 (trader_decision key) - settled by decision rather than fix: the 5-tier signal
  is the deliverable, the field saves as null and is unused.
- §32d arithmetic - written when today was 2026-07-25. Updated in §34d above.
- §22d vs §23a contradict on the load_ohlcv window (5 vs 15 years). Runtime evidence from the
  failure log shows the download range as 2011-07-31 -> 2026-07-31, i.e. 15 years, so §23a
  appears correct and §22d is superseded. VERIFY the live value and delete whichever line
  is wrong - a stale MUST-FIX in the log is worse than no note.
- .DS_Store files were tracked in git and added noise to every git status; now gitignored
  and untracked.

### 34j. Immediate next actions
1. Confirm time.sleep(20) is present in backtest_generate.py, then rerun. It skips the 38
   and retries the 40.
2. On completion check three things: file count = 78; grep -c "^FAIL" backtest_run.log;
   grep -c "Trader: structured-output invocation failed" backtest_run.log.
3. Verify whether the 10-week OpenAI set was also regenerated with the ratio fields, or only
   DeepSeek - needed before any cross-model signal comparison is written up.
4. Run backtest_score.py on the full set. Report headline calibration on the 6 independent
   windows (§34c), full weekly series as descriptive.
5. Three of the six independent windows (2025-10-06, 2026-01-05, 2026-04-06) are currently in
   the failed set, so the calibration subset is unusable until the rerun completes.
## 35. Multi-ticker extension: rationale, selection, and sizing (PLANNED, not yet run)

### 35a. Why more tickers rather than more weeks
§34c established that 90-day scoring windows stepping 7 days overlap by ~83 days, so 78
weekly dates on one ticker yield only ~6 independent observations. Lengthening the window
does not fix this: independence requires 13 weeks of separation, so any 18-month span on any
single ticker gives ~6. Additional tickers are the only route to more independent
observations. This matters for both open results - the drawdown calibration (0/69 p95
breaches is uninformative at n_eff=6) and the signal test (§34: spread -0.0 pp, underpowered
to detect a modest edge even if one exists).

### 35b. The constraint that drives selection: cross-correlation
Pooling tickers only adds independent observations to the extent the tickers move
independently. Approximate effective sample size:
    N_eff ~= (tickers x windows) / (1 + (tickers-1) x avg_correlation)
Worked at 4 tickers x 6 windows = 24 raw observations:
  - NVDA + QQQ + AMD + TSM   (avg corr ~0.75) -> N_eff ~7
  - NVDA + MSFT + AAPL + AMD (avg corr ~0.6)  -> N_eff ~9
  - NVDA + XOM + UNH + JPM   (avg corr ~0.3)  -> N_eff ~13
A correlated basket is close to worthless here. Sector diversity is not cosmetic - it is the
entire mechanism by which the sample grows.

### 35c. Ranking
| Rank | Ticker | Sector | Est. corr vs NVDA | Rationale |
|---|---|---|---|---|
| 1 | XOM | Energy | ~0.2-0.3 | Oil-driven; genuinely different risk source. Vol ~25-30%, so real drawdowns exist to test. Full financial statements. |
| 2 | UNH | Healthcare | ~0.2 | Large idiosyncratic decline inside this window - a real tail event unrelated to the AI cycle. Directly stresses p95. |
| 3 | JPM | Financials | ~0.35-0.45 | Rate/credit driven. Moderate vol, deep coverage, full fundamentals. |
| 4 | KO / PG | Staples | ~0.15-0.25 | Best independence, but ~15% vol leaves almost no drawdowns to score against. |
| - | MSFT / AAPL | Tech | ~0.5-0.65 | Too correlated to add much. |
| - | AMD / TSM / AVGO | Semis | ~0.7+ | Near-redundant with NVDA. |
| - | QQQ / XLE / GDX | ETFs | high / n.a. | REJECTED. NVDA is ~8-9% of QQQ by weight, so it is partly the same asset. ETFs also have no financial statements, so the fundamentals analyst degrades. |

QQQ was the initial candidate and was rejected on both counts above. Recording that here so
the reasoning is not repeated.

### 35d. Selection-bias disclosure on UNH
UNH is chosen partly BECAUSE its decline in this window is already known. That is
forward-looking information applied to ticker selection and must be stated in any writeup.
Defensible framing: a known stress case was deliberately included to test whether the p95
band catches a real tail event. This makes the test harder, not easier - but only if declared.

### 35e. How many: 3 additional, 4 total
- 6 -> ~13 effective observations, roughly doubling statistical power.
- Binding cost is runtime, not money. At ~90s per run plus the 20s sleep (§34e), each ticker
  is ~2.5 hours for 78 weeks. Three additional tickers is a full day; six would be a weekend.
- Diminishing returns: 8 tickers gives ~20 effective observations, still only enough to detect
  a LARGE effect. No feasible ticker count turns this into a precise measurement. State that
  limitation rather than implying more tickers would settle it.

### 35f. Prerequisite before spending the runtime
The correlations in 35c are estimates, not measured. Verify first:
    import yfinance as yf
    d = yf.download(["NVDA","XOM","UNH","JPM","QQQ","AMD"],
                    start="2025-01-01", end="2026-07-01")["Close"]
    print(d.pct_change().corr().round(2))
Proceed if XOM/UNH/JPM are each below ~0.4 vs NVDA. Swap out anything above ~0.6.
Fix the ticker list BEFORE running. Adding tickers to widen the test is sound; adding tickers
until one shows an edge is not. Report every ticker run, not a subset.

MEASURED (2025-01-01 to 2026-07-01 daily returns, actual not estimated):
    NVDA-XOM 0.05   NVDA-UNH 0.02   NVDA-JPM 0.38
XOM and UNH are close to uncorrelated with NVDA - better than the 35c estimate. JPM matches
the estimate. Average pairwise correlation in the 4-ticker basket ~0.14, revising N_eff from
the estimated ~13 (35b) to ~17. Ticker list CONFIRMED, proceeding as planned.

### 35g. Code changes required
- backtest_generate.py: TICKER is a module constant; needs to become a loop parameter.
  CORRECTION (implemented): results stay FLAT in backtest_results/<model_tag>/ - filenames are
  already ticker-prefixed so they coexist without collision. Per-ticker SUBDIRECTORIES would
  have broken the out_path.exists() resume skip and silently regenerated all 78 NVDA runs.
- backtest_score.py: same hardcoded TICKER.
- Signal test needs a real change, not just a loop: pool each ticker's excess return over ITS
  OWN buy-and-hold, not raw returns. Pooling raw returns would measure which ticker rose most,
  not whether the signals carried information.

## 36. Four-ticker scoring: the p95 calibration claim does not survive

### 36a. Headline: what looked calibrated on one ticker fails on four
All four tickers complete at 78 weeks each, 70 scoreable per ticker (280 rows).

| ticker | p95 breach rate | price inside p5-p95 | realized worse than median |
|---|---|---|---|
| NVDA | 0/70 = 0%  | 96% | 20% |
| XOM  | 0/70 = 0%  | 83% | 41% |
| JPM  | 7/70 = 10% | 97% | 37% |
| UNH  | 14/70 = 20% | 74% | 60% |
| POOLED | 21/280 = 8% | 88% | 40% |

The single-ticker result in §34 (0/69 breaches on NVDA) was NOT evidence that the forecast
bounds tail risk. It was evidence that NVDA experienced no idiosyncratic collapse in this
window. One ticker cannot distinguish those two explanations; four can. This is the clearest
justification for the multi-ticker extension and should be reported as such.

### 36b. The UNH failure is severe, not marginal
Worst case 2025-03-17: realized max drawdown -54.23% against a p95 forecast of -27.11%. The
forecast was wrong by a factor of ~2 on the single quantity it exists to bound. 14 breaches
clustered in Jan-May 2025, i.e. one sustained event repeatedly re-measured by overlapping
windows (§34c), not 14 independent failures. UNH price also fell outside the p5-p95 band on
18 of 70 weeks (74% coverage vs 90% expected) - the only ticker where the price interval
also failed.

### 36c. Why it failed - structural, not a bug
forecast_max_drawdown fits sigma from the trailing 252 trading days and, in bootstrap mode,
resamples only from returns observed in that window. UNH was quiet before it collapsed, so
the estimation window contained no move of the magnitude that followed. A bootstrap cannot
generate a shock absent from its sample; a Gaussian fit to a calm window cannot either.
This is the known limitation of historical-window risk estimation, now DEMONSTRATED on this
project's own data rather than cited. That is a stronger result than the original calibration
claim would have been.

### 36d. Reporting rule that follows
Report the per-ticker table, not the pooled figure. Pooling averages one total failure with
three passes into 8%, which reads as "close to 5%" and conceals the finding. The pooled row
is retained above only to make that concealment visible.
Correct claim: the forecast bounded realized drawdown on three of four tickers and failed
substantially on the fourth, in the one case featuring a large idiosyncratic decline.
Superseded: §34's "no evidence of miscalibration" - there is now direct evidence of
miscalibration under idiosyncratic stress.

### 36e. Overlap check on the independent subset
Breaches on the six non-overlapping windows (2025-01-06, 2025-04-07, 2025-07-07, 2025-10-06,
2026-01-05, 2026-04-06) across all four tickers: 2 of 24 = 8%, matching the naive pooled rate.
So overlap did not distort the breach RATE here. It does distort the count - the 14 UNH
breaches are ~1-2 independent events. Rate alone is also insufficient: a breach by a factor of
2 is not equivalent to a marginal one, and the current metric does not capture magnitude.

### 36f. Signal test: still no clear edge, and the tiers are not ordered
Per-ticker buy-and-hold baselines over the 90-day forward horizon:
JPM +5.3%, NVDA +10.9%, UNH +0.2%, XOM +6.9%. Excess returns pooled across tickers (n=280):

| signal | n | mean | median |
|---|---|---|---|
| Buy | 18 | +3.4% | +1.2% |
| Overweight | 61 | +0.3% | -2.6% |
| Hold | 166 | -0.9% | -1.7% |
| Underweight | 35 | +2.2% | -3.3% |

bullish (Buy/Overweight) +1.0% vs cautious (Hold/Under/Sell) -0.4%; spread +1.4 pp.
The spread is positive but the tier ordering is broken: Underweight has the SECOND-HIGHEST
mean and the LOWEST median, i.e. a small number of wrong calls preceded large rallies and
drag the mean up. On medians the ordering is Buy > Hold > Overweight > Underweight - still not
monotonic, since Overweight sits below Hold.
Buy is the only tier where mean and median agree in sign (+3.4% / +1.2%, n=18).
Interpretation: at ~17 effective observations this is consistent with noise. The honest claim
remains "no edge demonstrated", now with a modestly positive point estimate rather than the
exactly-zero spread of the single-ticker test.

### 36g. Signal distribution differs sharply by ticker - RESOLVED: directionally right, but lagging
NVDA skewed bullish (long Overweight/Buy runs), UNH skewed bearish (frequent Underweight),
JPM skewed bullish, XOM was almost entirely Hold. Hold is 166 of 280 rows overall.
The open question was whether those per-ticker skews tracked each ticker's actual direction.
Checked against the real event timeline for both extreme cases:

UNH. The decline was earnings-driven. The Q1 2025 report (mid-April 2025) revealed a severely
deteriorated medical care ratio and full-year adjusted EPS guidance was cut from ~$30 to
~$16; the stock fell from ~$600 to ~$260 by August 2025. A second leg followed a weak 2026
revenue guide reported 2026-01-27 (~-19% in one session).
Signal timing: Hold throughout Jan-Mar 2025, i.e. up to and through the crash. The first
sustained Underweight cluster begins 2025-04-14 - AFTER the report that caused the drop. The
same pattern repeats: the second Underweight cluster appears early Feb 2026, after the
January guidance shock was public.

NVDA. Bottomed around April 2025 in the tariff-driven correction. Signal was Underweight/Hold
through the decline, flipping to Overweight on 2025-04-28, roughly three weeks after the low -
faster than the UNH reaction, plausibly because price and technical inputs update daily
whereas the UNH shock only became visible through a quarterly report.

FINDING: the signal is not random. It moved in the correct direction on both tickers. But it
moved AFTER the information became public, not before. That is the expected behaviour of a
system reading contemporaneous public fundamentals, news and price data: it cannot see a
guidance cut before the guidance is issued.
This refines the §36f result. The precise claim is not "no edge" but "directionally responsive,
consistently lagging" - the pipeline classifies the current state of a stock rather than
forecasting its next 90 days, and a 90-day forward-return test measures the latter.
CAVEAT: this is eyeballed timing against a public event timeline, not a statistical test. It
is a qualitative observation and must be labelled as such. A formal version would regress the
signal on contemporaneous vs lagged returns to separate reaction from anticipation.

### 36h. Run completeness
XOM required two passes (45 -> 53 -> 78); UNH failed 3 dates and JPM 4, all "Connection error"
from the DeepSeek endpoint, all recovered on rerun. time.sleep(20) eliminated the Yahoo rate
limiting entirely - zero "no rows" failures across all three new tickers, versus 18 on the
first NVDA attempt. §34e's preventive fix is now VALIDATED.
Note: print statements gained the ticker prefix mid-XOM-run; a running Python process does not
reload edited source, so early XOM failures logged in the old format without a ticker and were
briefly mistaken for NVDA failures.

## Appendix: Financial Concepts and Methods Used

Separate from the numbered chronological log above. This section collects the financial and
quantitative concepts used anywhere in the project in one place, for reference, rather than
scattered across the dated entries where they first appeared.

This section separates (i) standard theory, (ii) the specific implementation choices made here,
and (iii) what this project's own data actually showed. Only (iii) is a result; (i) and (ii) are
context.

### FM-a. Monte Carlo simulation - what it is and why it was used
Monte Carlo estimates the distribution of an outcome by simulating many possible futures and
reading the statistics off the resulting sample, rather than solving for the answer
analytically. It is used when the quantity of interest has no closed form.

Maximum drawdown is exactly such a quantity. Drawdown is PATH-DEPENDENT: it is the largest
peak-to-trough decline along a price path, so it depends on the ORDER in which returns arrive,
not just the start and end points. Two paths with identical 63-day returns can have completely
different maximum drawdowns. There is no simple formula for the distribution of the maximum
drawdown of a random walk over a finite horizon, so simulation is the practical route.

Implementation here: 10,000 simulated paths over a 63-trading-day horizon, parameters fitted
from a 252-day trailing window, seeded (seed=42) so results are reproducible and identical
across models. The whole computation is deterministic numpy - no LLM, no tokens. This is why
model choice cannot affect it (§32).

### FM-b. GBM vs bootstrap - the two methods implemented, and why both exist
GEOMETRIC BROWNIAN MOTION (gbm): daily log returns are drawn from a Normal(mu, sigma) fitted
to the estimation window. This is the textbook model underlying Black-Scholes. Its assumption
is that log returns are normally distributed and independent.
Known weakness: real equity returns are FAT-TAILED. Extreme moves occur far more often than a
normal distribution predicts. A Gaussian model therefore systematically understates tail risk.

BOOTSTRAP: daily log returns are resampled WITH REPLACEMENT from the actual historical returns
in the window. This makes no distributional assumption - whatever skew and fat tails the real
data contains are carried into the simulation automatically. Chosen as the default here for
that reason.

The critical shared limitation, and the one this project demonstrated: the bootstrap can only
resample moves that ALREADY OCCURRED in its window. It cannot generate a shock larger than the
largest historical observation. GBM can in principle produce an arbitrarily large move, but
only with the vanishing probability a normal distribution assigns to it. Both methods therefore
inherit the assumption that the future resembles the estimation window.

### FM-c. Drift, and why it was set to zero (see §34a)
The drift term is the average daily return in the estimation window, which compounds across the
simulated horizon and makes paths lean in the direction the asset recently moved.
Setting use_drift=False subtracts the sample mean from the return pool before drawing, so the
distribution keeps its full shape and volatility but centres on zero.
Theoretical justification: at a ~3-month horizon the standard error on an estimated mean return
is large relative to the mean itself - the drift estimate is dominated by noise. Extrapolating
it is a directional forecast disguised as a parameter.
Honest counterpoint: zero drift is also a claim, and it is wrong over long horizons, since the
equity risk premium is real and positive. The defensible statement is narrow - at a 3-month
horizon, the drift estimate is noisier than the quantity it adds.
Demonstrated cost of the choice (§34): with drift off, expected_price sits ~4% above spot
(lognormal convexity only) and systematically UNDER-predicted realized price during rallies.
The same setting that made the drawdown forecast trustworthy biased the price forecast low.
That trade-off is inherent, not a defect.

### FM-d. Drawdown as a risk measure
Maximum drawdown answers "what is the worst peak-to-trough loss to expect", which is closer to
the question an investor actually asks than variance is. Variance penalises upside and downside
symmetrically; drawdown is purely downside and path-dependent.
Reported as a distribution rather than a point estimate:
- expected_max_drawdown_pct = MEDIAN across paths (typical case)
- p95 / p99 = conservative tail estimates ("95% of paths were no worse than this")
The p95 is the quantity that matters for risk management, and the one §36 shows failed on UNH.

### FM-e. Return-over-risk ratio
Computed as (R_mean - r_horizon) / s_ret, where R_mean is the mean simulated horizon return,
r_horizon is the risk-free rate scaled to the horizon, and s_ret the standard deviation of
simulated returns. This is a Sharpe-ratio construction: excess return per unit of risk.
The risk-free rate is taken from the 13-week T-bill yield (^IRX), filtered to <= curr_date to
preserve look-ahead safety, and scaled by horizon_days/252.
Design decision worth noting: when the rate fetch fails the function returns 0.0 together with
a risk_free_available=False flag rather than silently substituting zero. A failed rate and a
genuinely zero rate must not be indistinguishable in the output.
Note the interaction with 37c: with drift off, R_mean is near zero by construction, so this
ratio is small by design. It measures the risk-adjusted return of a driftless process, not a
forecast of the asset's Sharpe ratio.

### FM-f. Look-ahead bias, and the structural guard against it
A backtest is worthless if the model can see data from after the prediction date. The guard
here is structural rather than procedural: load_ohlcv filters every price series to
<= curr_date, so any function built on it inherits the constraint. risk_free_annual applies the
same filter to the T-bill series.
This is the reason compute_max_drawdown, which looks BACKWARD, is used for SCORING by calling it
with curr_date = prediction_date + 90 days: the realized window is then genuinely in the past
relative to that call, and no separate un-filtered data path was needed (§34b).

### FM-g. Overlapping windows and effective sample size (see §34c, §35b)
Standard statistical tests assume independent observations. Scoring windows 90 days long that
step forward 7 days share 83 days of data, so consecutive observations are near-duplicates.
Independence requires non-overlapping windows, i.e. 13 weeks of separation, so 78 weekly dates
yield ~6 independent observations regardless of the ticker.
Pooling across tickers only helps to the extent the tickers are uncorrelated:
    N_eff ~= (tickers x windows) / (1 + (tickers-1) x avg_correlation)
Measured average pairwise correlation across NVDA/XOM/UNH/JPM was ~0.14, giving N_eff ~17 from
280 raw rows. The gap between 280 and 17 is the single most important caveat on every number in
§36.

### FM-h. What this project demonstrated - the actual contribution
The limitations above are textbook. What is not textbook is that they were demonstrated here
on this project's own data, in a form that shows exactly how the failure occurs:
1. A historical-window risk model produced 0/70 p95 breaches on NVDA and 0/70 on XOM, which in
   isolation reads as a well-calibrated or conservative model.
2. The SAME model, unchanged, produced 14/70 breaches on UNH, with a worst case wrong by a
   factor of ~2 (-54.23% realized vs -27.11% forecast p95).
3. The difference is not model quality but sample composition: UNH experienced a large
   idiosyncratic decline that its estimation window contained no precedent for.
CONCLUSION: a single-asset calibration result cannot distinguish "the model bounds tail risk"
from "this asset had no tail event". Only cross-sectional testing separates them. This is the
project's strongest methodological finding and it generalises beyond this codebase.
This is also the standard practitioner rationale for STRESS TESTING as a complement to
simulation-based risk measures rather than a substitute: prescribed adverse scenarios exist
precisely because a model fitted to history cannot generate a shock history has not shown.

### FM-i. Context: where these methods are used in practice
Recorded for framing, not as a project result.
- Banks: Monte Carlo VaR and expected shortfall; pricing of path-dependent and exotic
  derivatives where no closed-form solution exists.
- Insurers: catastrophe modelling and regulatory capital.
- Asset managers and pension funds: asset-liability modelling, funding-ratio projection.
- Retail planning tools: "probability the portfolio lasts N years" projections.
The known industry failure mode matches 37h: models calibrated on recent history understate
risk when the regime changes. This is why supervisory frameworks mandate stress scenarios
alongside historically-fitted risk measures.
