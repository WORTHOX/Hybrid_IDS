# Evaluation — test

- Windows: 430,692
- Macro-F1: **0.9578** (95% CI 0.9415 to 0.9675)
- Macro-F1 excluding Rare: **0.9613**
- Accuracy: 0.9991
- Benign false-positive rate: 0.00056
- ECE: 0.0003, Brier: 0.0017, temperature 1.259
- val_rows_identical_to_a_train_row: 0.2191
- test_rows_identical_to_a_train_row: 0.2980
- xgb_only_macro_f1: 0.9472

| Class | Precision | Recall | F1 | F1 95% CI | Support |
|---|---|---|---|---|---|
| BENIGN | 0.9995 | 0.9994 | 0.9994 | 0.999 to 0.999 | 339,325 |
| DoS | 0.9990 | 0.9955 | 0.9973 | 0.997 to 0.998 | 35,548 |
| DDoS | 0.9996 | 0.9949 | 0.9972 | 0.996 to 0.998 | 7,613 |
| PortScan | 0.9998 | 1.0000 | 0.9999 | 1.000 to 1.000 | 45,385 |
| BruteForce | 0.9995 | 0.9991 | 0.9993 | 0.999 to 1.000 | 2,205 |
| WebAttack | 0.9497 | 0.9787 | 0.9640 | 0.951 to 0.977 | 328 |
| Bot | 0.6372 | 0.9786 | 0.7718 | 0.735 to 0.799 | 280 |
| Rare | 1.0000 | 0.8750 | 0.9333 | 0.816 to 1.000 | 8 |

## Confusion matrix (rows = truth)

| | BENIGN | DoS | DDoS | PortScan | BruteForce | WebAttack | Bot | Rare |
|---|---|---|---|---|---|---|---|---|
| BENIGN | 339,134 | 12 | 3 | 7 | 1 | 12 | 156 | 0 |
| DoS | 154 | 35,389 | 0 | 1 | 0 | 4 | 0 | 0 |
| DDoS | 16 | 23 | 7,574 | 0 | 0 | 0 | 0 | 0 |
| PortScan | 0 | 0 | 0 | 45,385 | 0 | 0 | 0 | 0 |
| BruteForce | 1 | 0 | 0 | 0 | 2,203 | 1 | 0 | 0 |
| WebAttack | 7 | 0 | 0 | 0 | 0 | 321 | 0 | 0 |
| Bot | 6 | 0 | 0 | 0 | 0 | 0 | 274 | 0 |
| Rare | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 7 |

## Benign FPR per capture

- Friday-WorkingHours-Afternoon-DDos: 0.00213
- Friday-WorkingHours-Afternoon-PortScan: 0.00748
- Friday-WorkingHours-Morning: 0.00026
- Monday-WorkingHours: 0.00010
- Thursday-WorkingHours-Afternoon-Infilteration: 0.00023
- Thursday-WorkingHours-Morning-WebAttacks: 0.00009
- Tuesday-WorkingHours: 0.00004
- Wednesday-workingHours: 0.00032

# Evaluation — val

- Windows: 393,113
- Macro-F1: **0.9338** (95% CI 0.9185 to 0.9480)
- Macro-F1 excluding Rare: **0.9353**
- Accuracy: 0.9986
- Benign false-positive rate: 0.00110
- ECE: 0.0006, Brier: 0.0025, temperature 1.259

| Class | Precision | Recall | F1 | F1 95% CI | Support |
|---|---|---|---|---|---|
| BENIGN | 0.9995 | 0.9989 | 0.9992 | 0.999 to 0.999 | 339,101 |
| DoS | 0.9997 | 0.9938 | 0.9967 | 0.996 to 0.997 | 16,127 |
| DDoS | 1.0000 | 0.9998 | 0.9999 | 1.000 to 1.000 | 23,865 |
| PortScan | 0.9960 | 0.9958 | 0.9959 | 0.995 to 0.997 | 11,411 |
| BruteForce | 1.0000 | 0.9885 | 0.9942 | 0.992 to 0.996 | 2,087 |
| WebAttack | 0.9144 | 0.9269 | 0.9206 | 0.897 to 0.947 | 219 |
| Bot | 0.4720 | 0.9966 | 0.6406 | 0.611 to 0.674 | 296 |
| Rare | 1.0000 | 0.8571 | 0.9231 | 0.800 to 1.000 | 7 |

## Confusion matrix (rows = truth)

| | BENIGN | DoS | DDoS | PortScan | BruteForce | WebAttack | Bot | Rare |
|---|---|---|---|---|---|---|---|---|
| BENIGN | 338,728 | 5 | 0 | 31 | 0 | 7 | 330 | 0 |
| DoS | 81 | 16,027 | 0 | 15 | 0 | 4 | 0 | 0 |
| DDoS | 4 | 0 | 23,861 | 0 | 0 | 0 | 0 | 0 |
| PortScan | 40 | 0 | 0 | 11,363 | 0 | 8 | 0 | 0 |
| BruteForce | 24 | 0 | 0 | 0 | 2,063 | 0 | 0 | 0 |
| WebAttack | 16 | 0 | 0 | 0 | 0 | 203 | 0 | 0 |
| Bot | 1 | 0 | 0 | 0 | 0 | 0 | 295 | 0 |
| Rare | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |

## Benign FPR per capture

- Friday-WorkingHours-Afternoon-DDos: 0.00272
- Friday-WorkingHours-Afternoon-PortScan: 0.00535
- Friday-WorkingHours-Morning: 0.00015
- Monday-WorkingHours: 0.00016
- Thursday-WorkingHours-Afternoon-Infilteration: 0.00060
- Thursday-WorkingHours-Morning-WebAttacks: 0.00023
- Tuesday-WorkingHours: 0.00006
- Wednesday-workingHours: 0.00015
