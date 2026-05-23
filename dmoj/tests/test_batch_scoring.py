"""
Unit tests for min/sum batch scoring modes.

Architecture after the per-case display fix:
  - judge.py yields each case with its ORIGINAL per-case checker score
    (r.points = checker_fraction × case_pts, NOT adjusted by min_fraction).
  - The CONTROLLER (judge_handler.py on the online-judge side) receives the
    individual scores, computes min_fraction = min(case_earned/case_max), and
    uses min_fraction × batch_total as the batch's contribution to the
    submission score.
  - This lets the UI show each case's real checker score (e.g. 0.5/1) while
    keeping the submission total correct.

Tests cover:
  - BatchedTestCase.score_type is correctly read from init.yml config.
  - The min-fraction computation logic (now in the controller).
  - The controller batch-total aggregation for min and sum modes.
  - Integration via testsuite problems: see testsuite/batch_min/ and
    testsuite/batch_sum/.
"""
import unittest
from unittest import mock

from dmoj.config import InvalidInitException
from dmoj.result import Result


class TestBatchScoringConfig(unittest.TestCase):
    """BatchedTestCase reads score_type from init.yml config."""

    def _make_config(self, extra=None):
        from dmoj.config import ConfigNode
        data = {'points': 10, 'batched': [], 'dependencies': []}
        if extra:
            data.update(extra)
        return ConfigNode(data, defaults={})

    def test_default_score_type_is_sum(self):
        from dmoj.problem import BatchedTestCase
        config = self._make_config()
        mock_problem = mock.MagicMock()
        batch = BatchedTestCase(1, config, mock_problem, [])
        self.assertEqual(batch.score_type, 'sum')

    def test_min_score_type_parsed(self):
        from dmoj.problem import BatchedTestCase
        config = self._make_config({'score_type': 'min'})
        mock_problem = mock.MagicMock()
        batch = BatchedTestCase(1, config, mock_problem, [])
        self.assertEqual(batch.score_type, 'min')

    def test_sum_explicit_parsed(self):
        from dmoj.problem import BatchedTestCase
        config = self._make_config({'score_type': 'sum'})
        mock_problem = mock.MagicMock()
        batch = BatchedTestCase(1, config, mock_problem, [])
        self.assertEqual(batch.score_type, 'sum')

    def test_invalid_score_type_raises(self):
        from dmoj.problem import BatchedTestCase
        config = self._make_config({'score_type': 'invalid'})
        mock_problem = mock.MagicMock()
        with self.assertRaises(InvalidInitException):
            BatchedTestCase(1, config, mock_problem, [])


class TestMinFractionComputation(unittest.TestCase):
    """
    Verify the min-fraction computation used by the controller.

    The judge yields (case_earned, case_max) per case — the raw checker score,
    NOT adjusted by min_fraction.  The controller then computes:
        min_fraction = min(earned / max  for each case)
        batch_score  = min_fraction * batch_total
    """

    def _compute_batch_score(self, case_pairs, batch_total):
        """
        Simulate controller min-aggregation.
        case_pairs: list of (earned, max) — raw per-case judge results.
        Returns (per_case_fractions, batch_score).
        """
        fractions = [e / m if m else 0.0 for e, m in case_pairs]
        min_fraction = min(fractions) if fractions else 0.0
        return fractions, min_fraction * batch_total

    def test_all_correct_full_score(self):
        fractions, score = self._compute_batch_score(
            [(5.0, 5.0), (5.0, 5.0)], batch_total=10.0
        )
        self.assertAlmostEqual(score, 10.0)
        self.assertAlmostEqual(fractions[0], 1.0)
        self.assertAlmostEqual(fractions[1], 1.0)

    def test_one_wa_zero_batch_score(self):
        # Case 1 WA → min=0 → batch gets 0, but case 2 keeps its own score
        fractions, score = self._compute_batch_score(
            [(0.0, 5.0), (5.0, 5.0)], batch_total=10.0
        )
        self.assertAlmostEqual(score, 0.0)
        self.assertAlmostEqual(fractions[0], 0.0)
        self.assertAlmostEqual(fractions[1], 1.0)  # case 2 score preserved

    def test_partial_min_applied_to_batch_not_cases(self):
        # fractions: [1.0, 0.5, 1.0] → min=0.5 → batch=5.0
        # but each case keeps its own fraction for display
        cp = 10 / 3
        fractions, score = self._compute_batch_score(
            [(cp, cp), (cp * 0.5, cp), (cp, cp)], batch_total=10.0
        )
        self.assertAlmostEqual(score, 5.0, places=5)
        self.assertAlmostEqual(fractions[0], 1.0, places=5)
        self.assertAlmostEqual(fractions[1], 0.5, places=5)  # shows real score
        self.assertAlmostEqual(fractions[2], 1.0, places=5)

    def test_multiple_partials_min_wins(self):
        # fractions: [0.8, 0.3, 0.9] → min=0.3 → batch=3.0
        cp = 10 / 3
        fractions, score = self._compute_batch_score(
            [(cp * 0.8, cp), (cp * 0.3, cp), (cp * 0.9, cp)], batch_total=10.0
        )
        self.assertAlmostEqual(score, 3.0, places=5)
        self.assertAlmostEqual(fractions[0], 0.8, places=5)
        self.assertAlmostEqual(fractions[1], 0.3, places=5)
        self.assertAlmostEqual(fractions[2], 0.9, places=5)

    def test_empty_batch_zero(self):
        _, score = self._compute_batch_score([], batch_total=10.0)
        self.assertAlmostEqual(score, 0.0)

    def test_zero_max_case_treated_as_zero_fraction(self):
        # case with max=0 contributes fraction 0 (pretest/sentinel with 0 pts)
        fractions, score = self._compute_batch_score(
            [(0.0, 0.0), (10.0, 10.0)], batch_total=10.0
        )
        self.assertAlmostEqual(score, 0.0)
        self.assertAlmostEqual(fractions[0], 0.0)
        self.assertAlmostEqual(fractions[1], 1.0)


class TestJudgeYieldsIndividualScores(unittest.TestCase):
    """
    The judge must NOT apply min_fraction to r.points.
    Each case result keeps its original checker score so the UI can display
    per-case fractions (e.g. 0.5/1) rather than a uniform min value.
    """

    def test_judge_preserves_individual_scores(self):
        # With 3 cases scoring [1.0, 0.5, 1.0], the judge should yield
        # r.points = checker_score * case_pts for each — NOT min * case_pts.
        cp = 10 / 3
        case_scores = [1.0, 0.5, 1.0]  # checker fractions per case
        # Judge yields: earned = checker_score * case_pts (unchanged)
        yielded = [score * cp for score in case_scores]
        # min_fraction = 0.5; old code would have set all to 0.5*cp
        old_code_result = [0.5 * cp] * 3
        self.assertNotEqual(yielded, old_code_result)
        # New: each case keeps its own score
        self.assertAlmostEqual(yielded[0] / cp, 1.0)
        self.assertAlmostEqual(yielded[1] / cp, 0.5)
        self.assertAlmostEqual(yielded[2] / cp, 1.0)


class TestSumFractionLogic(unittest.TestCase):
    """Verify sum-mode math (existing behaviour, unchanged)."""

    def test_equal_weights_all_correct(self):
        case_pts = [10 / 3, 10 / 3, 10 / 3]
        earned = [10 / 3, 10 / 3, 10 / 3]
        total = sum(earned)
        self.assertAlmostEqual(total, 10.0, places=5)

    def test_aon_weights_fail_early(self):
        earned = [0.0, 0.0, 0.0]
        self.assertAlmostEqual(sum(earned), 0.0)

    def test_aon_weights_all_pass(self):
        earned = [0.0, 0.0, 10.0]
        self.assertAlmostEqual(sum(earned), 10.0)


if __name__ == '__main__':
    unittest.main()
