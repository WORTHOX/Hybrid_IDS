# ADR-0002: Label taxonomy

- Status: **Accepted** (2026-11-11, product owner)
- Date: 2026-11-11

## Mapping (raw label → class), `TAXONOMY_VERSION = 1`
| Class | Raw labels | Rows |
|---|---|---|
| BENIGN | BENIGN | 2,273,097 |
| DoS | DoS Hulk, DoS GoldenEye, DoS slowloris, DoS Slowhttptest | 252,661 |
| DDoS | DDoS | 128,027 |
| PortScan | PortScan | 158,930 |
| BruteForce | FTP-Patator, SSH-Patator | 13,835 |
| WebAttack | Web Attack – Brute Force, Web Attack – XSS, Web Attack – Sql Injection | 2,180 |
| Bot | Bot | 1,966 |
| Rare | Heartbleed (11), Infiltration (36) | 47 |

## Decisions
- **Rare is kept** as a class.
- Because its support is tiny (8 test rows), every evaluation reports:
  - macro-F1 **with and without** Rare;
  - bootstrap confidence intervals for each class.
- The release gate's 0.95 macro-F1 is judged on both numbers, and any gap is recorded as a deviation.
- The mapping lives in `src/hnids/ingestion/taxonomy.py` and is written into every bundle.
- Unknown raw labels are quarantined with `UNKNOWN_LABEL`.
