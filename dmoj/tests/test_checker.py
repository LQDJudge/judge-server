import unittest
from unittest import mock

from dmoj.result import CheckerResult


def check_to_bool(result):
    if isinstance(result, CheckerResult):
        return result.passed
    return bool(result)


class CheckerTest(unittest.TestCase):
    def assert_pass(self, check, str1, str2, expect=True):
        if expect:
            message = 'expecting %r to equal %r'
        else:
            message = 'expecting %r to not equal %r'
        self.assertEqual(check_to_bool(check(str1, str2, point_value=1.0)), expect, message % (str1, str2))
        self.assertEqual(check_to_bool(check(str2, str1, point_value=1.0)), expect, message % (str2, str1))

    def assert_fail(self, check, str1, str2):
        self.assert_pass(check, str1, str2, expect=False)

    def test_standard(self):
        from dmoj.checkers.standard import check

        self.assert_pass(check, b'a', b'a')
        self.assert_pass(check, b'a b', b'a  b')
        self.assert_pass(check, b'a b   \n', b'a b')
        self.assert_pass(check, b'\n\na b \n    ', b'a b')
        self.assert_pass(check, b'a\n\n\nb', b'a\nb')
        self.assert_pass(check, b'  a   \n\n', b'\n\n\n  a   \n')
        self.assert_pass(check, b'a ' * 1000, b' a' * 1000)
        self.assert_pass(check, b'a\n' * 1000, b'\n\n\na' * 1000)
        self.assert_pass(check, b'\n\n\na \n b\n', b'a b')
        self.assert_pass(check, b'a\n\n\nb', b'a b')

        self.assert_fail(check, b'a', b'b')
        self.assert_fail(check, b'ab', b'a b')
        self.assert_fail(check, b'a b', b'a b b')
        self.assert_fail(check, b'a bb', b'a b b')
        self.assert_fail(check, b'a b\\b', b'a b b')

        # Checkers should handle mixed bytes/str
        self.assert_pass(check, b'a', 'a')
        self.assert_fail(check, b'a', 'b')

    def test_linecount(self):
        from dmoj.checkers.linecount import check

        self.assert_pass(check, b'a', b'a')
        self.assert_pass(check, b'a b', b'a  b')
        self.assert_pass(check, b'a b   \n', b'a b')
        self.assert_pass(check, b'\n\na b \n    ', b'a b')
        self.assert_pass(check, b'a\n\n\nb', b'a\nb')
        self.assert_pass(check, b'  a   \n\n', b'\n\n\n  a   \n')
        self.assert_pass(check, b'a ' * 1000, b' a' * 1000)
        self.assert_pass(check, b'a\n' * 1000, b'\n\n\na' * 1000)
        self.assert_pass(check, b'a b\n', b'a b')

        self.assert_fail(check, b'a', b'b')
        self.assert_fail(check, b'\n\n\na \n b\n', b'a b')
        self.assert_fail(check, b'a\n\n\nb', b'a b')
        self.assert_fail(check, b'a b\na\n', b'a b\na b')
        self.assert_fail(check, b'ab', b'a b')

        # Checkers should handle mixed bytes/str
        self.assert_pass(check, b'a', 'a')
        self.assert_fail(check, b'a', 'b')

    def test_identical(self):
        from dmoj.checkers.identical import check

        def is_pe(res, feedback='Presentation Error, check your whitespace'):
            return res is not True and not res.passed and res.feedback == feedback

        assert check(b'a\nb\nc', b'a\nb\nc', point_value=1.0)
        assert check(b'a\nb\nc', b'a\nb\nc', point_value=1.0)
        assert is_pe(check(b'a \nb\nc', b'a\nb\nc', point_value=1.0))
        assert is_pe(check(b'a\nb\nc', b'a\nb\nc\n', point_value=1.0))
        assert is_pe(check(b'a\nb\nc', b'a\nb\nc\n', pe_allowed=False, point_value=1.0), feedback=None)


class BridgedCheckerTest(unittest.TestCase):
    def run_bridged_checker(self, **kwargs):
        from dmoj.checkers.bridged import check

        launched = {}

        class MockExecutor:
            def launch(self, *args, **launch_kwargs):
                launched.update(launch_kwargs)
                return mock.Mock(
                    returncode=0,
                    communicate=mock.Mock(return_value=(b'', b'')),
                )

        class MockContribModule:
            @classmethod
            def get_checker_args_format_string(cls):
                return '{input_file} {output_file} {answer_file}'

            @classmethod
            def parse_return_code(cls, *args, **kwargs):
                return CheckerResult(True, 1)

        case = mock.Mock()
        case.problem.time_limit = 1.5
        case.input_data_io.return_value.to_path.return_value = __file__

        with mock.patch(
            'dmoj.checkers.bridged.get_problem_root', return_value='/tmp'
        ), mock.patch(
            'dmoj.checkers.bridged.compile_with_auxiliary_files',
            return_value=MockExecutor(),
        ), mock.patch(
            'dmoj.checkers.bridged.contrib_modules',
            {'default': mock.Mock(ContribModule=MockContribModule)},
        ):
            check(
                b'process output',
                b'judge output',
                b'judge input',
                'problem-id',
                'checker.cpp',
                case,
                point_value=1,
                **kwargs,
            )

        return launched

    def test_bridged_checker_defaults_to_problem_time_limit(self):
        launched = self.run_bridged_checker()

        self.assertEqual(launched['time'], 1.5)

    def test_bridged_checker_honors_explicit_time_limit(self):
        launched = self.run_bridged_checker(time_limit=3)

        self.assertEqual(launched['time'], 3)
