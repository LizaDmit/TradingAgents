from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

ta = TradingAgentsGraph(debug=False, config=config)
final_state, decision = ta.propagate("NVDA", "2025-06-02")
print(final_state["investment_debate_state"]["history"])

