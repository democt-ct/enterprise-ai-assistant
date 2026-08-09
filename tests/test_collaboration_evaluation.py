from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.evaluation.collaboration import evaluate_collaboration_case


class CollaborationEvaluationTests(unittest.TestCase):
    def test_policy_eval_set_passes(self):
        cases = json.loads(Path("data/eval/collaboration_policy_set.json").read_text(encoding="utf-8"))
        results = [evaluate_collaboration_case(case) for case in cases]
        self.assertTrue(all(result.passed for result in results), results)


if __name__ == "__main__":
    unittest.main()
