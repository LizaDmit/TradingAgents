# compare_models.py - step 3d: compare model signals week by week.
import json
from pathlib import Path

ROOT, TICKER = Path("backtest_results"), "NVDA"
TIER = {"Sell": -2, "Underweight": -1, "Hold": 0, "Overweight": 1, "Buy": 2}

def load(tag):
    return {json.loads(p.read_text())["date"]: json.loads(p.read_text())
            for p in sorted((ROOT / tag).glob(f"{TICKER}_*.json"))}

a, b = load("openai"), load("deepseek")
dates = sorted(set(a) & set(b))

print(f"{'date':<12}{'openai':<14}{'deepseek':<14}{'match':<7}gap")
exact, gaps, dd_same = 0, [], 0
for d in dates:
    sa, sb = a[d]["signal"], b[d]["signal"]
    gap = TIER.get(sa, 99) - TIER.get(sb, 99)
    gaps.append(gap)
    exact += (sa == sb)
    dd_same += (a[d]["drawdown_forecast"]["expected_max_drawdown_pct"]
                == b[d]["drawdown_forecast"]["expected_max_drawdown_pct"])
    print(f"{d:<12}{sa:<14}{sb:<14}{'yes' if sa==sb else 'no':<7}{gap:+d}")

n = len(dates)
print(f"\nweeks compared:   {n}")
print(f"exact agreement:  {exact}/{n} ({100*exact/n:.0f}%)")
print(f"within one tier:  {sum(1 for g in gaps if abs(g)<=1)}/{n}")
print(f"mean tier gap (openai - deepseek): {sum(gaps)/n:+.2f}")
print(f"identical drawdown forecast: {dd_same}/{n}  (expect {n}/{n}, deterministic)")
