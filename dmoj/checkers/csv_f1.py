from collections import defaultdict
from dmoj.checkers.csv_common import aligned_pairs, feedback
from dmoj.result import CheckerResult


def _macro_f1(true_labels, pred_labels):
    classes = set(true_labels) | set(p for p in pred_labels if p is not None)
    if not classes: return 0.0
    f1s = []
    for c in classes:
        tp = sum(1 for t, p in zip(true_labels, pred_labels) if t == c and p == c)
        fp = sum(1 for t, p in zip(true_labels, pred_labels) if t != c and p == c)
        fn = sum(1 for t, p in zip(true_labels, pred_labels) if t == c and p != c)
        if tp == 0:
            f1s.append(0.0)
            continue
        prec = tp / (tp + fp)
        rec = tp / (tp + fn)
        f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
    return sum(f1s) / len(f1s)


def check(process_output: bytes, judge_output: bytes, point_value: float,
          has_header: bool = True, id_column: str = 'id', label_column: str = 'y',
          pretest_fraction: float = 1.0,
          pretests_only: bool = False,
          **kwargs) -> CheckerResult:
    try:
        effective = pretest_fraction if pretests_only else 1.0
        j, s, total, _ = aligned_pairs(
            judge_output, process_output,
            has_header=has_header, id_column=id_column, label_column=label_column,
            pretest_fraction=effective,
        )
        if total == 0:
            return CheckerResult(False, 0, feedback='empty answer key')
        score = _macro_f1(j, s)
        return CheckerResult(score >= 1.0 - 1e-9, point_value * score, feedback=feedback('F1_macro', score, effective))
    except Exception as e:
        return CheckerResult(False, 0, feedback=f'checker error: {e!r}')
