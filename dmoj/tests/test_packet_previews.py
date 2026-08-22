import pickle
import threading
import unittest
from io import BytesIO
from unittest import mock

from dmoj.judge import JudgeWorker
from dmoj.packet import PacketManager
from dmoj.result import CheckerResult, Result, TESTCASE_PREVIEW_MAX_BYTES


class _ProblemData:
    def __init__(self, files):
        self.files = files

    def open(self, key):
        if key not in self.files:
            raise KeyError(key)
        return BytesIO(self.files[key])


class _Problem:
    def __init__(self, files):
        self.problem_data = _ProblemData(files)


class _Case:
    def __init__(self, files=None, config=None):
        self.config = config or {'in': '1.in', 'out': '1.out'}
        self.problem = _Problem(files or {})
        self.points = 1
        self.output_prefix_length = TESTCASE_PREVIEW_MAX_BYTES


class PacketPreviewTest(unittest.TestCase):
    def test_static_input_preview_is_read_from_problem_data(self):
        result = Result(_Case(files={'1.in': b'abc'}))

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), 'abc')

    def test_static_output_preview_is_read_from_problem_data(self):
        result = Result(_Case(files={'1.out': b'answer'}))

        self.assertEqual(PacketManager._get_result_preview(result, 'output'), 'answer')

    def test_preview_exact_limit_has_no_ellipsis(self):
        result = Result(_Case(files={'1.in': b'a' * TESTCASE_PREVIEW_MAX_BYTES}))

        self.assertEqual(
            PacketManager._get_result_preview(result, 'input'),
            'a' * TESTCASE_PREVIEW_MAX_BYTES,
        )

    def test_preview_over_limit_has_ellipsis(self):
        result = Result(_Case(files={'1.in': b'a' * (TESTCASE_PREVIEW_MAX_BYTES + 1)}))

        self.assertEqual(
            PacketManager._get_result_preview(result, 'input'),
            'a' * TESTCASE_PREVIEW_MAX_BYTES + '...',
        )

    def test_result_preview_takes_precedence_over_static_file(self):
        result = Result(_Case(files={'1.in': b'static'}))
        result.input_preview = b'generated'

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), 'generated')

    def test_generator_style_case_does_not_regenerate_preview(self):
        case = _Case(config={'in': '', 'out': ''})
        case.input_data = mock.Mock(return_value=b'should not be called')
        case.output_data = mock.Mock(return_value=b'should not be called')
        result = Result(case)
        result.input_preview = b''
        result.output_preview = b''

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), '')
        self.assertEqual(PacketManager._get_result_preview(result, 'output'), '')
        case.input_data.assert_not_called()
        case.output_data.assert_not_called()

    def test_case_without_config_uses_data_methods(self):
        case = mock.Mock()
        case.input_data.return_value = b'input-data'
        case.output_data.return_value = b'answer-data'
        result = Result(case)

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), 'input-data')
        self.assertEqual(PacketManager._get_result_preview(result, 'output'), 'answer-data')

    def test_worker_attached_previews_survive_pickle(self):
        worker = object.__new__(JudgeWorker)
        result = Result(_Case(files={'1.in': b'abc', '1.out': b'answer'}))

        worker._attach_case_previews(result, result.case)
        result = pickle.loads(pickle.dumps(result))

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), 'abc')
        self.assertEqual(PacketManager._get_result_preview(result, 'output'), 'answer')

    def test_missing_static_file_returns_empty_preview(self):
        result = Result(_Case(files={}))

        self.assertEqual(PacketManager._get_result_preview(result, 'input'), '')

    def test_output_uses_shared_preview_limit(self):
        result = Result(_Case())
        result.proc_output = b'a' * (TESTCASE_PREVIEW_MAX_BYTES + 1)

        self.assertEqual(result.output, 'a' * TESTCASE_PREVIEW_MAX_BYTES + '...')

    def test_feedback_fields_are_capped_in_testcase_packet(self):
        result = Result(_Case())
        result.feedback = 'f' * (TESTCASE_PREVIEW_MAX_BYTES + 1)
        result.extended_feedback = 'e' * (TESTCASE_PREVIEW_MAX_BYTES + 1)
        manager = object.__new__(PacketManager)
        manager.conn = None
        manager._closed = True
        manager._testcase_queue_lock = threading.Lock()
        manager._testcase_queue = [(1, result, '', '')]
        manager.judge = mock.Mock(current_submission=mock.Mock(id=123))
        manager._send_packet = mock.Mock()

        manager._flush_testcase_queue()

        case_packet = manager._send_packet.call_args[0][0]['cases'][0]
        self.assertEqual(case_packet['feedback'], 'f' * TESTCASE_PREVIEW_MAX_BYTES + '...')
        self.assertEqual(case_packet['extended-feedback'], 'e' * TESTCASE_PREVIEW_MAX_BYTES + '...')

    def test_checker_result_feedback_fields_use_shared_preview_limit(self):
        result = CheckerResult(
            False,
            0,
            feedback='f' * (TESTCASE_PREVIEW_MAX_BYTES + 1),
            extended_feedback='e' * (TESTCASE_PREVIEW_MAX_BYTES + 1),
        )

        self.assertEqual(result.feedback, 'f' * TESTCASE_PREVIEW_MAX_BYTES + '...')
        self.assertEqual(result.extended_feedback, 'e' * TESTCASE_PREVIEW_MAX_BYTES + '...')


if __name__ == '__main__':
    unittest.main()
