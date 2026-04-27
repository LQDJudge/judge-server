import bisect

from dmoj.checkers.csv_common import aligned_pairs, feedback
from dmoj.result import CheckerResult


def _binary_auc(y_true, y_score):
    """Mann–Whitney U based AUC. O((P+N) log N) via bisect on sorted negatives."""
    pairs = [(s, t) for s, t in zip(y_score, y_true) if s is not None and t is not None]
    if not pairs:
        return 0.0
    pos = [s for s, t in pairs if t == 1]
    neg = [s for s, t in pairs if t == 0]
    if not pos or not neg:
        return 0.0
    neg.sort()
    wins = ties = 0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        hi = bisect.bisect_right(neg, p)
        wins += lo
        ties += hi - lo
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def check(process_output: bytes, judge_output: bytes, point_value: float,
          has_header: bool = True, id_column: str = 'id', label_column: str = 'y',
          pretest_fraction: float = 1.0,
          pretests_only: bool = False,
          **kwargs) -> CheckerResult:
    try:
        effective = pretest_fraction if pretests_only else 1.0
        j_raw, s_raw, total, _ = aligned_pairs(
            judge_output, process_output,
            has_header=has_header, id_column=id_column, label_column=label_column,
            pretest_fraction=effective,
        )
        if total == 0:
            return CheckerResult(False, 0, feedback='empty answer key')
        y_true = [int(v) if v in ('0', '1') else None for v in j_raw]
        y_score = []
        for v in s_raw:
            try: y_score.append(float(v) if v is not None else None)
            except (TypeError, ValueError): y_score.append(None)
        auc = _binary_auc(y_true, y_score)
        return CheckerResult(auc >= 1.0 - 1e-9, point_value * auc, feedback=feedback('AUC', auc, effective))
    except Exception as e:
        return CheckerResult(False, 0, feedback=f'checker error: {e!r}')
