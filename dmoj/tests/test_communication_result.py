import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dmoj.error import InternalError
from dmoj.graders.communication import CommunicationGrader
from dmoj.result import CheckerResult, Result


class CommunicationResultTests(unittest.TestCase):
    def setUp(self):
        self.grader = CommunicationGrader.__new__(CommunicationGrader)
        self.grader.contrib_type = 'cms'
        self.grader._manager_proc = SimpleNamespace(returncode=0)
        self.grader._manager_binary = object()
        self.grader._manager_stderr = b''
        self.grader._manager_time_limit = 1
        self.grader._manager_memory_limit = 65536
        self.case = SimpleNamespace(config={'checker': 'standard'}, points=10)
        self.result = SimpleNamespace(result_flag=0, proc_output=b'')

    def test_manager_failure_is_reported_even_if_contestant_also_failed(self):
        self.grader._manager_proc.returncode = 1
        self.result.result_flag = Result.RTE
        with patch('dmoj.graders.communication.parse_helper_file_error', side_effect=InternalError('manager failed')):
            with self.assertRaises(InternalError):
                self.grader.check_result(self.case, self.result)

    def test_custom_checker_runs_after_manager_accepts(self):
        self.case.config['checker'] = 'custom'
        accepted = CheckerResult(True, 10)
        with patch('dmoj.graders.communication.contrib_modules') as modules, patch(
            'dmoj.graders.standard.StandardGrader.check_result', return_value='checker-result'
        ) as checker:
            modules.__getitem__.return_value.ContribModule.parse_return_code.return_value = accepted
            self.assertEqual(self.grader.check_result(self.case, self.result), 'checker-result')
        checker.assert_called_once_with(self.case, self.result)

    def test_manager_rejection_does_not_run_custom_checker(self):
        self.case.config['checker'] = 'custom'
        rejected = CheckerResult(False, 0)
        with patch('dmoj.graders.communication.contrib_modules') as modules, patch(
            'dmoj.graders.standard.StandardGrader.check_result'
        ) as checker:
            modules.__getitem__.return_value.ContribModule.parse_return_code.return_value = rejected
            self.assertIs(self.grader.check_result(self.case, self.result), rejected)
        checker.assert_not_called()
