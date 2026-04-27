import unittest

from dmoj.checkers.csv_accuracy import check as accuracy_check
from dmoj.checkers.csv_rmse import check as rmse_check
from dmoj.checkers.csv_mae import check as mae_check
from dmoj.checkers.csv_f1 import check as f1_check
from dmoj.checkers.csv_auc import check as auc_check
from dmoj.checkers.csv_logloss import check as logloss_check
from dmoj.checkers.csv_common import parse_csv


JUDGE = b"id,y\n1,1\n2,0\n3,1\n4,0\n"


class CsvAccuracyTests(unittest.TestCase):
    def test_perfect_match(self):
        sub = b"id,y\n1,1\n2,0\n3,1\n4,0\n"
        r = accuracy_check(sub, JUDGE, 100.0,
                           has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 100.0)

    def test_half_correct(self):
        sub = b"id,y\n1,1\n2,1\n3,1\n4,1\n"
        r = accuracy_check(sub, JUDGE, 100.0,
                           has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 50.0)

    def test_missing_id_in_submission(self):
        sub = b"id,y\n1,1\n2,0\n3,1\n"
        r = accuracy_check(sub, JUDGE, 100.0,
                           has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 75.0)


class CsvRmseTests(unittest.TestCase):
    JUDGE_NUM = b"id,y\n1,0\n2,0\n3,0\n4,0\n"

    def test_zero_error_full_score(self):
        sub = b"id,y\n1,0\n2,0\n3,0\n4,0\n"
        r = rmse_check(sub, self.JUDGE_NUM, 100.0,
                       has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 100.0)

    def test_higher_error_lower_score(self):
        sub_small = b"id,y\n1,0.1\n2,0.1\n3,0.1\n4,0.1\n"
        sub_big = b"id,y\n1,5\n2,5\n3,5\n4,5\n"
        r1 = rmse_check(sub_small, self.JUDGE_NUM, 100.0,
                        has_header=True, id_column='id', label_column='y')
        r2 = rmse_check(sub_big, self.JUDGE_NUM, 100.0,
                        has_header=True, id_column='id', label_column='y')
        self.assertGreater(r1.points, r2.points)


class CsvMaeTests(unittest.TestCase):
    def test_zero_error(self):
        judge = b"id,y\n1,2\n2,4\n"
        sub = b"id,y\n1,2\n2,4\n"
        r = mae_check(sub, judge, 100.0,
                      has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 100.0)


class CsvF1Tests(unittest.TestCase):
    def test_perfect(self):
        judge = b"id,y\n1,a\n2,b\n3,a\n4,b\n"
        sub = b"id,y\n1,a\n2,b\n3,a\n4,b\n"
        r = f1_check(sub, judge, 100.0,
                     has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 100.0, places=4)


class CsvAucTests(unittest.TestCase):
    def test_perfect_separation(self):
        judge = b"id,y\n1,0\n2,0\n3,1\n4,1\n"
        sub = b"id,y\n1,0.1\n2,0.2\n3,0.8\n4,0.9\n"
        r = auc_check(sub, judge, 100.0,
                      has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 100.0, places=4)


class CsvLoglossTests(unittest.TestCase):
    def test_confident_correct(self):
        judge = b"id,y\n1,0\n2,1\n"
        sub_good = b"id,y\n1,0.01\n2,0.99\n"
        sub_bad  = b"id,y\n1,0.99\n2,0.01\n"
        rg = logloss_check(sub_good, judge, 100.0,
                           has_header=True, id_column='id', label_column='y')
        rb = logloss_check(sub_bad, judge, 100.0,
                           has_header=True, id_column='id', label_column='y')
        self.assertGreater(rg.points, rb.points)


class CsvBaselineTests(unittest.TestCase):
    """When `baseline` is set, score = max(0, 1 - value/baseline)."""

    JUDGE = b"id,y\n1,0\n2,0\n3,0\n4,0\n"

    def test_rmse_at_baseline_scores_zero(self):
        # RMSE = 1, baseline = 1 → 0 pts
        sub = b"id,y\n1,1\n2,1\n3,1\n4,1\n"
        r = rmse_check(sub, self.JUDGE, 100.0,
                       has_header=True, id_column='id', label_column='y',
                       baseline=1.0)
        self.assertAlmostEqual(r.points, 0.0)

    def test_rmse_below_baseline_scales_linearly(self):
        # RMSE = 0.5, baseline = 1 → 50 pts
        sub = b"id,y\n1,0.5\n2,0.5\n3,0.5\n4,0.5\n"
        r = rmse_check(sub, self.JUDGE, 100.0,
                       has_header=True, id_column='id', label_column='y',
                       baseline=1.0)
        self.assertAlmostEqual(r.points, 50.0)

    def test_rmse_above_baseline_clamped_to_zero(self):
        sub = b"id,y\n1,2\n2,2\n3,2\n4,2\n"
        r = rmse_check(sub, self.JUDGE, 100.0,
                       has_header=True, id_column='id', label_column='y',
                       baseline=1.0)
        self.assertAlmostEqual(r.points, 0.0)

    def test_mae_baseline(self):
        # MAE = 1, baseline = 2 → 50 pts
        sub = b"id,y\n1,1\n2,1\n3,1\n4,1\n"
        r = mae_check(sub, self.JUDGE, 100.0,
                      has_header=True, id_column='id', label_column='y',
                      baseline=2.0)
        self.assertAlmostEqual(r.points, 50.0)

    def test_no_baseline_falls_back_to_1_over_1_plus(self):
        # Without baseline, RMSE=1 → 1/(1+1) = 0.5 → 50 pts
        sub = b"id,y\n1,1\n2,1\n3,1\n4,1\n"
        r = rmse_check(sub, self.JUDGE, 100.0,
                       has_header=True, id_column='id', label_column='y')
        self.assertAlmostEqual(r.points, 50.0)


class CsvCheckerErrorTests(unittest.TestCase):
    def test_malformed_submission_returns_zero(self):
        sub = b"this is not csv at all"
        r = accuracy_check(sub, JUDGE, 100.0,
                           has_header=True, id_column='id', label_column='y')
        self.assertEqual(r.points, 0)


class CsvSingleColumnTests(unittest.TestCase):
    """When id_column is empty, rows are aligned by row index. When
    label_column is empty, the first column is used. Together they support
    the simplest case: a CSV with just a single label per line."""

    def test_no_header_no_id_no_label_perfect(self):
        judge = b"1\n0\n1\n0\n"
        sub = b"1\n0\n1\n0\n"
        r = accuracy_check(sub, judge, 100.0,
                           has_header=False, id_column='', label_column='')
        self.assertAlmostEqual(r.points, 100.0)

    def test_no_header_no_id_no_label_half_correct(self):
        judge = b"1\n0\n1\n0\n"
        sub = b"1\n1\n1\n1\n"
        r = accuracy_check(sub, judge, 100.0,
                           has_header=False, id_column='', label_column='')
        self.assertAlmostEqual(r.points, 50.0)

    def test_header_y_only_perfect(self):
        # Single-column CSV with header "y" — id_column blank, label_column="y"
        judge = b"y\n1\n0\n1\n0\n"
        sub = b"y\n1\n0\n1\n0\n"
        r = accuracy_check(sub, judge, 100.0,
                           has_header=True, id_column='', label_column='y')
        self.assertAlmostEqual(r.points, 100.0)

    def test_header_y_only_default_label(self):
        # label_column also blank — uses first column by default.
        judge = b"y\n1.0\n2.0\n3.0\n"
        sub = b"y\n1.0\n2.0\n3.0\n"
        r = rmse_check(sub, judge, 100.0,
                       has_header=True, id_column='', label_column='')
        self.assertAlmostEqual(r.points, 100.0)

    def test_no_header_row_alignment_off_by_one_rmse(self):
        # 4 rows of zero, all submission predict 1 → RMSE = 1, score = 0.5 → 50 pts
        judge = b"0\n0\n0\n0\n"
        sub = b"1\n1\n1\n1\n"
        r = rmse_check(sub, judge, 100.0,
                       has_header=False, id_column='', label_column='')
        self.assertAlmostEqual(r.points, 50.0)

    def test_short_submission_penalized(self):
        # Submission has only 2 of 4 rows → missing rows penalized.
        judge = b"0\n0\n0\n0\n"
        sub = b"0\n0\n"
        r = accuracy_check(sub, judge, 100.0,
                           has_header=False, id_column='', label_column='')
        # Row index alignment: rows 0,1 match (both 0); rows 2,3 missing → 2/4 = 50
        self.assertAlmostEqual(r.points, 50.0)


class CsvPretestModeTests(unittest.TestCase):
    JUDGE_BIG = b"id,y\n" + b"".join(f"{i},{i % 2}\n".encode() for i in range(100))

    def test_full_eval_when_not_in_pretests_mode(self):
        sub = self.JUDGE_BIG
        r = accuracy_check(sub, self.JUDGE_BIG, 100.0,
                           has_header=True, id_column='id', label_column='y',
                           pretest_fraction=0.5, pretests_only=False)
        self.assertAlmostEqual(r.points, 100.0)

    def test_partial_eval_when_in_pretests_mode(self):
        # Perfect submission still scores full points in pretest mode — only
        # the rows scored differ. (Matches the existing per-case pretest
        # mechanism: denominator stays at the full ContestProblem.points.)
        sub = self.JUDGE_BIG
        r = accuracy_check(sub, self.JUDGE_BIG, 100.0,
                           has_header=True, id_column='id', label_column='y',
                           pretest_fraction=0.5, pretests_only=True)
        self.assertAlmostEqual(r.points, 100.0)

    def test_pretest_subset_is_deterministic(self):
        a = parse_csv(self.JUDGE_BIG, has_header=True, id_column='id',
                      label_column='y', pretest_fraction=0.5, _filter_ids=True)
        b = parse_csv(self.JUDGE_BIG, has_header=True, id_column='id',
                      label_column='y', pretest_fraction=0.5, _filter_ids=True)
        self.assertEqual(set(a.keys()), set(b.keys()))
        self.assertGreater(len(a), 30)
        self.assertLess(len(a), 70)


if __name__ == '__main__':
    unittest.main()
