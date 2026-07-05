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

### 9c. Refined input-cost ranking (from what we have seen so far)
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
- The failed subreddit names in the errors were r/2, r/0, r/5, r/-, r/6 - i.e. the characters of the date string "2025-06-02". The function treated the date as the subreddit LIST and iterated it character by character. The second positional argument is the subreddit list, not the date - my call signature was wrong.
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
Tool output remains second-order to the debate report re-injection (section 8/9), but it is not trivial: the fundamentals analyst alone ingests ~6k tokens of raw statements, and OHLCV adds ~2.3k for the market analyst. Preprocessing/trimming this data before the run (the supervisor's "preprocess data download" idea) would cut these one-time costs and is especially valuable for the backtest, where the same NVDA series would otherwise be re-fetched every week across ~78 weeks.

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
The Sentiment Analyst runs in a SINGLE call for 3,857 tokens because it was redesigned to pre-fetch its data and inject it once, with no tool loop (documented bug fix). The Market Analyst still uses the old tool-loop pattern. Converting it to the sentiment-analyst approach (pre-compute OHLCV + indicators, inject once, drop the multi-pass loop) would collapse the 3 calls toward 1 and remove the re-reads. This is the SAME work as the supervisor's "preprocess the data download" directive - the token fix and his directive coincide.

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

This section captures the reasoning behind the round-count recommendation, since it is the first question the supervisor will ask. Important caveat up front: token diagnosis measures COST, not accuracy. The accuracy claim below is an evidence-based expectation, not a proven result. The backtest is the instrument to confirm it.

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

Cutting 3 rounds -> 1 removes ~62% of total tokens (~63% of input). This is a controlled result (same script both times), so the causal claim "the round setting drove the difference" is now measured, not inferred. Directly addresses the top rigor gap in the supervisor review.

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

Supervisor's revised 2-week plan:
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

### 22d. MUST-FIX before backtesting: the history window is too short
load_ohlcv fetches only 5 years back (pd.DateOffset(years=5)), despite a docstring claiming "15 years". From mid-2026 that reaches back only to ~mid-2021, which does NOT cover the wanted training start of 1 Jan 2020. The window must be widened (>= 6-7 years, or set explicitly) or the 2020 to mid-2021 portion of the training data will be silently missing. Concrete fix required before the backtest.

### 22e. Cache filename is keyed to today's date
The cache filename embeds start and end derived from today, so a new file is created each calendar day. Fine for a backtest run completed within a single day; just be aware across days.

### 22f. Implications for the two features
- Realized max drawdown: compute directly from the load_ohlcv DataFrame (clean, date-filtered OHLCV), inheriting look-ahead safety for free. Short function on top of existing data.
- Method 4 remaining work: (a) widen the load_ohlcv window to cover 2020 (see 22d), (b) optionally route the market analyst's raw OHLCV text through load_ohlcv so it caches too and gains look-ahead safety, (c) trim the news article limits.
