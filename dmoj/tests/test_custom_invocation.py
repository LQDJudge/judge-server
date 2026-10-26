import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dmoj import custom_invocation, executors
from dmoj.custom_invocation import (
    DEFAULT_LIMITS,
    MAX_INPUT,
    MAX_SOURCE,
    run_custom_invocation,
    valid_request_id,
    validate_request,
)
from dmoj.executors import C11, CPP17, JAVA, PY3
from dmoj.judge import Judge
from dmoj.judgeenv import env
from dmoj.packet import PacketManager


class InvocationSchemaTests(unittest.TestCase):
    def request(self, **extra):
        return {'language': 'PY3', 'source': 'print(input())', 'stdin': 'hello\n', **extra}

    def test_controller_import_keeps_executor_configuration_lazy(self):
        output = subprocess.check_output(
            [
                sys.executable,
                '-c',
                'import sys; import dmoj.custom_invocation; print("dmoj.executors.base_executor" in sys.modules)',
            ]
        )
        self.assertEqual(output.strip(), b'False')

    def test_input_and_limits_are_copied_and_bounded(self):
        request = self.request(limits={'output_bytes': 100})
        language, code, raw, limits = validate_request(request)
        self.assertEqual((language, code, raw), ('PY3', b'print(input())', b'hello\n'))
        self.assertEqual(limits['output_bytes'], 100)
        self.assertEqual(DEFAULT_LIMITS['output_bytes'], 65536)
        binary = dict(language='PY3', source='pass', stdin_b64=base64.b64encode(b'\x00\xff').decode())
        self.assertEqual(validate_request(binary)[2], b'\x00\xff')

    def test_rejects_paths_commands_downloads_unknown_fields_and_malformed_types(self):
        invalid = [
            None,
            [],
            {},
            self.request(language='OUTPUT'),
            self.request(language='SCAT'),
            self.request(language=['PY3']),
            self.request(source=None),
            self.request(stdin=[]),
            self.request(source='x' * (MAX_SOURCE + 1)),
            self.request(source='\ud800'),
            self.request(stdin='x' * (MAX_INPUT + 1)),
            self.request(stdin_b64='a'),
            self.request(source='é' * MAX_SOURCE),
        ]
        for name in ('command', 'args', 'cwd', 'env', 'files', 'aux_sources', 'checker', 'problem_id', 'url', 'fs'):
            invalid.append(self.request(**{name: '/etc/shadow'}))
        for request in invalid:
            with self.subTest(request=str(request)[:80]), self.assertRaises(ValueError):
                validate_request(request)

    def test_rejects_unbounded_nan_boolean_or_unknown_limits(self):
        for limits in (
            {'cpu_seconds': True},
            {'cpu_seconds': float('nan')},
            {'cpu_seconds': 4},
            {'wall_seconds': float('inf')},
            {'memory_kib': 0},
            {'memory_kib': 262145},
            {'memory_kib': 32768.0},
            {'output_bytes': -1},
            {'output_bytes': 65537},
            {'wall_seconds': 0.1},
            {'compiler_seconds': 100},
            {'memory_kib': 10**1000},
            None,
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                validate_request(self.request(limits=limits))

    def test_request_ids_cannot_be_paths_or_unbounded(self):
        for value in ('', None, [], '../job', 'x' * 65, 'job\n'):
            self.assertFalse(valid_request_id(value))
        self.assertTrue(valid_request_id('job_123-abc'))

    def test_unavailable_and_pre_cancelled_do_not_compile(self):
        with patch.dict(executors.executors, {}, clear=True):
            self.assertEqual(run_custom_invocation(self.request())['status'], 'UNAVAILABLE')
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(run_custom_invocation(self.request(), cancelled)['status'], 'CANCELLED')


class InvocationTransportTests(unittest.TestCase):
    def judge(self):
        judge = Judge.__new__(Judge)
        judge._grading_lock = threading.Lock()
        judge._current_invocation_id = None
        judge._invocation_cancel_event = threading.Event()
        judge._invocation_thread = None
        judge.packet_manager = Mock()
        return judge

    def test_busy_rejects_without_blocking_or_disturbing_existing_job(self):
        judge = self.judge()
        judge._grading_lock.acquire()
        judge._current_invocation_id = 'existing'
        judge.begin_custom_invocation('next', {'language': 'PY3', 'source': 'pass'})
        self.assertEqual(judge._current_invocation_id, 'existing')
        judge.packet_manager.custom_invocation_result_packet.assert_called_once_with(
            'next', {'protocol': 1, 'status': 'BUSY'}
        )
        self.assertTrue(judge._grading_lock.locked())

    def test_invalid_packets_do_not_launch_or_echo_unbounded_input(self):
        judge = self.judge()
        with patch('dmoj.judge.threading.Thread') as thread:
            judge.begin_custom_invocation('../bad', {})
            judge.begin_custom_invocation('valid-id', {'command': 'sh'})
            thread.assert_not_called()
        self.assertFalse(judge._grading_lock.locked())
        self.assertEqual(judge.packet_manager.custom_invocation_result_packet.call_args_list[0].args[0], None)

    def test_packet_routing_and_result_are_separate_from_review_and_scoring(self):
        judge = Mock()
        manager = SimpleNamespace(judge=judge, _send_packet=Mock())
        request = {'language': 'PY3', 'source': 'pass'}
        PacketManager._receive_packet(
            manager, {'name': 'custom-invocation-request', 'request-id': 'one', 'request': request}
        )
        judge.begin_custom_invocation.assert_called_once_with('one', request)
        PacketManager._receive_packet(manager, {'name': 'terminate-custom-invocation', 'request-id': 'one'})
        judge.cancel_custom_invocation.assert_called_once_with('one')
        PacketManager.custom_invocation_result_packet(manager, 'one', {'status': 'EXIT_ZERO'})
        manager._send_packet.assert_called_once_with(
            {
                'name': 'custom-invocation-result',
                'request-id': 'one',
                'result': {'status': 'EXIT_ZERO'},
            }
        )

    def test_worker_releases_lock_on_failure_and_does_not_send_traceback(self):
        judge = self.judge()
        judge._grading_lock.acquire()
        judge._current_invocation_id = 'one'
        with patch('dmoj.judge.run_custom_invocation', side_effect=RuntimeError('private host details')):
            judge._custom_invocation_thread_main('one', {})
        judge.packet_manager.custom_invocation_result_packet.assert_called_once_with(
            'one', {'protocol': 1, 'status': 'INTERNAL_ERROR'}
        )
        self.assertFalse(judge._grading_lock.locked())
        self.assertIsNone(judge._current_invocation_id)

    def test_thread_start_failure_releases_reservation(self):
        judge = self.judge()
        with patch('dmoj.judge.threading.Thread.start', side_effect=RuntimeError('test')):
            judge.begin_custom_invocation('one', {'language': 'PY3', 'source': 'pass'})
        self.assertFalse(judge._grading_lock.locked())
        self.assertIsNone(judge._current_invocation_id)

    def test_cancel_only_matches_current_identity(self):
        judge = self.judge()
        judge._current_invocation_id = 'one'
        judge.cancel_custom_invocation('two')
        self.assertFalse(judge._invocation_cancel_event.is_set())
        judge.cancel_custom_invocation('one')
        self.assertTrue(judge._invocation_cancel_event.is_set())


@unittest.skipUnless(os.environ.get('DMOJ_REVIEW_SANDBOX_TESTS') == '1', 'Requires native sandbox')
class InvocationSandboxTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch.dict(executors.executors, PY3=PY3, CPP17=CPP17, C11=C11, JAVA=JAVA),
            patch.object(PY3.Executor, 'runtime_dict', {'python3': '/usr/bin/python3'}),
            patch.object(CPP17.Executor, 'runtime_dict', {'g++17': '/usr/bin/g++'}),
            patch.object(C11.Executor, 'runtime_dict', {'gcc11': '/usr/bin/gcc'}),
            patch.object(
                JAVA.Executor, 'runtime_dict', {'java': '/usr/bin/java', 'javac': '/usr/bin/javac', 'java_vm': 'server'}
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_code(self, source, language='PY3', stdin='', **kwargs):
        return run_custom_invocation({'source': source, 'language': language, 'stdin': stdin, **kwargs})

    def test_actual_python_c_cpp_java_stdin_stdout(self):
        for language, source in (
            ('PY3', 'print(2*int(input()))'),
            ('C11', '#include<stdio.h>\nint main(){int n;scanf("%d",&n);printf("%d",2*n);}'),
            ('CPP17', '#include<iostream>\nint main(){int n;std::cin>>n;std::cout<<2*n;}'),
            (
                'JAVA',
                'import java.util.*; public class Main {public static void main(String[] a){System.out.println(2*new Scanner(System.in).nextInt());}}',
            ),
        ):
            with self.subTest(language=language):
                result = self.run_code(source, language, '3\n')
                self.assertEqual(result['status'], 'EXIT_ZERO', result)
                self.assertEqual(result['stdout'].strip(), '6')
                self.assertGreaterEqual(result['time'], 0)
                self.assertGreater(result['memory'], 0)

    def test_binary_stderr_and_nonzero_exit(self):
        result = self.run_code(
            'import sys; sys.stdout.buffer.write(bytes([0,255])); sys.stderr.write("debug"); sys.exit(7)'
        )
        self.assertEqual(result['status'], 'EXIT_NONZERO', result)
        self.assertEqual(result['returncode'], 7)
        self.assertEqual(base64.b64decode(result['stdout_b64']), b'\x00\xff')
        self.assertEqual(result['stderr'], 'debug')

    def test_compile_error_is_terminal_and_bounded(self):
        result = self.run_code('this is not C++', 'CPP17')
        self.assertEqual(result['status'], 'COMPILE_ERROR', result)
        self.assertTrue(result['compile_error'])
        self.assertLessEqual(len(result['compile_error'].encode()), 65539)

    def test_excess_compiler_output_is_bounded_and_cleans_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(env.raw_config, tempdir=directory):
            result = self.run_code('#error ' + 'x' * 80000, 'CPP17')
            self.assertEqual(result['status'], 'COMPILE_ERROR', result)
            self.assertLessEqual(len(result['compile_error'].encode()), 65539)
            self.assertEqual(os.listdir(directory), [])

    def test_cpu_and_wall_timeouts(self):
        for source in ('while True: pass', 'import time; time.sleep(30)'):
            result = self.run_code(source, limits={'cpu_seconds': 0.1, 'wall_seconds': 0.2})
            self.assertEqual(result['status'], 'TLE', result)

    def test_runtime_memory_is_bounded(self):
        result = self.run_code('x=bytearray(512*1024*1024)', limits={'memory_kib': 32768})
        self.assertIn(result['status'], ('MLE', 'EXIT_NONZERO'), result)

    def test_stdout_and_stderr_limits(self):
        for source in ('print("x"*100000)', 'import sys; sys.stderr.write("x"*100000)'):
            result = self.run_code(source, limits={'output_bytes': 128})
            self.assertEqual(result['status'], 'OLE', result)
            self.assertTrue(result['output_truncated'])

    def test_private_host_files_environment_and_network_are_unavailable(self):
        with tempfile.NamedTemporaryFile() as private:
            private.write(b'private-invocation-secret')
            private.flush()
            result = self.run_code('print(open(%r).read())' % private.name)
            self.assertNotIn('private-invocation-secret', result.get('stdout', ''))
            self.assertNotEqual(result['status'], 'EXIT_ZERO')
        with patch.dict(os.environ, INVOCATION_SECRET='private-env-secret'):
            result = self.run_code('import os; print(os.getenv("INVOCATION_SECRET"))')
        self.assertEqual(result['stdout'].strip(), 'None')
        result = self.run_code('import socket; socket.socket().connect(("127.0.0.1",8000))')
        self.assertNotEqual(result['status'], 'EXIT_ZERO')

    def test_runtime_cancellation_and_followup_job(self):
        judge = InvocationTransportTests().judge()
        result_ready = threading.Event()
        results = []
        judge.packet_manager.custom_invocation_result_packet.side_effect = lambda *args: (
            results.append(args),
            result_ready.set(),
        )
        manager = SimpleNamespace(judge=judge)
        PacketManager._receive_packet(
            manager,
            {
                'name': 'custom-invocation-request',
                'request-id': 'one',
                'request': {'language': 'PY3', 'source': 'while True: pass'},
            },
        )
        time.sleep(0.1)
        PacketManager._receive_packet(manager, {'name': 'terminate-custom-invocation', 'request-id': 'one'})
        self.assertTrue(result_ready.wait(5))
        self.assertEqual(results[0][1]['status'], 'CANCELLED', results)
        deadline = time.monotonic() + 2
        while judge._grading_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(judge._grading_lock.locked())
        self.assertEqual(self.run_code('print(123)')['stdout'].strip(), '123')

    def test_cancellation_while_compiling(self):
        original = custom_invocation._watch_cancellation
        event, compiling = threading.Event(), threading.Event()

        def watch(process, cancel):
            original(process, cancel)
            compiling.set()

        results = []
        with patch('dmoj.custom_invocation._watch_cancellation', side_effect=watch):
            worker = threading.Thread(
                target=lambda: results.append(
                    run_custom_invocation(
                        {'language': 'CPP17', 'source': '#include<bits/stdc++.h>\nint main(){}'}, event
                    )
                )
            )
            worker.start()
            self.assertTrue(compiling.wait(5))
            event.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['status'], 'CANCELLED', results)

    def test_repeated_calls_leave_no_pipes_or_directories(self):
        before = len(os.listdir('/proc/self/fd'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(env.raw_config, tempdir=directory):
            for _ in range(8):
                self.assertEqual(self.run_code('print("x"*10000)', limits={'output_bytes': 10})['status'], 'OLE')
            self.assertEqual(os.listdir(directory), [])
        self.assertLessEqual(len(os.listdir('/proc/self/fd')), before + 2)


@unittest.skipUnless(os.environ.get('DMOJ_REVIEW_SANDBOX_TESTS') == '1', 'Requires native sandbox')
class InvocationWireTests(unittest.TestCase):
    def setUp(self):
        InvocationSandboxTests.setUp(self)

    def test_compressed_wire_handshake_run_busy_ping_cancel_and_invalid(self):
        worker_socket, bridge_socket = socket.socketpair()
        self.addCleanup(worker_socket.close)
        self.addCleanup(bridge_socket.close)
        worker_socket.settimeout(10)
        bridge_socket.settimeout(10)
        manager = PacketManager.__new__(PacketManager)
        manager.host, manager.port = 'local-test', 0
        manager._closed = False
        manager.conn, manager.input = worker_socket, worker_socket.makefile('rb')
        self.addCleanup(manager.input.close)
        manager._lock = threading.RLock()
        manager.judge = InvocationTransportTests().judge()
        manager.judge.packet_manager = manager
        bridge_input = bridge_socket.makefile('rb')
        self.addCleanup(bridge_input.close)
        failures = []

        def serve():
            try:
                manager.handshake([], {}, 'test-only', 'test-only')
                for _ in range(6):
                    manager._receive_packet(manager._read_single())
            except Exception as error:
                failures.append(error)

        def send(packet):
            raw = zlib.compress(json.dumps(packet).encode())
            bridge_socket.sendall(PacketManager.SIZE_PACK.pack(len(raw)) + raw)

        def receive():
            size = PacketManager.SIZE_PACK.unpack(bridge_input.read(4))[0]
            return json.loads(zlib.decompress(bridge_input.read(size)))

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            handshake = receive()
            self.assertEqual(handshake['custom-invocation-protocol'], 1)
            self.assertEqual(handshake['key'], 'test-only')
            send({'name': 'handshake-success'})
            send(
                {
                    'name': 'custom-invocation-request',
                    'request-id': 'hello',
                    'request': {'language': 'PY3', 'source': 'print(int(input())*2)', 'stdin': '3\n'},
                }
            )
            result = receive()
            self.assertEqual(result['name'], 'custom-invocation-result')
            self.assertEqual(result['request-id'], 'hello')
            self.assertEqual(result['result']['stdout'], '6\n')
            time.sleep(0.05)
            send(
                {
                    'name': 'custom-invocation-request',
                    'request-id': 'slow',
                    'request': {'language': 'PY3', 'source': 'while True: pass'},
                }
            )
            time.sleep(0.1)
            send(
                {
                    'name': 'custom-invocation-request',
                    'request-id': 'busy',
                    'request': {'language': 'PY3', 'source': 'pass'},
                }
            )
            self.assertEqual(receive()['result']['status'], 'BUSY')
            send({'name': 'ping', 'when': 123})
            self.assertEqual(receive()['name'], 'ping-response')
            send({'name': 'terminate-custom-invocation', 'request-id': 'slow'})
            result = receive()
            self.assertEqual(result['request-id'], 'slow')
            self.assertEqual(result['result']['status'], 'CANCELLED')
            send({'name': 'custom-invocation-request', 'request-id': 'bad', 'request': {'command': 'sh'}})
            self.assertEqual(receive()['result']['status'], 'INVALID_REQUEST')
        finally:
            manager.judge._invocation_cancel_event.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(failures, failures)
