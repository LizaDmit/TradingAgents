from tradingagents.dataflows.stockstats_utils import compute_max_drawdown

print(compute_max_drawdown("NVDA", "2025-06-02", lookback_days=90))
print(compute_max_drawdown("NVDA", "2025-06-02", lookback_days=180))


