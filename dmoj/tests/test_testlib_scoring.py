import unittest
from types import SimpleNamespace
from unittest import mock

from dmoj.checkers import bridged
from dmoj.contrib.testlib import ContribModule
from dmoj.error import InternalError


class TestlibScoringTests(unittest.TestCase):
    def parse(self, score, point_value=20, returncode=7, **options):
        return ContribModule.parse_return_code(
            SimpleNamespace(returncode=returncode),
            None,
            point_value,
            1,
            65536,
            'feedback',
            'details',
            'checker',
            b'points ' + score,
            **options,
        )

    def test_fraction_is_default_and_scales_to_case_points(self):
        for point_value in (0, 0.2, 1, 10, 18, 20, 70, 100):
            for score in (b'0', b'0.5', b'1', b'5e-1'):
                with self.subTest(point_value=point_value, score=score):
                    result = self.parse(score, point_value)
                    self.assertAlmostEqual(result.points, float(score) * point_value)
                    self.assertTrue(result.passed)
                    self.assertEqual(result.feedback, 'feedback')
                    self.assertEqual(result.extended_feedback, 'details')

    def test_invalid_fractions_raise_internal_error(self):
        for score in (b'-0.1', b'1.01', b'50', b'1e999', b'nan', b'inf', b'garbage'):
            with self.subTest(score=score):
                with self.assertRaises(InternalError):
                    self.parse(score)

    def test_explicit_absolute_mode_is_preserved_for_legacy_problems(self):
        self.assertEqual(self.parse(b'0.5', treat_checker_points_as_absolute=True).points, 0.5)
        self.assertEqual(self.parse(b'15', treat_checker_points_as_absolute=True).points, 15)
        with self.assertRaises(InternalError):
            self.parse(b'21', treat_checker_points_as_absolute=True)

    def test_explicit_fraction_mode_remains_compatible(self):
        self.assertEqual(self.parse(b'0.5', treat_checker_points_as_fraction=True).points, 10)

    def test_percentage_mode_is_unchanged(self):
        for score, expected in ((b'0', 0), (b'0.5', 0.1), (b'50', 10), (b'100', 20)):
            with self.subTest(score=score):
                self.assertAlmostEqual(self.parse(score, treat_checker_points_as_percentage=True).points, expected)
        with self.assertRaises(InternalError):
            self.parse(b'101', treat_checker_points_as_percentage=True)

    def test_conflicting_score_modes_raise_internal_error(self):
        for options in (
            {'treat_checker_points_as_fraction': True, 'treat_checker_points_as_percentage': True},
            {'treat_checker_points_as_fraction': True, 'treat_checker_points_as_absolute': True},
            {'treat_checker_points_as_percentage': True, 'treat_checker_points_as_absolute': True},
        ):
            with self.assertRaisesRegex(InternalError, 'mutually exclusive'):
                self.parse(b'0.5', **options)

    def test_binary_verdicts_are_unchanged(self):
        for options in (
            {},
            {'treat_checker_points_as_fraction': True},
            {'treat_checker_points_as_percentage': True},
            {'treat_checker_points_as_absolute': True},
        ):
            for code, passed, points in ((0, True, 20), (1, False, 0), (2, False, 0)):
                with self.subTest(options=options, code=code):
                    result = self.parse(b'', returncode=code, **options)
                    self.assertEqual(result.passed, passed)
                    self.assertEqual(result.points, points)
            with self.assertRaises(InternalError):
                self.parse(b'', returncode=3, **options)

    def test_bridge_passes_score_modes_to_real_parser(self):
        process = mock.Mock(returncode=7)
        process.communicate.return_value = (b'feedback', b'points 0.5')
        executor = mock.Mock()
        executor.launch.return_value = process
        case = mock.Mock()
        case.problem.time_limit = 1
        case.input_data_io.return_value.to_path.return_value = __file__

        with mock.patch.object(bridged, 'get_executor', return_value=executor), mock.patch.dict(
            bridged.contrib_modules, {'testlib': SimpleNamespace(ContribModule=ContribModule)}
        ):
            for options, expected in (
                ({}, 10),
                ({'treat_checker_points_as_fraction': True}, 10),
                ({'treat_checker_points_as_percentage': True}, 0.1),
                ({'treat_checker_points_as_absolute': True}, 0.5),
            ):
                with self.subTest(options=options):
                    result = bridged.check(
                        b'output',
                        b'answer',
                        b'input',
                        'problem',
                        'checker.cpp',
                        case,
                        type='testlib',
                        point_value=20,
                        **options,
                    )
                    self.assertAlmostEqual(result.points, expected)
