import math
from dmoj.checkers.csv_common import aligned_pairs, feedback, normalize_lower_better
from dmoj.result import CheckerResult


def _to_float(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def check(process_output: bytes, judge_output: bytes, point_value: float,
          has_header: bool = True, id_column: str = 'id', label_column: str = 'y',
          pretest_fraction: float = 1.0,
          pretests_only: bool = False,
          baseline=None,
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
        sq = 0.0
        n = 0
        for a, b in zip(j, s):
            af = _to_float(a)
            if af is None: continue
            bf = _to_float(b) if b is not None else None
            if bf is None: bf = af + 1e9
            sq += (af - bf) ** 2
            n += 1
        if n == 0:
            return CheckerResult(False, 0, feedback='no valid judge rows')
        rmse = math.sqrt(sq / n)
        score = normalize_lower_better(rmse, baseline)
        return CheckerResult(score >= 1.0 - 1e-9, point_value * score, feedback=feedback('RMSE', rmse, effective))
    except Exception as e:
        return CheckerResult(False, 0, feedback=f'checker error: {e!r}')
