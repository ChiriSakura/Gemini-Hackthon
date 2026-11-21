# StageState Stress Curves & Baseline Comparison

Generated: 2026-11-21 12:52:59

## 1. Scene Scaling Failure Curve (2 -> 8 Scenes)
| Scenes | No Rule Memory (`judge` / Invariant) | With Rule Memory (`judge` / Invariant) |
| :---: | :---: | :---: |
| 2 | 0.918 (PASS) | 0.932 (PASS) |
| 4 | 0.928 (PASS) | 0.951 (PASS) |
| 6 | 0.938 (PASS) | 0.914 (PASS) |
| 8 | 0.916 (PASS) | 0.927 (PASS) |

## 2. Stateless Baseline vs. StageState (4 Scenes)
- **Stateless Text Chaining Baseline**: `judge_score=0.564`, `all_passed=False`
- **StageState (WorldState + Rule Memory)**: `judge_score=0.951`, `invariant_passed=True`
