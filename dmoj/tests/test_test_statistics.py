import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from dmoj import executors
from dmoj.executors import PY3
from dmoj.judge import Judge
from dmoj.packet import PacketManager
from dmoj.test_statistics import parse_metrics, run_test_statistics, validate_request


def request(source='import json; print(json.dumps({"n": int(input())}))'):
    return {'problem_code': 'example', 'phase_id': 7, 'attempt_token': str(uuid.uuid4()), 'helper_source': source}


class StatisticsContractTests(unittest.TestCase):
    def test_request_cannot_supply_commands_paths_inputs_or_limits(self):
        invalid = [
            None,
            [],
            {},
            request() | {'limits': {}},
            request() | {'cases': []},
            request() | {'phase_id': True},
            request() | {'phase_id': -1},
            request() | {'problem_code': '../private'},
            request() | {'helper_source': '\ud800'},
            request() | {'attempt_token': 'not-a-uuid'},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, UnicodeError)):
                validate_request(value)
        validate_request(request())

    def test_metrics_are_finite_bounded_json_objects(self):
        self.assertEqual(parse_metrics(b'{"n":3,"sizes":[1,2]}'), {'n': 3, 'sizes': [1, 2]})
        for value in (
            '[]',
            'null',
            '{"n":NaN}',
            '{"n":1e999}',
            '{"text":"' + 'x' * 2049 + '"}',
            '{"n":' + '[' * 17 + '0' + ']' * 17 + '}',
        ):
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                parse_metrics(value)

    def test_missing_runtime_is_infrastructure_failure_not_success(self):
        with patch.dict(executors.executors, {}, clear=True):
            self.assertEqual(run_test_statistics(request())['status'], 'infrastructure_error')

    def test_cancelled_job_never_reads_package(self):
        event = threading.Event()
        event.set()
        with patch('dmoj.test_statistics.Problem') as problem:
            self.assertEqual(run_test_statistics(request(), event)['status'], 'cancelled')
        problem.assert_not_called()

    def judge(self):
        judge = object.__new__(Judge)
        judge.packet_manager = Mock()
        judge._grading_lock = threading.Lock()
        judge._current_statistics_id = None
        judge._statistics_cancel_event = threading.Event()
        judge._statistics_thread = None
        return judge

    def test_invalid_or_busy_requests_do_not_start_work(self):
        judge = self.judge()
        with patch('dmoj.judge.threading.Thread') as worker:
            judge.begin_test_statistics('../id', request())
            judge._grading_lock.acquire()
            judge.begin_test_statistics('one', request())
        worker.assert_not_called()
        self.assertEqual(
            judge.packet_manager.test_statistics_result_packet.call_args.args[1]['status'], 'infrastructure_error'
        )

    def test_cancel_only_matches_current_job(self):
        judge = self.judge()
        judge._current_statistics_id = 'one'
        judge.cancel_test_statistics('two')
        self.assertFalse(judge._statistics_cancel_event.is_set())
        judge.cancel_test_statistics('one')
        self.assertTrue(judge._statistics_cancel_event.is_set())

    def test_callback_preserves_identity_and_releases_lock_after_error(self):
        judge = self.judge()
        judge._grading_lock.acquire()
        job = request()
        with patch('dmoj.judge.run_test_statistics', side_effect=RuntimeError('secret')):
            judge._statistics_thread_main('one', job)
        result = judge.packet_manager.test_statistics_result_packet.call_args.args[1]
        self.assertEqual(result['attempt_token'], job['attempt_token'])
        self.assertEqual(result['phase_id'], 7)
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(judge._grading_lock.locked())

    def test_start_failure_releases_lock(self):
        judge = self.judge()
        with patch('dmoj.judge.threading.Thread.start', side_effect=RuntimeError('test')):
            judge.begin_test_statistics('one', request())
        self.assertFalse(judge._grading_lock.locked())

    def test_packet_dispatch(self):
        manager = object.__new__(PacketManager)
        manager.conn = None
        manager._closed = True
        manager.judge = Mock()
        job = request()
        manager._receive_packet({'name': 'test-statistics-request', 'request-id': 'one', 'request': job})
        manager.judge.begin_test_statistics.assert_called_once_with('one', job)
        manager._receive_packet({'name': 'terminate-test-statistics', 'request-id': 'one'})
        manager.judge.cancel_test_statistics.assert_called_once_with('one')


@unittest.skipUnless(os.environ.get('DMOJ_REVIEW_SANDBOX_TESTS') == '1', 'Requires native sandbox')
class StatisticsSandboxTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        (self.root / 'answer-secret').write_text('do-not-expose')
        (self.root / 'init.yml').write_text(
            'archive: tests.zip\ntest_cases:\n'
            '  - in: 1.in\n    out: 1.out\n'
            '  - batched:\n      - in: 2.in\n        out: 2.out\n'
        )
        with ZipFile(self.root / 'tests.zip', 'w') as archive:
            archive.writestr('1.in', '3\n')
            archive.writestr('1.out', 'secret-answer\n')
            archive.writestr('2.in', '5\n')
            archive.writestr('2.out', 'secret-answer\n')
        for item in (
            patch('dmoj.problem.get_problem_root', return_value=str(self.root)),
            patch.dict(executors.executors, PY3=PY3),
            patch.object(PY3.Executor, 'runtime_dict', {'python3': '/usr/bin/python3'}),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_official_input_selection_and_batched_metadata(self):
        result = run_test_statistics(request())
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([row['metrics']['n'] for row in result['cases']], [3, 5])
        self.assertEqual([row['case'] for row in result['cases']], [1, 2])
        self.assertEqual([row['batch'] for row in result['cases']], [None, 1])

    def test_host_files_answers_and_network_are_not_accessible(self):
        source = '''import json, socket
result = {}
for name, path in [('answer', %r), ('host', '/etc/shadow')]:
    try:
        open(path).read()
        result[name] = True
    except (OSError, PermissionError):
        result[name] = False
try:
    socket.socket().connect(('127.0.0.1', 8000))
    result['network'] = True
except OSError:
    result['network'] = False
print(json.dumps(result))
''' % str(self.root / 'answer-secret')
        result = run_test_statistics(request(source))
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['cases'][0]['metrics'], {'answer': False, 'host': False, 'network': False})

    def test_invalid_json_and_output_flood_are_not_metrics(self):
        for source in ('print("not json")', 'print("x"*1000000)'):
            with self.subTest(source=source):
                result = run_test_statistics(request(source))
                self.assertEqual(result['status'], 'unavailable', result)
                self.assertTrue(all(row['status'] == 'helper_error' for row in result['cases']))
                self.assertLess(len(json.dumps(result)), 2048)

    def test_infinite_helper_is_bounded(self):
        result = run_test_statistics(request('while True: pass'))
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertEqual(result['cases'][0]['status'], 'helper_error')

    def test_official_zip_only_never_runs_generator_or_reads_host_input(self):
        (self.root / 'init.yml').write_text('archive: tests.zip\ntest_cases:\n' '  - in: /etc/passwd\n    out: 1.out\n')
        result = run_test_statistics(request())
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertFalse(result['cases'])
