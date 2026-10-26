"""Constrained supplemental runs through the installed native grading classes.

No authored init.yml, host grader modules, shell commands, downloads or arbitrary
paths are accepted. Inputs are small model-proposed experiments, not a replacement
for prepared full-package submission evidence.
"""
import base64
import hashlib
import os
import re
import shutil
import tempfile
from types import SimpleNamespace

import yaml

from dmoj import executors
from dmoj.config import ConfigNode
from dmoj.cptbox.filesystem_policies import ExactFile
from dmoj.custom_invocation import sandbox_executor
from dmoj.error import CompileError, InternalError, OutputLimitExceeded
from dmoj.graders.base import SIGNATURE_GRADER_LANGUAGES
from dmoj.graders.bridged import BridgedInteractiveGrader
from dmoj.graders.communication import CommunicationGrader
from dmoj.graders.standard import StandardGrader
from dmoj.problem import Problem, ProblemConfig, ProblemDataManager
from dmoj.review_trace import ReviewTrace
from dmoj.result import CheckerResult
from dmoj.review_evidence import case_bytes, digest, run_review_job, valid_builtin_options

MAX_SOURCE = 262144
MAX_INPUT = 16 * 1024 * 1024
OUTPUT = 65536
RUNTIMES = frozenset(key for group in SIGNATURE_GRADER_LANGUAGES.values() for key in group)


def _filename(value):
    return (isinstance(value, str) and len(value) <= 128
            and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', value)
            and not value.startswith('_review_') and '..' not in value)


def validate_package(package):
    if not isinstance(package, dict) or package.get('execution') not in ('batch', 'interactive', 'communication'):
        raise ValueError('Unsupported profile execution')
    if package.keys() - {'execution', 'files', 'signature', 'component', 'num_processes', 'file_io', 'checker', 'binary'}:
        raise ValueError('Unknown profile package setting')
    files = package.get('files', {})
    if not isinstance(files, dict) or len(files) > 24:
        raise ValueError('Invalid profile file inventory')
    total = 0
    for name, metadata in files.items():
        if not _filename(name) or not isinstance(metadata, dict) or not isinstance(metadata.get('source'), str):
            raise ValueError('Invalid profile source file')
        raw = metadata['source'].encode()
        total += len(raw)
        if len(raw) > MAX_SOURCE or total > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != metadata.get('sha256'):
            raise ValueError('Invalid profile file identity/size')
    signature = package.get('signature')
    if signature is not None:
        if not isinstance(signature, dict) or signature.keys() - {'entry', 'header', 'family'}:
            raise ValueError('Invalid signature interface')
        if signature.get('family') not in ('c', 'java', 'python') or signature.get('entry') not in files:
            raise ValueError('Missing signature entry')
        if signature['family'] == 'c' and signature.get('header') not in files:
            raise ValueError('Missing signature header')
    component = package.get('component')
    if package['execution'] != 'batch':
        if not isinstance(component, dict) or component.keys() - {'files', 'language', 'type'}:
            raise ValueError('Invalid manager/interactor configuration')
        names = component.get('files')
        if (not isinstance(names, list) or not 0 < len(names) <= 8
                or any(name not in files for name in names)
                or component.get('language') not in ('C', 'C11', 'C23', 'CPP11', 'CPP14', 'CPP17', 'CPP20', 'CPP23')
                or component.get('type') not in ('default', 'lqdoj', 'testlib', 'cms')):
            raise ValueError('Unsupported manager/interactor invocation')
    count = package.get('num_processes', 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 16:
        raise ValueError('Invalid profile process count')
    checker = package.get('checker')
    if checker is not None:
        if not isinstance(checker, dict):
            raise ValueError('Invalid profile checker')
        if checker.get('kind') == 'builtin':
            if checker.keys() - {'kind', 'name', 'args'} or not valid_builtin_options(checker.get('name'), checker.get('args')):
                raise ValueError('Invalid profile built-in checker')
        elif (checker.get('kind') != 'source' or checker.keys() - {'kind', 'filename', 'language', 'type', 'args'}
              or checker.get('filename') not in files or checker.get('language') not in ('PY3', 'CPP11', 'CPP14', 'CPP17', 'CPP20', 'CPP23')
              or checker.get('type') not in ('default', 'testlib', 'cms', 'lqdoj') or not isinstance(checker.get('args'), dict)):
            raise ValueError('Invalid profile source checker')
    if 'binary' in package and not isinstance(package['binary'], bool):
        raise ValueError('Invalid binary data flag')
    file_io = package.get('file_io', {})
    if not isinstance(file_io, dict) or file_io.keys() - {'input', 'output'} or any(not _filename(name) for name in file_io.values()):
        raise ValueError('Invalid named I/O configuration')
    return package


def _executor(runtime, cancel_event=None):
    if runtime not in RUNTIMES or runtime not in executors.executors:
        raise InternalError('Requested review runtime unavailable')
    return sandbox_executor(runtime, cancel_event)


class _ReviewCompilation:
    def _generate_binary(self):
        package = self.problem.review_package
        signature = package.get('signature')
        auxiliary = {name: item['source'].encode() for name, item in package['files'].items()
                     if name.endswith(('.h', '.hpp', '.inc'))}
        entry = self.source
        kwargs = {'aux_sources': auxiliary, 'compiler_time_limit': 20, 'fs': [], 'cached': False}
        if signature:
            family = signature['family']
            native_family = 'cpp' if family == 'c' else family
            if self.language not in SIGNATURE_GRADER_LANGUAGES[native_family]:
                raise ValueError('Contestant runtime does not match interface family')
            entry = package['files'][signature['entry']]['source'].encode()
            if family == 'c':
                prefix = '#include "%s"\n#define main _review_contestant_main\n' % signature['header']
                auxiliary[self.problem.id + '_submission'] = prefix.encode() + self.source
                kwargs['defines'] = ['-DSIGNATURE_GRADER']
            elif family == 'java':
                auxiliary[self.problem.id + '_submission'] = self.source
            else:
                auxiliary['_submission'] = self.source
        return _executor(self.language, self.problem.review_cancel_event)(self.problem.id, entry, **kwargs)

    def _compile_component(self):
        package = self.problem.review_package
        component = package['component']
        auxiliary = {name: item['source'].encode() for name, item in package['files'].items()
                     if name in component['files'] or name.endswith(('.h', '.hpp', '.inc'))}
        base = _executor(component['language'], self.problem.review_cancel_event)

        class ComponentExecutor(base):
            def get_fs(self):
                # CommunicationGrader adds exact FIFO entries to write_fs at
                # launch. Grant matching reads without its normal /tmp grant.
                return super().get_fs() + self.write_fs

            def launch(binary, *args, **kwargs):
                if hasattr(self, '_review_component_args'):
                    args = self._review_component_args(args)
                    binary.write_fs = binary.write_fs + [ExactFile(path) for path in self.review_trace.fifo_paths]
                return super().launch(*args, **kwargs)

        return ComponentExecutor(
            '_review_component', None, aux_sources=auxiliary,
            compiler_time_limit=20, fs=[], cached=False, unbuffered=True,
        )


class _Standard(_ReviewCompilation, StandardGrader):
    pass


class _Interactive(_ReviewCompilation, BridgedInteractiveGrader):
    def _generate_interactor_binary(self):
        return self._compile_component()

    def _launch_process(self, case, input_file=None):
        super()._launch_process(case, input_file)
        self.review_trace = ReviewTrace()
        original_in, original_out = self._interactor_stdin_pipe, self._interactor_stdout_pipe
        self._interactor_stdin_pipe, relay_out = os.pipe()
        relay_in, self._interactor_stdout_pipe = os.pipe()
        self.review_trace.relay(original_in, relay_out, 'contestant -> interactor')
        self.review_trace.relay(relay_in, original_out, 'interactor -> contestant')


class _Communication(_ReviewCompilation, CommunicationGrader):
    def _generate_manager_binary(self):
        return self._compile_component()

    def _review_component_args(self, args):
        self.review_trace = ReviewTrace()
        return self.review_trace.communication_args(args)


class _ProfileProblem(Problem):
    def load_checker(self, name):
        if name != '_review_author_checker.py':
            return super().load_checker(name)
        package = self.review_package
        checker = package['checker']

        def check(candidate, answer, **metadata):
            source = {**package['files'][checker['filename']], 'language': checker['language']}
            source['aux_sources'] = {filename: item for filename, item in package['files'].items() if filename.endswith(('.h', '.hpp', '.inc'))}
            options = {'type': checker['type']}
            for key in ('args_format_string', 'treat_checker_points_as_fraction', 'treat_checker_points_as_percentage', 'flags'):
                if key in checker['args']:
                    options[key] = checker['args'][key]
            if checker['type'] == 'testlib':
                options.pop('treat_checker_points_as_percentage', None)
                options['treat_checker_points_as_fraction'] = True
            payload = {'source': source, 'checker': options, 'checker_args': checker['args'],
                       'binary_data': bool(package.get('binary')),
                       'cases': [{'id': 'final-check', 'input_b64': base64.b64encode(bytes(metadata['judge_input'])).decode(),
                                  'expected_output_b64': base64.b64encode(bytes(answer)).decode(),
                                  'candidate_output_b64': base64.b64encode(bytes(candidate)).decode(),
                                  'expected_output': '', 'candidate_output': '',
                                  'batch': metadata.get('batch') or 0, 'position': metadata.get('case_position', 0),
                                  'submission_source': bytes(metadata.get('submission_source') or b'').decode('utf8', 'replace'),
                                  'submission_language': metadata.get('submission_language', '')}]}
            observed = run_review_job({'operation': 'checker_probe', 'snapshot_hash': '_review_profile',
                                       'payload_hash': digest(payload), 'payload': payload}, self.review_cancel_event)
            records = observed.get('cases', [])
            if (len(records) != 1 or records[0].get('status') not in ('EXIT_ZERO', 'EXIT_NONZERO')
                    or not records[0].get('checker_result')):
                raise InternalError('Frozen final checker could not establish a result in the constrained adapter')
            result = records[0]['checker_result']
            return CheckerResult(result['passed'], result['points'])
        return SimpleNamespace(check=check)


def _cleanup(grader):
    if grader is None:
        return
    processes = list(getattr(grader, '_user_procs', ()))
    processes += [getattr(grader, key, None) for key in ('_current_proc', '_interactor', '_manager_proc')]
    for process in processes:
        if process is not None:
            if process.returncode is None:
                process.kill()
            process.wait()
            for key in ('stdin', 'stdout', 'stderr'):
                stream = getattr(process, key, None)
                if stream is not None and not stream.closed:
                    stream.close()
    for directory in getattr(grader, '_fifo_dir', ()):
        shutil.rmtree(directory, ignore_errors=True)
    trace = getattr(grader, 'review_trace', None)
    if trace is not None:
        trace.close()


def run_profile_probe(payload, result, cancel_event=None):
    package = validate_package(payload.get('package'))
    source = payload['source']
    if source.get('language') not in RUNTIMES:
        raise ValueError('Unsupported profile contestant language')
    cases = payload['cases']
    grader = None
    with tempfile.TemporaryDirectory(prefix='review-profile-') as directory:
        for name, item in package['files'].items():
            with open(os.path.join(directory, name), 'wb') as stream:
                stream.write(item['source'].encode())
        definitions = []
        ids = set()
        for index, case in enumerate(cases):
            if not isinstance(case.get('id'), str) or case['id'] in ids:
                raise ValueError('Invalid profile case identity')
            ids.add(case['id'])
            raw = base64.b64decode(case['input_b64'], validate=True) if 'input_b64' in case else case['input'].encode()
            answer = case_bytes(case, 'expected_output')
            if (max(len(raw), len(answer)) > MAX_INPUT
                    or case.get('input_sha256') and hashlib.sha256(raw).hexdigest() != case['input_sha256']):
                raise ValueError('Invalid profile input identity/size')
            for suffix, data in (('in', raw), ('out', answer)):
                with open(os.path.join(directory, '_review_%s.%s' % (index, suffix)), 'wb') as stream:
                    stream.write(data)
            definitions.append({'in': '_review_%s.in' % index, 'out': '_review_%s.out' % index, 'points': 1})
        config = {'test_cases': definitions, 'checker': 'standard', 'output_limit_length': OUTPUT,
                  'wall_time_factor': 3, 'file_io': package.get('file_io', {}), 'binary_data': bool(package.get('binary'))}
        checker = package.get('checker', {})
        if checker.get('kind') == 'builtin':
            config['checker'] = {'name': checker['name'], 'args': checker['args']} if checker['args'] else checker['name']
        elif checker.get('kind') == 'source':
            config['checker'] = '_review_author_checker.py'
        execution = package['execution']
        component = package.get('component', {})
        if execution == 'interactive':
            config.update(interactive={'files': component['files'], 'type': component['type'],
                                       'memory_limit': 262144, 'preprocessing_time': 1}, unbuffered=True)
        elif execution == 'communication':
            config['communication'] = {'manager': {'files': component['files'], 'memory_limit': 262144},
                                       'num_processes': package.get('num_processes', 1), 'type': component['type']}
        with open(os.path.join(directory, 'init.yml'), 'w') as stream:
            yaml.safe_dump(config, stream)
        # Construct only from the generated whitelist above. No author/model
        # init.yml reaches ProblemConfig or dynamic native checker loading.
        problem = _ProfileProblem.__new__(_ProfileProblem)
        problem.id, problem.root_dir, problem.storage_namespace = '_review_evidence', directory, None
        problem.time_limit = 3
        problem.memory_limit = 262144 // package.get('num_processes', 1)
        problem.run_pretests_only = bool(checker.get('args', {}).get('pretests_only'))
        problem._batch_counter = problem._testcase_counter = 0
        problem.meta, problem._checkers = ConfigNode({}), {}
        problem.problem_data = ProblemDataManager(directory)
        problem.config = ProblemConfig(problem.problem_data)
        problem.review_package = package
        problem.review_cancel_event = cancel_event
        grader_class = {'batch': _Standard, 'interactive': _Interactive, 'communication': _Communication}[execution]
        result['environment'].update(language=source['language'], profile=execution, adapter='native-profile-v1')
        result['environment']['checker_args'] = checker.get('args', {})
        result['limitations'].extend([
            'Bounded supplemental cases use a normalized point value of 1 and constrained helper resource limits.',
            'The selected frozen final checker is applied to proposed reference/candidate data with normalized point value 1; proposed oracle validity still needs independent review.',
            'Protocol traces are bounded byte previews; verdicts and sampled traces do not prove unobserved interaction paths.',
        ])
        try:
            grader = grader_class(SimpleNamespace(), problem, source['language'], source['source'].encode())
            result['environment']['runtime'] = repr(executors.executors[source['language']].Executor.get_runtime_versions())[:1000]
            if package.get('component'):
                key = package['component']['language']
                result['environment']['component_runtime'] = key
                result['environment']['component_runtime_version'] = repr(executors.executors[key].Executor.get_runtime_versions())[:1000]
            for proposal, case in zip(cases, problem.cases()):
                if cancel_event is not None and cancel_event.is_set():
                    result['cancelled'] = True
                    break
                input_hash = hashlib.sha256(case.input_data()).hexdigest()
                try:
                    observed = grader.grade(case)
                    record = {'id': proposal['id'], 'input_sha256': input_hash,
                              'expected_claim': proposal.get('expected_claim'),
                              'status': 'EXIT_ZERO', 'verdicts': observed.readable_codes(),
                              'checker_result': {'passed': observed.result_flag == 0, 'points': observed.points},
                              'time': observed.execution_time, 'memory': observed.max_memory,
                              'stdout': bytes(observed.proc_output or b'')[:OUTPUT].decode('utf8', 'replace')}
                    record['component_stderr'] = bytes(getattr(grader, '_manager_stderr', getattr(grader, '_interactor_stderr', b'')) or b'')[:OUTPUT].decode('utf8', 'replace')
                    record['participation'] = [not value for value in getattr(grader, '_user_abandoned', ())]
                    if any(code not in ('AC', 'WA') for code in record['verdicts']):
                        record['status'] = 'CONTESTANT_RUNTIME_FAILURE'
                    trace = getattr(grader, 'review_trace', None)
                    if trace is not None:
                        record['trace'] = trace.summary()
                        if trace.limit_exceeded:
                            record['status'] = 'TRACE_LIMIT'
                    # Result.AC is zero; WA with partial points is not full acceptance.
                    result['cases'].append(record)
                    result['processed'] += 1
                except (InternalError, OutputLimitExceeded) as error:
                    result['cases'].append({'id': proposal['id'], 'status': 'PROFILE_EXECUTION_ERROR',
                                            'error': str(error)[:OUTPUT]})
                    result['failed'] += 1
                finally:
                    case.free_data()
                    _cleanup(grader)
        except CompileError as error:
            result['compile_error'] = str(error.message)[:OUTPUT]
            result['unprocessed'] = [case['id'] for case in cases]
        except InternalError as error:
            result['infrastructure_error'] = str(error)[:OUTPUT]
        except InterruptedError:
            result['cancelled'] = True
        finally:
            _cleanup(grader)
            if grader is not None:
                binaries = [getattr(grader, key, None) for key in ('binary', 'interactor_binary', '_manager_binary')]
                binaries += list(getattr(grader, '_user_binaries', ()))
                seen = set()
                for binary in binaries:
                    if binary is not None and id(binary) not in seen:
                        seen.add(id(binary))
                        binary.cleanup()
        if cancel_event is not None and cancel_event.is_set():
            result['cancelled'] = True
    return result
