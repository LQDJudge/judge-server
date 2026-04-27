"""
Common helpers for csv_* checkers. Each checker takes:
  process_output (submission), judge_output (answer key) — both bytes.
  kwargs (from init.yml's checker_args):
      has_header: bool (default True)
      id_column: str (optional) — column name (or 0-based index when no header)
          identifying each row. If omitted/empty, rows are aligned by row index
          (i.e. the answer key's first data row is matched against the
          submission's first data row).
      label_column: str (optional) — column name (or 0-based index) of the
          label/score column. Defaults to the first column when no header
          (index 0), or the only column if there is exactly one.
      pretest_fraction: float in (0, 1] (default 1.0) — fraction of judge rows
          included in scoring when the *judge* is running in pretests-only mode.
          Outside pretest mode, this arg is ignored and 100% of rows are scored.
          The judge passes its pretest mode in via the magic `pretests_only`
          kwarg (set by Problem.checker() in dmoj/problem.py).
"""
import csv
import hashlib
import io
from typing import Dict, Tuple


def _row_in_pretest(row_id: str, fraction: float) -> bool:
    if fraction >= 1.0:
        return True
    if fraction <= 0.0:
        return False
    h = hashlib.md5(row_id.encode('utf-8')).digest()
    bucket = int.from_bytes(h[:4], 'big') % 1000
    return bucket < int(fraction * 1000)


def _resolve_columns(rows, has_header, id_column, label_column):
    """Return (id_idx, label_idx, data_rows). id_idx is None if rows should
    be aligned by row index instead of by an id column."""
    if has_header:
        header = rows[0]
        data_rows = rows[1:]
        # label_column defaults to the only / first column
        if label_column:
            try:
                label_idx = header.index(label_column)
            except ValueError:
                return None, None, []
        else:
            label_idx = 0 if len(header) >= 1 else None
            if label_idx is None:
                return None, None, []
        if id_column:
            try:
                id_idx = header.index(id_column)
            except ValueError:
                return None, None, []
        else:
            id_idx = None  # align by row index
        return id_idx, label_idx, data_rows
    else:
        data_rows = rows
        try:
            label_idx = int(label_column) if label_column else 0
        except ValueError:
            return None, None, []
        try:
            id_idx = int(id_column) if id_column else None
        except ValueError:
            return None, None, []
        return id_idx, label_idx, data_rows


def parse_csv(
    blob: bytes,
    has_header: bool,
    id_column: str,
    label_column: str,
    pretest_fraction: float = 1.0,
    _filter_ids: bool = False,
) -> Dict[str, str]:
    """Return {id: label_string}. When `id_column` is empty, rows are aligned
    by row index (the synthetic id is the data-row number, starting at 0).
    If `_filter_ids=True`, drops rows whose id is not in the active pretest subset.
    """
    text = blob.decode('utf-8', errors='replace')
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}

    id_idx, label_idx, data_rows = _resolve_columns(rows, has_header, id_column, label_column)
    if label_idx is None:
        return {}

    out = {}
    for row_no, row in enumerate(data_rows):
        max_idx = label_idx if id_idx is None else max(id_idx, label_idx)
        if len(row) <= max_idx:
            continue
        rid = row[id_idx] if id_idx is not None else str(row_no)
        if _filter_ids and not _row_in_pretest(rid, pretest_fraction):
            continue
        out[rid] = row[label_idx]
    return out


def aligned_pairs(judge_blob: bytes, sub_blob: bytes, **kwargs) -> Tuple[list, list, int, int]:
    """
    Returns (judge_labels, sub_labels, total, missing_in_sub).
    """
    judge_kwargs = dict(kwargs); judge_kwargs['_filter_ids'] = True
    sub_kwargs = dict(kwargs); sub_kwargs['_filter_ids'] = False
    judge = parse_csv(judge_blob, **judge_kwargs)
    sub = parse_csv(sub_blob, **sub_kwargs)
    judge_labels, sub_labels = [], []
    missing = 0
    for k, v in judge.items():
        judge_labels.append(v)
        sv = sub.get(k)
        sub_labels.append(sv)
        if sv is None:
            missing += 1
    return judge_labels, sub_labels, len(judge), missing


def feedback(metric_name: str, value: float, fraction: float = 1.0) -> str:
    suffix = f' (public LB on {int(fraction * 100)}% of rows)' if fraction < 1.0 else ''
    return f'{metric_name} = {value:.6f}{suffix}'


def normalize_lower_better(value: float, baseline) -> float:
    """Map a lower-better metric to a [0, 1] score.
    With `baseline` set: linear `max(0, 1 - value / baseline)`. Score 1 at value=0,
    score 0 at value>=baseline.
    Without `baseline`: fallback `1 / (1 + value)`.
    """
    try:
        b = float(baseline) if baseline is not None else None
    except (TypeError, ValueError):
        b = None
    if b is not None and b > 0:
        return max(0.0, min(1.0, 1.0 - value / b))
    return 1.0 / (1.0 + value)
