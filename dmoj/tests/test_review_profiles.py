import hashlib
import base64
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from dmoj import executors
from dmoj.contrib import load_contrib_modules
from dmoj.executors import C11, CPP17, JAVA, PY3
from dmoj.review_evidence import digest, run_review_job
from dmoj.review_profiles import validate_package


def file(source):
    return {'source': source, 'sha256': hashlib.sha256(source.encode()).hexdigest()}


class ProfilePacketTests(unittest.TestCase):
    def test_controller_import_does_not_freeze_executor_configuration(self):
        result = subprocess.check_output([sys.executable, '-c', 'import sys; import dmoj.review_evidence; print("dmoj.executors.base_executor" in sys.modules)'])
        self.assertEqual(result.strip(), b'False')

    def test_rejects_host_paths_and_commands(self):
        for package in (
            {'execution': 'custom', 'files': {}},
            {'execution': 'batch', 'files': {'../secret.h': file('x')}},
            {'execution': 'batch', 'files': {}, 'command': 'sh'},
            {'execution': 'batch', 'files': {}, 'file_io': {'input': '/etc/passwd'}},
            {'execution': 'communication', 'files': {}, 'num_processes': 100000},
        ):
            with self.subTest(package=package), self.assertRaises(ValueError):
                validate_package(package)


@unittest.skipUnless(os.environ.get('DMOJ_REVIEW_SANDBOX_TESTS') == '1', 'Requires native sandbox')
class NativeProfileTests(unittest.TestCase):
    def setUp(self):
        load_contrib_modules()
        for patcher in (
            patch.dict(executors.executors, PY3=PY3, CPP17=CPP17, C11=C11, JAVA=JAVA),
            patch.object(PY3.Executor, 'runtime_dict', {'python3': '/usr/bin/python3'}),
            patch.object(CPP17.Executor, 'runtime_dict', {'g++17': '/usr/bin/g++'}),
            patch.object(C11.Executor, 'runtime_dict', {'gcc11': '/usr/bin/gcc'}),
            patch.object(JAVA.Executor, 'runtime_dict', {'java': '/usr/bin/java', 'javac': '/usr/bin/javac', 'java_vm': 'server'}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_probe(self, source, package=None, language='PY3', answer='6\n'):
        payload = {'source': {**file(source), 'language': language},
                   'package': package or {'execution': 'batch', 'files': {}},
                   'cases': [{'id': 'toy', 'input': '3\n', 'expected_output': answer}]}
        return run_review_job({'operation': 'profile_probe', 'snapshot_hash': 'frozen',
                               'payload_hash': digest(payload), 'payload': payload})

    def test_native_standard_accept_and_reject(self):
        for value, passed in ((6, True), (7, False)):
            with self.subTest(value=value):
                result = self.run_probe('print(%s)' % value)
                self.assertEqual(result['processed'], 1, result)
                self.assertEqual(result['cases'][0]['checker_result']['passed'], passed, result)

    def test_frozen_tolerant_checker_is_used(self):
        package = {'execution': 'batch', 'files': {},
                   'checker': {'kind': 'builtin', 'name': 'floatsabs', 'args': {'precision': 3}}}
        result = self.run_probe('print(6.0001)', package)
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)
        result = self.run_probe('print(6.1)', package)
        self.assertFalse(result['cases'][0]['checker_result']['passed'], result)

    def test_frozen_python_checker_runs_in_sandbox(self):
        package = {'execution': 'batch', 'files': {
            'checker.py': file('def check(process_output, judge_output, **kwargs): return process_output.strip() == b"accepted"')},
            'checker': {'kind': 'source', 'filename': 'checker.py', 'language': 'PY3', 'type': 'lqdoj', 'args': {}}}
        result = self.run_probe('print("accepted")', package)
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)
        result = self.run_probe('print("rejected")', package)
        self.assertFalse(result['cases'][0]['checker_result']['passed'], result)
        package['files']['checker.py'] = file('def check(*args, **kwargs): return bool(open("/etc/shadow").read())')
        result = self.run_probe('print("accepted")', package)
        self.assertNotEqual(result['cases'][0]['status'], 'EXIT_ZERO', result)

    def test_frozen_cpp_checker_is_used(self):
        source = '#include<fstream>\nint main(int argc,char**argv){int a,b;std::ifstream(argv[2])>>a;std::ifstream(argv[3])>>b;return a==b?0:1;}'
        package = {'execution': 'batch', 'files': {'checker.cpp': file(source)},
                   'checker': {'kind': 'source', 'filename': 'checker.cpp', 'language': 'CPP17', 'type': 'lqdoj', 'args': {}}}
        for value, passed in ((6, True), (7, False)):
            result = self.run_probe('print(%s)' % value, package)
            self.assertEqual(result['cases'][0]['checker_result']['passed'], passed, result)

    def test_binary_answers_are_preserved(self):
        payload = {'source': {**file('import sys; sys.stdout.buffer.write(bytes([0,255,13,10]))'), 'language': 'PY3'},
                   'package': {'execution': 'batch', 'files': {}, 'binary': True,
                               'checker': {'kind': 'builtin', 'name': 'identical', 'args': {}}},
                   'cases': [{'id': 'binary', 'input': '', 'expected_output': '',
                              'expected_output_b64': base64.b64encode(bytes([0,255,13,10])).decode()}]}
        result = run_review_job({'operation': 'profile_probe', 'snapshot_hash': 'frozen', 'payload_hash': digest(payload), 'payload': payload})
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)

    def test_signature_can_compose_with_interaction_and_communication(self):
        interactive = '#include<iostream>\nint main(){std::cout<<3<<std::endl;int x;if(!(std::cin>>x))return 1;return x==6?0:1;}'
        communication = '#include<fstream>\nint main(int argc,char**argv){std::ofstream out(argv[2]);std::ifstream in(argv[1]);out<<3<<std::endl;int x;if(!(in>>x))return 1;return x==6?0:1;}'
        for execution, manager in (('interactive', interactive), ('communication', communication)):
            package = {'execution': execution, 'num_processes': 1,
                       'files': {'grader.py': file('import _submission\nprint(_submission.twice(int(input())), flush=True)'), 'manager.cpp': file(manager)},
                       'signature': {'family': 'python', 'entry': 'grader.py'},
                       'component': {'files': ['manager.cpp'], 'language': 'CPP17', 'type': 'default'}}
            for factor, passed in ((2, True), (3, False)):
                result = self.run_probe('def twice(x): return %s*x' % factor, package)
                self.assertEqual(result['cases'][0]['checker_result']['passed'], passed, result)

    def test_python_signature_uses_official_entry(self):
        package = {'execution': 'batch', 'files': {'grader.py': file('import _submission\nprint(_submission.twice(int(input())))')},
                   'signature': {'family': 'python', 'entry': 'grader.py'}}
        result = self.run_probe('def twice(x): return 2*x', package)
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)

    def test_cpp_signature_uses_header_and_linked_entry(self):
        package = {'execution': 'batch', 'files': {
            'task.h': file('int twice(int);'),
            'grader.cpp': file('#include<iostream>\n#include "task.h"\nint main(){int x;std::cin>>x;std::cout<<twice(x);}')},
            'signature': {'family': 'c', 'entry': 'grader.cpp', 'header': 'task.h'}}
        result = self.run_probe('int twice(int x){return 2*x;}', package, 'CPP17')
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)

    def test_named_files_use_native_mapping(self):
        result = self.run_probe('open("task.out","w").write(str(2*int(open("task.in").read())))',
                                {'execution': 'batch', 'files': {}, 'file_io': {'input': 'task.in', 'output': 'task.out'}})
        self.assertTrue(result['cases'][0]['checker_result']['passed'], result)

    def test_c_signature_and_java_signature(self):
        for runtime, package, source in (
            ('C11', {'execution': 'batch', 'files': {
                'task.h': file('int twice(int);'),
                'grader.c': file('#include<stdio.h>\n#include "task.h"\nint main(){int x;scanf("%d",&x);printf("%d",twice(x));}')},
                'signature': {'family': 'c', 'entry': 'grader.c', 'header': 'task.h'}}, 'int twice(int x){return 2*x;}'),
            ('JAVA', {'execution': 'batch', 'files': {'grader.java': file('public class grader {public static void main(String[] args) {java.util.Scanner s=new java.util.Scanner(System.in);System.out.println(solution.twice(s.nextInt()));}}')},
                'signature': {'family': 'java', 'entry': 'grader.java'}}, 'public class solution {public static int twice(int x){return 2*x;}}'),
        ):
            with self.subTest(runtime=runtime):
                result = self.run_probe(source, package, runtime)
                self.assertEqual(result['processed'], 1, result)
                self.assertTrue(result['cases'][0]['checker_result']['passed'], result)

    def test_interactive_native_positive_and_negative(self):
        manager = '#include<iostream>\nint main(){std::cout<<3<<std::endl;int x;if(!(std::cin>>x))return 1;return x==6?0:1;}'
        package = {'execution': 'interactive', 'files': {'interactor.cpp': file(manager)},
                   'component': {'files': ['interactor.cpp'], 'language': 'CPP17', 'type': 'default'}}
        for expression, passed in (('2*int(input())', True), ('7', False)):
            with self.subTest(expression=expression):
                result = self.run_probe('print(%s, flush=True)' % expression, package)
                self.assertEqual(result['processed'], 1, result)
                self.assertEqual(result['cases'][0]['checker_result']['passed'], passed, result)

    def test_helper_cannot_read_private_host_file(self):
        result = self.run_probe('print(open("/etc/shadow").read())')
        self.assertFalse(result['cases'][0]['checker_result']['passed'], result)

    def test_two_process_manager_uses_native_routing(self):
        manager = '''#include<fstream>
int main(int argc,char**argv){for(int i=0;i<2;i++){
std::ofstream out(argv[2+2*i]);std::ifstream in(argv[1+2*i]);
out<<3<<std::endl;int x;if(!(in>>x)||x!=6)return 1;}return 0;}
'''
        package = {'execution': 'communication', 'num_processes': 2,
                   'files': {'manager.cpp': file(manager)},
                   'component': {'files': ['manager.cpp'], 'language': 'CPP17', 'type': 'default'}}
        for expression, passed in (('2*int(input())', True), ('7', False)):
            with self.subTest(expression=expression):
                result = self.run_probe('print(%s, flush=True)' % expression, package)
                self.assertEqual(result['processed'], 1, result)
                self.assertEqual(result['cases'][0]['checker_result']['passed'], passed, result)

    def test_manager_failure_is_not_contestant_rejection(self):
        package = {'execution': 'communication', 'num_processes': 2,
                   'files': {'manager.cpp': file('int main(){return 3;}')},
                   'component': {'files': ['manager.cpp'], 'language': 'CPP17', 'type': 'default'}}
        result = self.run_probe('print(int(input()))', package)
        self.assertEqual(result['cases'][0]['status'], 'PROFILE_EXECUTION_ERROR', result)

    def test_cms_manager_partial_score_and_crash(self):
        for source, points in (('#include<iostream>\nint main(){std::cout<<0.5;}', .5), ('int main(){return 3;}', None)):
            package = {'execution': 'communication', 'num_processes': 1,
                       'files': {'manager.cpp': file(source)},
                       'component': {'files': ['manager.cpp'], 'language': 'CPP17', 'type': 'cms'}}
            result = self.run_probe('print(0)', package)
            if points is None:
                self.assertEqual(result['cases'][0]['status'], 'PROFILE_EXECUTION_ERROR', result)
            else:
                self.assertEqual(result['cases'][0]['checker_result']['points'], points, result)

    def test_cancellation_stops_running_profile_without_acceptance_evidence(self):
        payload = {'source': {**file('while True: pass'), 'language': 'PY3'},
                   'package': {'execution': 'batch', 'files': {}},
                   'cases': [{'id': 'cancel', 'input': '', 'expected_output': ''}]}
        event = threading.Event()
        timer = threading.Timer(.3, event.set)
        timer.start()
        try:
            started = time.monotonic()
            result = run_review_job({'operation': 'profile_probe', 'snapshot_hash': 's', 'payload_hash': digest(payload), 'payload': payload}, event)
            self.assertTrue(result['cancelled'], result)
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(any(case.get('checker_result', {}).get('passed') for case in result['cases']))
        finally:
            timer.cancel()

    def test_java_off_heap_allocation_remains_process_bounded(self):
        source = '''import sun.misc.Unsafe;import java.lang.reflect.Field;
public class solution {public static void main(String[] args)throws Exception{
Field f=Unsafe.class.getDeclaredField("theUnsafe");f.setAccessible(true);
Unsafe u=(Unsafe)f.get(null);long p=u.allocateMemory(400L*1024*1024);
for(long i=0;i<400L*1024*1024;i+=4096)u.putByte(p+i,(byte)1);
System.out.println(6);}}
'''
        result = self.run_probe(source, language='JAVA')
        self.assertEqual(result['processed'], 1, result)
        self.assertFalse(result['cases'][0]['checker_result']['passed'], result)


if __name__ == '__main__':
    unittest.main()
