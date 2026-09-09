import threading
import unittest
from unittest import mock

from dmoj.judge import Judge
from dmoj.packet import PacketManager


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.validate_id = '97638b94-cb13-4750-8fa0-3ea50e09ded3'
        self.manager = object.__new__(PacketManager)
        self.manager.conn = None
        self.manager._closed = True
        self.manager._send_packet = mock.Mock()
        self.judge = object.__new__(Judge)
        self.judge.packet_manager = self.manager
        self.judge._grading_lock = threading.Lock()
        self.judge._grading_lock.acquire()
        self.judge._current_validate_id = self.validate_id

    def run_validation(self):
        self.judge._validation_thread_main(self.validate_id, 'interactive01', mock.Mock())
        self.assertIsNone(self.judge._current_validate_id)
        self.assertFalse(self.judge._grading_lock.locked())

    def assert_validation_error(self, reason):
        self.manager._send_packet.assert_called_once_with(
            {
                'name': 'validate-error',
                'validate-id': self.validate_id,
                'error': reason,
            }
        )

    def test_missing_problem_reports_error_and_releases_judge(self):
        with mock.patch('dmoj.commands.validate.get_problem_root', return_value=None):
            self.run_validation()

        self.assert_validation_error('Problem interactive01 not found')

    def test_missing_validator_reports_error_and_releases_judge(self):
        with (
            mock.patch('dmoj.commands.validate.get_problem_root', return_value='/problems/interactive01'),
            mock.patch('dmoj.commands.validate.ProblemDataManager'),
            mock.patch('dmoj.commands.validate.ProblemConfig', return_value=mock.Mock(validator=None)),
        ):
            self.run_validation()

        self.assert_validation_error('No validator found')

    def test_unsupported_language_reports_error_and_releases_judge(self):
        config = mock.MagicMock()
        config.__getitem__.return_value = {'language': 'unsupported'}
        with (
            mock.patch('dmoj.commands.validate.get_problem_root', return_value='/problems/interactive01'),
            mock.patch('dmoj.commands.validate.ProblemDataManager'),
            mock.patch('dmoj.commands.validate.ProblemConfig', return_value=config),
            mock.patch('dmoj.commands.validate.all_executors', {}),
        ):
            self.run_validation()

        self.assert_validation_error('Language not supported')


if __name__ == '__main__':
    unittest.main()
