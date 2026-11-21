# StageState Stress Curves & Baseline Comparison

Generated: 2026-11-21 12:56:09

## 1. Scene Scaling Failure Curve (2 -> 8 Scenes)
| Scenes | No Rule Memory (`judge` / Invariant) | With Rule Memory (`judge` / Invariant) |
| :---: | :---: | :---: |
| 2 | 0.951 (PASS) | 0.947 (PASS) |
| 4 | 0.952 (PASS) | 0.901 (PASS) |
| 6 | 0.937 (PASS) | 0.699 (PASS) |
| 8 | 0.95 (PASS) | 0.946 (PASS) |

## 2. Actor Count Scaling Curve (1 -> 4 Actors, 4 Scenes)
| Actors | Cast | `judge()` Score | Invariant | Latency (s) |
| :---: | :--- | :---: | :---: | :---: |
| 1 | lia | 0.885 | PASS | 11.65 |
| 2 | lia, nyx | 0.855 | PASS | 11.04 |
| 3 | lia, nyx, owl_keeper | 0.82 | PASS | 10.08 |
| 4 | lia, nyx, owl_keeper, clockmaker | 0.82 | PASS | 10.67 |

## 3. Stateless Baseline vs. StageState (4 Scenes)
- **Stateless Text Chaining Baseline**: `judge_score=0.564`, `all_passed=False`
- **StageState (WorldState + Rule Memory)**: `judge_score=0.901`, `invariant_passed=True`
