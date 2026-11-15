# ADR-0001: Split protocol (class-aware temporal block split)

- Status: **Accepted** (2026-11-11, product owner)
- Date: 2026-11-11

## Context
FR-4 prohibits random row splits. It requires chronological, capture-aware partitions in which no sequence crosses a boundary. Several CIC-IDS2017 attacks are single short bursts within one day: DDoS lasts about 20 minutes, XSS about 20 minutes, and SQL injection about 2 minutes. A plain 70/15/15 split per day would put each burst wholly into one partition.

## Decision
1. Rows are ordered within each capture by `(timestamp, source_row_index)`. Monday, Tuesday and Wednesday are **not** in chronological order in the raw files, with 160–210k out-of-order rows each.
2. Each capture is cut into contiguous **5-minute blocks** (`split.block_minutes: 5`), giving 496 blocks in total.
3. Whole blocks are assigned to train, val or test by **iterative stratification on the raw attack subtype** (`split.stratify_by: label_raw`). Subtypes are processed rarest first, and each block goes to the split furthest below its 70/15/15 share for that subtype. Ties are broken by a seeded permutation. Stratifying on the merged class instead left the DoS variants (GoldenEye, slowloris, slowhttptest) with **no training data**.
4. **No purge** (`split.purge_minutes: 0`). Timestamps have minute resolution, so a 1-minute purge removes whole dense attack minutes: 274k rows (10%), leaving DDoS test at 1.3k rows and PortScan test at 987. Leakage control instead rests on three things:
   - whole-block assignment;
   - sequences built strictly within a block;
   - a **cross-split exact-duplicate audit** in the evaluation report. CIC-IDS2017 contains many byte-identical flows, such as about 167k duplicates in PortScan, and that inflates any split of this dataset.
5. XGBoost out-of-fold features use `StratifiedGroupKFold` over **1-minute sub-blocks** of the training partition (`xgboost.oof_group_minutes: 1`). See the amendment below.
6. The split manifest (`capture_id, source_row_index → block_id, split`) is stored with an order-independent SHA-256 hash. Re-running produces the same hash.

## Resulting support (seed 42)
| Class | Train | Val | Test |
|---|---|---|---|
| BENIGN | 1,591,104 | 340,871 | 341,122 |
| DoS | 200,985 | 16,127 | 35,549 |
| DDoS | 96,549 | 23,865 | 7,613 |
| PortScan | 102,121 | 11,412 | 45,397 |
| BruteForce | 9,467 | 2,163 | 2,205 |
| WebAttack | 1,627 | 223 | 330 |
| Bot | 1,377 | 297 | 292 |
| Rare | 32 | 7 | 8 |

Known imbalance within classes: DoS GoldenEye has 0 test rows, and SQL injection (21 rows) is train-only. Block granularity cannot spread these further.

## Amendment 1 (2026-11-11): finer OOF groups
The first full run grouped OOF folds by whole 5-minute blocks. Training PortScan is about 91% in a single block (14:50, 93k rows). Its OOF probabilities therefore came from fold models that never saw that burst, so they said "BENIGN". At test time the final XGBoost model, which *had* seen the burst, said "PortScan" for 100% of test PortScan rows. The hybrid network had never seen that input combination and predicted BENIGN for all 45,384 test PortScan windows (F1 0.00), which lowered macro-F1 to 0.808.

OOF groups are now 1-minute sub-blocks inside the training partition. The split itself (5-minute blocks, assignment and leakage boundary) is unchanged. Only how training rows are folded for OOF scoring changes, so OOF features are distributed like the final model's features on unseen blocks. Every evaluation now also reports an XGBoost-only baseline (`xgb_only_macro_f1`) so that a regression of this kind is visible.
