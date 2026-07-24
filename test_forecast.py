"""Quick test of the Monte Carlo drawdown forecast. Run from the project root."""

from tradingagents.dataflows.drawdown_forecast import forecast_max_drawdown

DATE = "2025-06-02"

print("Bootstrap, with drift (main method):")
print(forecast_max_drawdown("NVDA", DATE, method="bootstrap", use_drift=True))

print("\nBootstrap, no drift (conservative, ignores past uptrend):")
print(forecast_max_drawdown("NVDA", DATE, method="bootstrap", use_drift=False))

print("\nGBM, with drift (normal-returns baseline):")
print(forecast_max_drawdown("NVDA", DATE, method="gbm", use_drift=True))
