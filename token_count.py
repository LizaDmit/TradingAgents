"""
Per-stage token counter for TradingAgents.

HOW TO USE:
  1. Put this file in the same folder where you normally run the pipeline.
  2. Run it:   python token_count.py
  3. Copy the whole table it prints and send it back.

To do the second run, change ROUNDS below from 1 to 3 and run it again.
"""

# ====== THE ONLY THING YOU CHANGE ======
ROUNDS = 1         # first run: 1   |   second run: 3
TICKER = "NVDA"
DATE   = "2025-06-02"
# ========================================

from collections import defaultdict
from langchain_core.callbacks import BaseCallbackHandler
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


class TokenLogger(BaseCallbackHandler):
    """Records input/output tokens for every LLM call, tagged by graph node."""
    def __init__(self):
        self.calls = []
        self._node = {}

    def _tag(self, **kwargs):
        md = kwargs.get("metadata") or {}
        self._node[kwargs.get("run_id")] = md.get("langgraph_node", "?")

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._tag(**kwargs)

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._tag(**kwargs)

    def on_llm_end(self, response, **kwargs):
        node = self._node.pop(kwargs.get("run_id"), "?")
        i = o = None
        try:
            um = getattr(response.generations[0][0].message, "usage_metadata", None) or {}
            i, o = um.get("input_tokens"), um.get("output_tokens")
        except Exception:
            pass
        if i is None:
            tu = (response.llm_output or {}).get("token_usage", {})
            i, o = tu.get("prompt_tokens"), tu.get("completion_tokens")
        self.calls.append((node, i or 0, o or 0))


def main():
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = ROUNDS
    config["max_risk_discuss_rounds"] = ROUNDS

    ta = TradingAgentsGraph(debug=False, config=config)

    # Attach the logger to every LLM the graph holds, whatever they're named.
    logger = TokenLogger()
    attached = []
    for attr in dir(ta):
        if "llm" in attr.lower():
            obj = getattr(ta, attr, None)
            if hasattr(obj, "callbacks"):
                obj.callbacks = [logger]
                attached.append(attr)
    print(f"Logger attached to: {attached}\n")

    print(f"Running {TICKER} on {DATE} at ROUNDS={ROUNDS} ... (this makes real API calls)\n")
    ta.propagate(TICKER, DATE)

    # Aggregate per node.
    agg = defaultdict(lambda: [0, 0, 0])      # node -> [calls, in, out]
    for node, i, o in logger.calls:
        agg[node][0] += 1
        agg[node][1] += i
        agg[node][2] += o

    print("=" * 56)
    print(f"PER-STAGE TOKENS   (ROUNDS={ROUNDS})")
    print("=" * 56)
    print(f"{'NODE':28}{'CALLS':>6}{'IN':>10}{'OUT':>8}")
    print("-" * 56)
    tin = tout = 0
    for node, (c, i, o) in sorted(agg.items(), key=lambda x: -x[1][1]):
        print(f"{node:28}{c:>6}{i:>10}{o:>8}")
        tin += i
        tout += o
    print("-" * 56)
    print(f"{'TOTAL':28}{len(logger.calls):>6}{tin:>10}{tout:>8}")
    print(f"\ninput:output ratio = {tin / max(tout, 1):.1f} : 1")

    # If node names came back as '?', the calls are still in pipeline order:
    if all(n == "?" for n, _, _ in logger.calls):
        print("\n(Node names unavailable - here is every call in run order instead:)")
        for k, (node, i, o) in enumerate(logger.calls, 1):
            print(f"  {k:2}.  in={i:>8}  out={o:>7}")


if __name__ == "__main__":
    main()
