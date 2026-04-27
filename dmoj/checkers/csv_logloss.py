import math
from dmoj.checkers.csv_common import aligned_pairs, feedback, normalize_lower_better
from dmoj.result import CheckerResult

EPS = 1e-15


def check(process_output: bytes, judge_output: bytes, point_value: float,
          has_header: bool = True, id_column: str = 'id', label_column: str = 'y',
          pretest_fraction: float = 1.0,
          pretests_only: bool = False,
          baseline=None,
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
        loss_sum = 0.0
        n = 0
        for tv, sv in zip(j_raw, s_raw):
            try: t = int(tv)
            except (TypeError, ValueError): continue
            try: p = float(sv) if sv is not None else 0.5
            except (TypeError, ValueError): p = 0.5
            p = max(EPS, min(1.0 - EPS, p))
            loss_sum += -(t * math.log(p) + (1 - t) * math.log(1 - p))
            n += 1
        if n == 0:
            return CheckerResult(False, 0, feedback='no valid rows')
        ll = loss_sum / n
        score = normalize_lower_better(ll, baseline)
        return CheckerResult(score >= 1.0 - 1e-9, point_value * score, feedback=feedback('logloss', ll, effective))
    except Exception as e:
        return CheckerResult(False, 0, feedback=f'checker error: {e!r}')
