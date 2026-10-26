import hashlib
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dmoj import executors
from dmoj.contrib import load_contrib_modules
from dmoj.executors import CPP17, PY3
from dmoj.review_evidence import digest, run_review_job
from dmoj.packet import PacketManager
from dmoj.utils import builtin_int_patch


class ReviewEvidenceProtocolTests(unittest.TestCase):
    def test_submission_input_attestation_preserves_subtask_identity(self):
        manager = SimpleNamespace(_review_case_hashes=[], _testcase_queue=[],
            _testcase_queue_lock=threading.Lock(),
            _get_result_preview=lambda *args: None,
            judge=SimpleNamespace(current_submission=SimpleNamespace(id=1)))
        result = SimpleNamespace(case=SimpleNamespace(batch=2), input_sha256='input',
            answer_sha256='answer', readable_codes=lambda: ['AC'],
            execution_time=0.01, max_memory=1024, points=1, total_points=1)
        PacketManager.test_case_status_packet(manager, 7, result)
        self.assertEqual(manager._review_case_hashes,
            [{'case': 7, 'batch': 2, 'input_sha256': 'input', 'answer_sha256': 'answer'}])
        result.case.batch = 0
        PacketManager.test_case_status_packet(manager, 8, result)
        self.assertIsNone(manager._review_case_hashes[-1]['batch'])

    def job(self, source='print(1)', language='PY3', operation='solution_probe', cases=None):
        payload = {'source': {'source': source, 'language': language, 'sha256': hashlib.sha256(source.encode()).hexdigest()},
                   'cases': cases or [{'id': 'one', 'input': '3\n'}]}
        return {'operation': operation, 'snapshot_hash': 'frozen', 'payload_hash': digest(payload), 'payload': payload}

    def test_source_and_payload_hashes_cannot_be_forged(self):
        job = self.job()
        job['payload']['source']['source'] = 'print(2)'
        with self.assertRaises(ValueError):
            run_review_job(job)
        job['payload_hash'] = digest(job['payload'])
        with self.assertRaises(ValueError):
            run_review_job(job)

    def test_extractor_language_is_python_only(self):
        with self.assertRaises(ValueError):
            run_review_job(self.job(language='CPP17', operation='extractor'))

    def builtin_job(self, name='standard', cases=None):
        job = self.job(source='', language='BUILTIN', operation='checker_probe', cases=cases or [
            {'id': 'accept', 'input': '1\n', 'expected_output': '6\n', 'candidate_output': '6\n'},
            {'id': 'reject', 'input': '1\n', 'expected_output': '6\n', 'candidate_output': '7\n'},
        ])
        job['payload'].update(builtin_checker=name, checker_args={})
        job['payload_hash'] = digest(job['payload'])
        return job

    def test_actual_builtin_checkers_accept_and_reject_without_executor(self):
        for name in ('standard', 'easy', 'identical', 'rstripped'):
            with self.subTest(name=name), patch.dict(executors.executors, {}, clear=True):
                result = run_review_job(self.builtin_job(name))
                self.assertEqual(result['processed'], 2)
                self.assertEqual(result['failed'], 0)
                self.assertEqual([case['checker_result']['passed'] for case in result['cases']], [True, False])
                self.assertEqual(result['environment']['checker_adapter'], 'native-builtin-v1')

    def test_builtin_disallows_authored_code_kwargs_and_dynamic_import(self):
        for update in ({'builtin_checker': 'bridged'}, {'builtin_checker': '../../arbitrary'},
                       {'checker_args': {'_checker': 'injected'}}):
            job = self.builtin_job()
            job['payload'].update(update)
            job['payload_hash'] = digest(job['payload'])
            with self.subTest(update=update), self.assertRaises(ValueError):
                run_review_job(job)
        job = self.builtin_job()
        job['payload']['source']['source'] = 'raise Exception()'
        job['payload']['source']['sha256'] = hashlib.sha256(b'raise Exception()').hexdigest()
        job['payload_hash'] = digest(job['payload'])
        with self.assertRaises(ValueError):
            run_review_job(job)

    def test_builtin_checker_exception_is_not_deliberate_rejection(self):
        with patch('dmoj.review_evidence.BUILTIN_CHECKERS', {'standard': lambda *a, **k: 1 / 0}):
            result = run_review_job(self.builtin_job())
        self.assertEqual(result['failed'], 2)
        self.assertNotIn('checker_result', result['cases'][0])
        self.assertEqual(result['cases'][0]['status'], 'CHECKER_PROTOCOL_ERROR')

    def test_unavailable_runtime_is_not_zero_case_success(self):
        with patch.dict(executors.executors, {}, clear=True):
            result = run_review_job(self.job())
        self.assertTrue(result['infrastructure_error'])
        self.assertEqual(result['processed'], 0)
        self.assertEqual(result['helper_limits']['stdout_bytes'], 65536)


@unittest.skipUnless(os.environ.get('DMOJ_REVIEW_SANDBOX_TESTS') == '1',
                     'Opt-in genuine cptbox sandbox tests require native build/toolchains')
class ReviewEvidenceSandboxTests(ReviewEvidenceProtocolTests):
    def setUp(self):
        load_contrib_modules()
        self.registry = patch.dict(executors.executors, PY3=PY3, CPP17=CPP17)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.python_runtime = patch.object(PY3.Executor, 'runtime_dict', {'python3': '/usr/bin/python3'})
        self.cpp_runtime = patch.object(CPP17.Executor, 'runtime_dict', {'g++17': '/usr/bin/g++'})
        self.python_runtime.start()
        self.cpp_runtime.start()
        self.addCleanup(self.python_runtime.stop)
        self.addCleanup(self.cpp_runtime.stop)

    def run_code(self, source, language='PY3'):
        return run_review_job(self.job(source, language))

    def test_actual_python_cpp_output(self):
        sources = [('PY3', 'import sys; print(2*int(sys.stdin.read()))'),
                   ('CPP17', '#include<iostream>\nint main(){int x;std::cin>>x;std::cout<<2*x;}')]
        for language, source in sources:
            with self.subTest(language=language):
                result = self.run_code(source, language)
                self.assertEqual(result['cases'][0]['status'], 'EXIT_ZERO')
                self.assertEqual(result['cases'][0]['stdout'].strip(), '6')

    def test_actual_multitest_extractor_consumes_full_input(self):
        source = '''import json, sys
tokens = iter(map(int, sys.stdin.buffer.read().split()))
t = next(tokens)
sizes = []
maximum = 0
for _ in range(t):
    n = next(tokens)
    sizes.append(n)
    for _ in range(n):
        maximum = max(maximum, next(tokens))
remaining = list(tokens)
print(json.dumps(dict(parse_status='ok', input_consumption=dict(remaining_tokens=len(remaining)),
    logical_case_count=t, statistics=dict(max_n=max(sizes), total_n=sum(sizes), max_a=maximum), limitations=[])))
'''
        result = run_review_job(self.job(source, operation='extractor',
            cases=[{'id': 'multitest', 'input': '2\n1\n7\n3\n8 9 10\n'}]))
        record = result['cases'][0]
        self.assertEqual(record['status'], 'EXIT_ZERO')
        self.assertEqual(record['statistics']['statistics'], {'max_n': 3, 'total_n': 4, 'max_a': 10})
        self.assertEqual(record['statistics']['input_consumption']['remaining_tokens'], 0)
        self.assertTrue(record['limitations'])  # Successful parsing remains a helper claim.

    def test_extractor_malformed_output_is_not_evidence_or_daemon_failure(self):
        valid = dict(parse_status='ok', input_consumption={'remaining_tokens': 0},
                     logical_case_count=1, statistics={'n': 3}, limitations=[])
        outputs = [json.dumps({}), '[' * 2000 + '0' + ']' * 2000,
                   json.dumps({**valid, 'statistics': {'n': float('nan')}})]
        for key, value in [('input_consumption', 'all'), ('logical_case_count', True),
                           ('logical_case_count', -1), ('limitations', 'none')]:
            outputs.append(json.dumps({**valid, key: value}))
        for output in outputs:
            with self.subTest(output=output[:80]):
                result = run_review_job(self.job('print(%r)' % output, operation='extractor'))
                self.assertEqual(result['cases'][0]['status'], 'INVALID_EXTRACTOR_OUTPUT')
                self.assertNotIn('statistics', result['cases'][0])

    def test_extractor_cannot_read_daemon_environment_or_spawn_processes(self):
        with patch.dict(os.environ, REVIEW_PRIVATE_TEST_TOKEN='review-secret-canary'):
            result = self.run_code('import os; print(os.environ.get("REVIEW_PRIVATE_TEST_TOKEN", "absent"))')
        self.assertEqual(result['cases'][0]['status'], 'EXIT_ZERO')
        self.assertEqual(result['cases'][0]['stdout'].strip(), 'absent')
        for source in ('import os; os.fork()',
                       'import subprocess; subprocess.run(["/bin/sh", "-c", "echo escaped"])'):
            with self.subTest(source=source):
                result = run_review_job(self.job(source, operation='extractor'))
                self.assertNotEqual(result['cases'][0]['status'], 'EXIT_ZERO')
                self.assertNotIn('escaped', result['cases'][0]['stdout'])

    def test_memory_wall_clock_and_stderr_are_bounded(self):
        for source, statuses in [
                ('import time; time.sleep(60)', {'TLE'}),
                ('x = bytearray(512 * 1024 * 1024)', {'MLE', 'EXIT_NONZERO'}),
                ('import sys; sys.stderr.write("x" * 100000)', {'OLE'})]:
            with self.subTest(source=source):
                result = self.run_code(source)
                self.assertIn(result['cases'][0]['status'], statuses)

    def test_compiler_flags_and_auxiliary_paths_cannot_expand_access(self):
        for flags in (['-include/etc/passwd'], ['-fplugin=/tmp/plugin.so'], ['-DOK;touch /tmp/escaped']):
            job = self.job('int main(){}', language='CPP17')
            job['payload']['checker'] = {'flags': flags}
            job['payload_hash'] = digest(job['payload'])
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                run_review_job(job)
        for name in ('../private.h', '/tmp/private.h', 'subdir/private.h'):
            job = self.job('int main(){}', language='CPP17')
            job['payload']['source']['aux_sources'] = {
                name: {'source': '', 'sha256': hashlib.sha256(b'').hexdigest()}}
            job['payload_hash'] = digest(job['payload'])
            with self.subTest(name=name), self.assertRaises(ValueError):
                run_review_job(job)

    def test_private_reads_compiler_reads_and_writes_are_blocked(self):
        with tempfile.TemporaryDirectory(prefix='review-private-fixture-') as directory:
            filename = os.path.join(directory, 'secret.h')
            with open(filename, 'w') as stream:
                stream.write('SECRET_DATA_MUST_NOT_BE_VISIBLE')
            result = self.run_code('print(open(%r).read())' % filename)
            self.assertNotEqual(result['cases'][0]['status'], 'EXIT_ZERO')
            self.assertNotIn('SECRET_DATA_MUST_NOT_BE_VISIBLE', result['cases'][0]['stdout'])
            result = self.run_code('#include "%s"\nint main(){}' % filename, 'CPP17')
            self.assertTrue(result.get('compile_error'))
            self.assertNotIn('SECRET_DATA_MUST_NOT_BE_VISIBLE', result['compile_error'])
            target = os.path.join(directory, 'created')
            result = self.run_code('open(%r,"w").write("x")' % target)
            self.assertNotEqual(result['cases'][0]['status'], 'EXIT_ZERO')
            self.assertFalse(os.path.exists(target))

    def test_network_timeout_output_and_crash_are_not_rejections(self):
        for source, expected, language in [
                ('import socket; socket.socket().connect(("127.0.0.1",8000))', 'EXIT_NONZERO', 'PY3'),
                ('while True: pass', 'TLE', 'PY3'),
                ('print("x"*100000)', 'OLE', 'PY3'),
                ('#include<cstdlib>\nint main(){std::abort();}', 'SIGNALLED', 'CPP17')]:
            with self.subTest(status=expected):
                result = self.run_code(source, language)
                self.assertEqual(result['cases'][0]['status'], expected)

    def test_validator_positive_negative_and_checker_challenges(self):
        source = '#include<iostream>\nint main(){int n;if(!(std::cin>>n)||n<1||n>3)return 1;return 0;}'
        result = run_review_job(self.job(source, 'CPP17', 'validator_probe',
            [{'id': 'legal', 'input': '3\n'}, {'id': 'illegal', 'input': '4\n'}]))
        self.assertEqual([case['returncode'] for case in result['cases']], [0, 1])
        source = '#include<fstream>\nint main(int argc,char**argv){int a,b;std::ifstream(argv[2])>>a;std::ifstream(argv[3])>>b;return a==b?0:1;}'
        cases = [{'id': 'valid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '6\n'},
                 {'id': 'invalid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '5\n'}]
        result = run_review_job(self.job(source, 'CPP17', 'checker_probe', cases))
        self.assertEqual([case['checker_result']['passed'] for case in result['cases']], [True, False])

    def test_python_checker_module_is_invoked_not_just_loaded(self):
        cases = [{'id': 'valid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '6\n'},
                 {'id': 'invalid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '5\n'}]
        sources = ['def check(process_output, judge_output, **kwargs):\n return process_output.strip()==judge_output.strip()',
                   'from dmoj.result import CheckerResult\ndef check(process_output, judge_output, **kwargs):\n ok=process_output.strip()==judge_output.strip()\n return CheckerResult(ok, int(ok))']
        for source in sources:
            result = run_review_job(self.job(source, 'PY3', 'checker_probe', cases))
            self.assertEqual([case['checker_result']['passed'] for case in result['cases']], [True, False])
            self.assertEqual(result['source_sha256'], hashlib.sha256(source.encode()).hexdigest())
        unsupported = 'def check(process_output, judge_output, **kwargs):\n return kwargs["case"].points'
        result = run_review_job(self.job(unsupported, 'PY3', 'checker_probe', cases))
        self.assertEqual(result['cases'][0]['status'], 'CHECKER_PROTOCOL_ERROR')
        self.assertNotIn('checker_result', result['cases'][0])

    def test_validator_default_flat_invocation_and_explicit_group(self):
        source = '#include<cstdlib>\nint main(int argc,char**argv){return argc==3&&std::atoi(argv[1])==0&&std::atoi(argv[2])==0?0:1;}'
        result = run_review_job(self.job(source, 'CPP17', 'validator_probe',
            [{'id': 'global', 'input': '3\n'}, {'id': 'group', 'input': '3\n', 'batch': 2, 'position': 4}]))
        self.assertEqual([case['returncode'] for case in result['cases']], [0, 1])
        self.assertEqual(result['cases'][0]['invocation']['argv'], ['0', '0'])
        self.assertEqual(result['cases'][1]['invocation']['argv'], ['2', '4'])

    def test_probe_positions_with_judge_builtin_int_patch(self):
        source = '#include<cstdlib>\nint main(){return 0;}'
        # Decode before applying the patch, as the real bridge packet does.
        cases = [{'id': 'legal', 'input': '3\n', 'batch': 0, 'position': 2}]
        job = self.job(source, 'CPP17', 'validator_probe', cases)
        builtin_int_patch.apply()
        try:
            result = run_review_job(job)
            self.assertEqual(result['cases'][0]['returncode'], 0)
            for invalid in (True, 1.0, '0', -1, 1000001):
                cases[0]['batch'] = invalid
                job['payload_hash'] = digest(job['payload'])
                with self.subTest(batch=invalid), self.assertRaises(ValueError):
                    run_review_job(job)
        finally:
            builtin_int_patch.unapply()

    def test_python_checker_configured_params_and_runtime_override(self):
        source = 'def check(process_output, judge_output, modulus, **kwargs):\n return int(process_output) %% modulus == int(judge_output) %% modulus and kwargs["point_value"] == 1'
        cases = [{'id': 'valid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '13\n'},
                 {'id': 'invalid', 'input': '3\n', 'expected_output': '6\n', 'candidate_output': '12\n'}]
        job = self.job(source.replace('%%', '%'), 'PY3', 'checker_probe', cases)
        job['payload']['checker_args'] = '{"modulus":7,"point_value":99}'
        job['payload_hash'] = digest(job['payload'])
        result = run_review_job(job)
        self.assertEqual([case['checker_result']['passed'] for case in result['cases']], [True, False])
        self.assertEqual(result['cases'][0]['invocation']['point_value'], 1)
        self.assertEqual(result['cases'][0]['invocation']['checker_args']['modulus'], 7)
