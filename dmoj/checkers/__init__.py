from typing import Callable, Union

from dmoj.checkers import (
    bridged,
    csv_accuracy,
    csv_auc,
    csv_f1,
    csv_logloss,
    csv_mae,
    csv_rmse,
    easy,
    floats,
    floatsabs,
    floatsrel,
    identical,
    linecount,
    linematches,
    rstripped,
    sorted,
    standard,
    unordered,
)
from dmoj.result import CheckerResult

CheckerOutput = Union[bool, CheckerResult]

CheckerCallable = Callable


class Checker:
    check: CheckerCallable
