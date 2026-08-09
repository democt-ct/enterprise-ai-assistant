#!/usr/bin/env python
"""运行协作策略回归评测。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluation.collaboration import evaluate_collaboration_case


def main() -> None:
    path = Path("data/eval/collaboration_policy_set.json")
    cases = json.loads(path.read_text(encoding="utf-8"))
    results = [evaluate_collaboration_case(case) for case in cases]
    passed = sum(result.passed for result in results)
    for result in results:
        flag = "PASS" if result.passed else "FAIL"
        print(f"[{flag}] {result.case_id} mode={result.actual_mode} participants={result.actual_participants}")
        if result.failures:
            print("  " + ", ".join(result.failures))
    print(f"Collaboration policy: {passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
