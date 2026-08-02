#!/usr/bin/env python3
"""Tests for run_config_check — profile validation only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_config_check as RC

HERE = Path(__file__).resolve().parent
REAL_CONFIG = HERE.parents[1] / "steering" / "run-config.md"

VALID_CONFIG = """# Run configuration

## Stage 0
- [x] `0a` [Fold](search-playbook.md#stage-0a) — 🧠
- [x] `0b` [Sync](search-playbook.md#stage-0b) — 🔧
- [x] `0e` [Sweep](search-playbook.md#stage-0e) — 🔧

```yaml
profiles:
  no-llm-sweep: ["0b", "0e"]
```
"""


class ProfileTests(unittest.TestCase):
    def test_a_valid_no_llm_profile_passes(self):
        self.assertEqual(RC.validate(VALID_CONFIG), [])

    def test_llm_stage_in_a_no_llm_profile_is_rejected(self):
        cfg = VALID_CONFIG.replace('no-llm-sweep: ["0b", "0e"]', 'no-llm-sweep: ["0a", "0b"]')
        issues = RC.validate(cfg)
        self.assertTrue(any("`0a`" in i and "not 🔧" in i for i in issues), issues)

    def test_unknown_stage_in_a_profile_is_rejected(self):
        cfg = VALID_CONFIG.replace('no-llm-sweep: ["0b", "0e"]', 'no-llm-sweep: ["0b", "9z"]')
        issues = RC.validate(cfg)
        self.assertTrue(any("unknown stage" in i and "9z" in i for i in issues), issues)

    def test_real_config_is_valid(self):
        text = REAL_CONFIG.read_text(encoding="utf-8")
        self.assertEqual(RC.validate(text), [])


if __name__ == "__main__":
    unittest.main()
