from dmoj.checkers.csv_common import aligned_pairs, feedback
from dmoj.result import CheckerResult


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
        correct = sum(1 for a, b in zip(j, s) if a == b and b is not None)
        acc = correct / total
        return CheckerResult(acc >= 1.0 - 1e-9, point_value * acc, feedback=feedback('accuracy', acc, effective))
    except Exception as e:
        return CheckerResult(False, 0, feedback=f'checker error: {e!r}')
