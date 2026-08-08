import os
import unittest
from unittest import mock

from dmoj.config import ConfigNode, InvalidInitException
from dmoj.problem import Problem, ProblemDataManager, TestCase as ProblemTestCase


class ProblemTest(unittest.TestCase):
    def setUp(self):
        self.data_patch = mock.patch('dmoj.problem.ProblemDataManager')
        data_mock = self.data_patch.start()
        data_mock.side_effect = lambda problem: self.problem_data

    def test_test_case_matching(self):
        class MockProblem(Problem):
            def _resolve_archive_files(self):
                return None

            def _problem_file_list(self):
                # fmt: off
                return [
                    's2.1-1.in', 's2.1-1.out',
                    's2.1.2.in', 's2.1.2.out',
                    's3.4.in', 's3.4.out',
                    '5.in', '5.OUT',
                    '6-1.in', '6-1.OUT',
                    '6.2.in', '6.2.OUT',
                    'foo/a.b.c.6.3.in', 'foo/a.b.c.6.3.OUT',
                    'bar.in.7', 'bar.out.7',
                    'INPUT8.txt', 'OUTPUT8.txt',
                    '.DS_Store',
                ]
                # fmt: on

        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            self.problem_data = ProblemDataManager(None)
            self.problem_data.update({'init.yml': 'archive: foo.zip'})
            problem = MockProblem('test', 2, 16384, {})
            self.assertEqual(
                problem.config.test_cases.unwrap(),
                [
                    {
                        'batched': [{'in': 's2.1-1.in', 'out': 's2.1-1.out'}, {'in': 's2.1.2.in', 'out': 's2.1.2.out'}],
                        'points': 1,
                    },
                    {'in': 's3.4.in', 'out': 's3.4.out', 'points': 1},
                    {'in': '5.in', 'out': '5.OUT', 'points': 1},
                    {
                        'batched': [
                            {'in': '6-1.in', 'out': '6-1.OUT'},
                            {'in': '6.2.in', 'out': '6.2.OUT'},
                            {'in': 'foo/a.b.c.6.3.in', 'out': 'foo/a.b.c.6.3.OUT'},
                        ],
                        'points': 1,
                    },
                    {'in': 'bar.in.7', 'out': 'bar.out.7', 'points': 1},
                    {'in': 'INPUT8.txt', 'out': 'OUTPUT8.txt', 'points': 1},
                ],
            )

    def test_no_init(self):
        self.problem_data = {}
        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            with self.assertRaises(InvalidInitException):
                Problem('test', 2, 16384, {})

    def test_empty_init(self):
        self.problem_data = {'init.yml': ''}
        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            with self.assertRaisesRegex(InvalidInitException, 'lack of content'):
                Problem('test', 2, 16384, {})

    def test_bad_init(self):
        self.problem_data = {'init.yml': '"'}
        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            with self.assertRaisesRegex(InvalidInitException, 'while scanning a quoted scalar'):
                Problem('test', 2, 16384, {})

    def test_blank_init(self):
        self.problem_data = {'init.yml': 'archive: does_not_exist.txt'}
        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            with self.assertRaisesRegex(InvalidInitException, 'archive file'):
                Problem('test', 2, 16384, {})

    @unittest.skipIf(os.devnull != '/dev/null', 'os.path.exists("nul") is False on Windows')
    def test_bad_archive(self):
        self.problem_data = {'init.yml': 'archive: %s' % (os.devnull,)}
        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/'
            with self.assertRaisesRegex(InvalidInitException, 'bad archive:'):
                Problem('test', 2, 16384, {})

    def test_no_testcases(self):
        class MockProblem(Problem):
            def _resolve_archive_files(self):
                return None

            def _problem_file_list(self):
                return []

        with mock.patch('dmoj.problem.get_problem_root') as gpr:
            gpr.return_value = '/proc'
            self.problem_data = ProblemDataManager(None)
            self.problem_data.update({'init.yml': 'archive: foo.zip'})
            with self.assertRaisesRegex(InvalidInitException, 'No test cases'):
                MockProblem('test', 2, 16384, {})

    def test_generator_defaults_to_problem_time_limit(self):
        executor = mock.Mock()
        process = mock.Mock()
        process.unsafe_communicate.return_value = (b'', b'')
        executor.launch.return_value = process
        problem = mock.Mock(
            id='test',
            storage_namespace=None,
            time_limit=3.5,
            problem_data={},
        )
        case = ProblemTestCase(1, 0, ConfigNode({'points': 1}), problem)

        with mock.patch('dmoj.problem.get_problem_root', return_value='/tmp'), mock.patch(
            'dmoj.problem.compile_with_auxiliary_files', return_value=executor
        ), mock.patch('dmoj.problem.parse_helper_file_error'):
            case._run_generator('generator.cpp')

        self.assertEqual(executor.launch.call_args.kwargs['time'], 3.5)

    def test_generator_config_time_limit_overrides_problem_time_limit(self):
        executor = mock.Mock()
        process = mock.Mock()
        process.unsafe_communicate.return_value = (b'', b'')
        executor.launch.return_value = process
        problem = mock.Mock(
            id='test',
            storage_namespace=None,
            time_limit=3.5,
            problem_data={},
        )
        case = ProblemTestCase(1, 0, ConfigNode({'points': 1}), problem)
        generator = ConfigNode({'source': 'generator.cpp', 'time_limit': 7})

        with mock.patch('dmoj.problem.get_problem_root', return_value='/tmp'), mock.patch(
            'dmoj.problem.compile_with_auxiliary_files', return_value=executor
        ), mock.patch('dmoj.problem.parse_helper_file_error'):
            case._run_generator(generator)

        self.assertEqual(executor.launch.call_args.kwargs['time'], 7)

    def tearDown(self):
        self.data_patch.stop()
