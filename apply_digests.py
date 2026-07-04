"""
Method 2 implementation: feed compact report digests to the five debate agents
instead of the four full analyst reports.

WHAT IT DOES
  - Adds a digest_report() helper to agent_utils.py.
  - In the five debators (bull, bear, aggressive, conservative, neutral),
    wraps the four report reads so each debator gets a digest, not the full text.
  - Backs up every file it touches to <file>.bak before changing it.

HOW TO USE
  1. From the TradingAgents project root, run:   python apply_digests.py
  2. Re-measure:                                 python token_count.py
  3. Compare the debator token numbers to before.

HOW TO REVERT
  Run:   python apply_digests.py --revert
  (restores every .bak file)

SAFE TO RE-RUN: it skips files already patched.
"""

import os
import re
import sys

ROOT = "tradingagents"

DEBATORS = [
    "bull_researcher.py",
    "bear_researcher.py",
    "aggressive_debator.py",
    "conservative_debator.py",
    "neutral_debator.py",
]

REPORT_KEYS = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]

HELPER = '''

def digest_report(report: str, max_chars: int = 1400) -> str:
    """Compact digest of an analyst report for the debate stage.

    Keeps the opening (which carries the headline read and key figures) plus any
    explicit transaction line, and drops the long prose body. This cuts the cost
    of re-injecting the four full analyst reports into every debator on every turn.
    Tune max_chars to trade detail against tokens.
    """
    import re as _re
    if not report:
        return report
    text = str(report).strip()
    head = text[:max_chars]
    suffix = "" if len(text) <= max_chars else " [...]"
    m = _re.search(r"FINAL TRANSACTION PROPOSAL:.*", text)
    tail = "\\n" + m.group(0).strip() if (m and m.start() >= max_chars) else ""
    return head + suffix + tail
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
    n = 0
    for dirpath, _, files in os.walk(ROOT):
        for name in files:
            if name.endswith(".bak"):
                bak = os.path.join(dirpath, name)
                orig = bak[:-4]
                open(orig, "w").write(open(bak).read())
                os.remove(bak)
                n += 1
                print("reverted", orig)
    print(f"done, {n} file(s) restored")


def patch_agent_utils():
    path = find("agent_utils.py")
    if not path:
        print("ERROR: agent_utils.py not found"); return False
    text = open(path).read()
    if "def digest_report" in text:
        print("agent_utils.py already has digest_report, skipping")
        return True
    backup(path)
    open(path, "w").write(text + HELPER)
    print("patched", path, "(added digest_report)")
    return True


def patch_debator(path):
    text = open(path).read()
    if 'digest_report(state["market_report"])' in text:
        print("already patched, skipping", path)
        return
    backup(path)

    # 1) add digest_report to the agent_utils import block
    text = re.sub(
        r"(from tradingagents\.agents\.utils\.agent_utils import \()",
        r"\1\n    digest_report,",
        text,
        count=1,
    )

    # 2) wrap each report read
    for key in REPORT_KEYS:
        text = text.replace(
            f'state["{key}"]',
            f'digest_report(state["{key}"])',
        )

    open(path, "w").write(text)
    print("patched", path)


def main():
    if not os.path.isdir(ROOT):
        print(f"ERROR: run this from the TradingAgents project root (no {ROOT}/ here)")
        sys.exit(1)

    if "--revert" in sys.argv:
        revert()
        return

    if not patch_agent_utils():
        sys.exit(1)

    for name in DEBATORS:
        path = find(name)
        if path:
            patch_debator(path)
        else:
            print("WARNING: not found:", name)

    print("\nDone. Now run:  python token_count.py")
    print("To undo:       python apply_digests.py --revert")


if __name__ == "__main__":
    main()
