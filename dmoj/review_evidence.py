"""Bounded review experiments using real judge executors, never host eval/exec."""
import base64
import hashlib
import json
import math
import os
import platform
import re
import shlex
import tempfile
from importlib import import_module

from dmoj import executors
from dmoj.checkers import (
    easy, identical, rstripped, standard, floats, floatsabs, floatsrel,
    sorted as sorted_checker, unordered, linecount, linematches,
    csv_accuracy, csv_rmse, csv_mae, csv_f1, csv_auc, csv_logloss,
)
from dmoj.contrib import contrib_modules
from dmoj.cptbox.filesystem_policies import ExactFile
from dmoj.custom_invocation import run_process, sandbox_executor
from dmoj.error import CompileError
from dmoj.result import CheckerResult

OPERATIONS = {'extractor', 'validator_probe', 'checker_probe', 'solution_probe', 'generator_probe', 'profile_probe'}
TIME = 3
MEMORY = 262144
OUTPUT = 65536
MAX_INPUT = 16 * 1024 * 1024
MAX_SOURCE = 256 * 1024
BUILTIN_CHECKERS = {'standard': standard.check, 'easy': easy.check,
                    'identical': identical.check, 'rstripped': rstripped.check,
                    'floats': floats.check, 'floatsabs': floatsabs.check, 'floatsrel': floatsrel.check,
                    'sorted': sorted_checker.check, 'unordered': unordered.check,
                    'linecount': linecount.check, 'linematches': linematches.check,
                    'csv_accuracy': csv_accuracy.check, 'csv_rmse': csv_rmse.check,
                    'csv_mae': csv_mae.check, 'csv_f1': csv_f1.check,
                    'csv_auc': csv_auc.check, 'csv_logloss': csv_logloss.check}


def valid_builtin_options(name, options):
    if name not in BUILTIN_CHECKERS or not isinstance(options, dict):
        return False
    allowed = {'precision', 'error_mode', 'has_header', 'id_column', 'label_column',
               'pretest_fraction', 'pretests_only', 'baseline', 'split_on', 'separator'}
    if options.keys() - allowed:
        return False
    for key, value in options.items():
        if key in ('has_header', 'pretests_only'):
            if not isinstance(value, bool):
                return False
        elif key == 'precision':
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 64:
                return False
        elif key in ('pretest_fraction', 'baseline'):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not -1e308 <= value <= 1e308 or not math.isfinite(value):
                return False
            if key == 'pretest_fraction' and not 0 < value <= 1:
                return False
        elif key == 'error_mode':
            if value not in ('absolute', 'relative', 'default'):
                return False
        elif not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > 128:
            return False
    return True


def case_bytes(case, key):
    encoded = key + '_b64'
    raw = base64.b64decode(case[encoded], validate=True) if encoded in case else case[key].encode()
    if len(raw) > MAX_INPUT:
        raise ValueError('Checker fixture exceeds limit')
    return raw


def run_builtin_checker(payload, result):
    # Only installed, trusted judge functions run here, just as in normal
    # grading. No author/model source, dynamic import, or kwargs are executed.
    # Only installed functions and bounded scalar options are accepted. Custom
    # code stays in its sandbox adapter, never a host module import.
    source = payload['source']
    name = payload.get('builtin_checker')
    options = payload.get('checker_args')
    if (not valid_builtin_options(name, options)
            or source['source'] or source.get('aux_sources')
            or len(json.dumps(payload, allow_nan=False).encode()) > 32 * 1024 * 1024):
        raise ValueError('Invalid built-in checker invocation')
    result['environment'].update(language='BUILTIN', checker=name,
                                 checker_adapter='native-builtin-v1', execution='trusted-judge-checker', checker_args=options)
    result['helper_limits'] = {'cases': 24, 'maximum_input_bytes': MAX_INPUT,
                               'maximum_packet_bytes': 32 * 1024 * 1024}
    result['limitations'] = ['Probe expectations and reference answers are model proposals, not independently proven correct answers.']
    ids = set()
    for case in payload['cases']:
        if not isinstance(case.get('id'), str) or case['id'] in ids:
            raise ValueError('Invalid checker fixture identity')
        ids.add(case['id'])
        raw = base64.b64decode(case['input_b64'], validate=True) if 'input_b64' in case else case['input'].encode()
        answer = case_bytes(case, 'expected_output')
        candidate = case_bytes(case, 'candidate_output')
        if (max(len(raw), len(answer), len(candidate)) > MAX_INPUT
                or (case.get('input_sha256') and hashlib.sha256(raw).hexdigest() != case['input_sha256'])):
            raise ValueError('Invalid checker fixture size/digest')
        record = {'id': case['id'], 'input_sha256': hashlib.sha256(raw).hexdigest(),
                  'input_size': len(raw), 'expected_claim': case.get('expected_claim'),
                  'expected_output_sha256': hashlib.sha256(answer).hexdigest(),
                  'candidate_output_sha256': hashlib.sha256(candidate).hexdigest(),
                  'invocation': {'protocol': 'native-builtin-v1', 'checker': name, 'point_value': 1}}
        try:
            observed = BUILTIN_CHECKERS[name](candidate, answer, point_value=1, **options)
            if isinstance(observed, bool):
                observed = CheckerResult(observed, 1 if observed else 0)
            if (not isinstance(observed, CheckerResult) or not isinstance(observed.passed, bool)
                    or not isinstance(observed.points, (int, float)) or not math.isfinite(observed.points)):
                raise ValueError('Invalid checker return value')
            if str(observed.feedback or '').startswith('checker error:'):
                raise ValueError('Native scorer reported an internal parsing/calculation error')
            record.update(status='EXIT_ZERO', returncode=0,
                          checker_result={'passed': observed.passed, 'points': observed.points})
        except Exception as error:
            record.update(status='CHECKER_PROTOCOL_ERROR', error=type(error).__name__)
            result['failed'] += 1
        result['cases'].append(record)
        result['processed'] += 1
    return result


def python_checker_adapter(source):
    # This entire adapter, including authored exec(), runs under the PY3 cptbox
    # executor. No authored checker module is imported into the judge daemon.
    return ('''import base64, contextlib, io, json, math, sys, types
class CheckerResult:
    def __init__(self, passed, points, feedback=None, extended_feedback=None):
        self.passed, self.points = passed, points
        self.feedback, self.extended_feedback = feedback, extended_feedback
package = types.ModuleType('dmoj'); package.__path__ = []
module = types.ModuleType('dmoj.result'); module.CheckerResult = CheckerResult
package.result = module
sys.modules['dmoj'] = package; sys.modules['dmoj.result'] = module
fixture = json.load(sys.stdin)
try:
    namespace = {'__name__': '_review_checker'}
    metadata = dict(fixture['checker_args'])
    metadata.update(judge_input=base64.b64decode(fixture['input']), point_value=1,
        case_position=fixture['position'], batch=fixture['batch'],
        submission_source=fixture['submission_source'],
        submission_language=fixture['submission_language'], binary_data=fixture['binary_data'],
        execution_time=0.0, problem_id='_review_evidence')
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(%r, '_review_checker.py', 'exec'), namespace)
        value = namespace['check'](base64.b64decode(fixture['candidate']),
            base64.b64decode(fixture['answer']), **metadata)
    if isinstance(value, bool):
        passed, points = value, int(value)
    elif isinstance(value, CheckerResult):
        passed, points = value.passed, value.points
    else:
        raise TypeError('Unsupported checker return type')
    if not isinstance(passed, bool) or not isinstance(points, (int, float)) or not math.isfinite(points):
        raise ValueError('Invalid checker result')
    print(json.dumps({'passed': passed, 'points': points}, allow_nan=False))
except Exception as error:
    print(json.dumps({'adapter_error': type(error).__name__}))
    raise SystemExit(2)
''' % source).encode()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def run_review_job(job, cancel_event=None):
    """AI expectations are retained as claims, not converted into pass/fail proof."""
    payload = job['payload']
    result = {'snapshot_hash': job['snapshot_hash'], 'payload_hash': job['payload_hash'],
              'cases': [], 'environment': {'platform': platform.platform(), 'protocol': 1},
              'processed': 0, 'failed': 0, 'unprocessed': [], 'limitations': []}
    result['helper_limits'] = {'cpu_seconds': TIME, 'wall_seconds': TIME * 3, 'memory_kib': MEMORY,
                               'stdout_bytes': OUTPUT, 'stderr_bytes': OUTPUT, 'cases': 24,
                               'maximum_input_bytes': MAX_INPUT, 'compiler_seconds': 20}
    result['limitations'].append('Helper resource failures are not proof that official problem limits or author artifacts are incorrect.')
    if digest(payload) != job['payload_hash']:
        raise ValueError('Review payload digest mismatch')
    operation = job['operation']
    source = payload['source']
    language = source['language']
    code = source['source'].encode()
    if (operation not in OPERATIONS or len(code) > MAX_SOURCE
            or hashlib.sha256(code).hexdigest() != source['sha256']
            or (operation != 'profile_probe' and language not in ('PY3', 'CPP11', 'CPP14', 'CPP17', 'CPP20', 'CPP23', 'BUILTIN'))
            or (operation == 'extractor' and language != 'PY3')):
        raise ValueError('Invalid review operation/source')
    cases = payload.get('cases', [])
    if not 0 < len(cases) <= 24:
        raise ValueError('Invalid review case count')
    if cancel_event is not None and cancel_event.is_set():
        result['cancelled'] = True
        result['unprocessed'] = [case['id'] for case in cases]
        return result
    result['source_sha256'] = source['sha256']
    if operation == 'profile_probe':
        if len(json.dumps(payload, allow_nan=False).encode()) > 32 * 1024 * 1024:
            raise ValueError('Profile packet exceeds limit')
        # Native executor classes capture env.runtime at import time. Load the
        # grader-backed adapter only after daemon configuration/initialization.
        return import_module('dmoj.review_profiles').run_profile_probe(payload, result, cancel_event)
    if language == 'BUILTIN':
        if operation != 'checker_probe':
            raise ValueError('Built-in invocation is only for checker probes')
        return run_builtin_checker(payload, result)
    if payload.get('builtin_checker') is not None:
        raise ValueError('Built-in checkers require the dedicated invocation mode')
    module_checker = operation == 'checker_probe' and language == 'PY3'
    checker_args = {}
    if module_checker:
        if not isinstance(payload.get('binary_data', False), bool):
            raise ValueError('Invalid binary checker metadata')
        checker_args = payload.get('checker_args') or {}
        if isinstance(checker_args, str):
            if len(checker_args.encode()) > MAX_SOURCE:
                raise ValueError('Python checker arguments exceed limit')
            checker_args = json.loads(checker_args)
        if not isinstance(checker_args, dict) or len(json.dumps(checker_args, allow_nan=False).encode()) > MAX_SOURCE:
            raise ValueError('Invalid Python checker arguments')
        code = python_checker_adapter(source['source'])
        result['limitations'].append('Probe point_value is normalized to 1; official testcase point values and timing-dependent checker behavior are not reproduced.')
        result['limitations'].append('Python checker adapter supplies bytes and standard scalar metadata; rich live case/result objects, LazyBytes and other judge imports are unavailable. Adapter errors are uncertainty, not deliberate output rejection.')
        result['environment']['checker_adapter'] = 'python-module-v1'
    result['environment']['language'] = language
    if language not in executors.executors:
        result['infrastructure_error'] = 'Requested executor unavailable'
        return result
    # Do not use compile_with_auxiliary_files: that helper grants all of /tmp.
    auxiliary_sources = {}
    aux_size = 0
    for name, auxiliary in source.get('aux_sources', {}).items():
        if (name != os.path.basename(name) or os.path.splitext(name)[1] not in ('.h', '.hpp', '.inc')
                or len(auxiliary_sources) >= 16):
            raise ValueError('Invalid auxiliary source name')
        raw_aux = auxiliary['source'].encode()
        aux_size += len(raw_aux)
        if (len(raw_aux) > MAX_SOURCE or aux_size > 512 * 1024
                or hashlib.sha256(raw_aux).hexdigest() != auxiliary['sha256']):
            raise ValueError('Invalid auxiliary source identity/size')
        auxiliary_sources[name] = raw_aux
    # Review code does not inherit operator-configured broad extra_fs grants or
    # compiler secrets. Compiler and runtime still use the normal cptbox tracer.
    ReviewExecutor = sandbox_executor(language, cancel_event)
    try:
        kwargs = {'aux_sources': auxiliary_sources, 'compiler_time_limit': 20, 'fs': []}
        if language != 'PY3':
            flags = payload.get('checker', {}).get('flags', [])
            if (not isinstance(flags, list) or len(flags) > 16
                    or not all(isinstance(flag, str) and re.fullmatch(r'-D[A-Za-z_][A-Za-z_0-9]*(?:=[A-Za-z_0-9]+)?', flag) for flag in flags)):
                raise ValueError('Unsupported review compiler flag')
            kwargs['flags'] = list(ReviewExecutor.flags) + flags
        executor = ReviewExecutor('_review_evidence', code, **kwargs)
    except CompileError as error:
        if cancel_event is not None and cancel_event.is_set():
            result['cancelled'] = True
        else:
            result['compile_error'] = str(error.message)[:OUTPUT]
        result['unprocessed'] = [case['id'] for case in cases]
        return result
    except InterruptedError:
        result['cancelled'] = True
        result['unprocessed'] = [case['id'] for case in cases]
        return result
    try:
        return _run_compiled_cases(executor, job, result, cancel_event, module_checker, checker_args)
    except InterruptedError:
        result['cancelled'] = True
        return result
    finally:
        executor.cleanup()


def _run_compiled_cases(executor, job, result, cancel_event, module_checker, checker_args):
    payload, operation = job['payload'], job['operation']
    cases = payload['cases']
    result['environment']['runtime'] = repr(executor.get_runtime_versions())[:1000]
    executor.nproc = 0
    for case in cases:
        if cancel_event is not None and cancel_event.is_set():
            result['cancelled'] = True
            break
        for key in ('batch', 'position'):
            # The running judge replaces builtins.int for bounded parsing.
            # JSON still produces native ints, so identity checks reject legal 0.
            if key in case and (not isinstance(case[key], int) or isinstance(case[key], bool)
                                or not 0 <= case[key] <= 1000000):
                raise ValueError('Invalid probe batch/case position')
        raw = base64.b64decode(case['input_b64'], validate=True) if 'input_b64' in case else case['input'].encode()
        if len(raw) > MAX_INPUT or (case.get('input_sha256') and hashlib.sha256(raw).hexdigest() != case['input_sha256']):
            raise ValueError('Invalid review input digest/size')
        record = {'id': case['id'], 'input_sha256': hashlib.sha256(raw).hexdigest(),
                  'expected_claim': case.get('expected_claim'), 'input_size': len(raw)}
        args = []
        extra_fs = []
        with tempfile.TemporaryDirectory(prefix='review-fixture-') as directory:
            if module_checker:
                record['invocation'] = {'protocol': 'python-module-v1', 'batch': case.get('batch', 0),
                                        'position': case.get('position', 0), 'point_value': 1,
                                        'checker_args': checker_args}
                answer = case_bytes(case, 'expected_output')
                candidate = case_bytes(case, 'candidate_output')
                if max(len(answer), len(candidate)) > MAX_INPUT:
                    raise ValueError('Checker fixture exceeds limit')
                raw = json.dumps({'input': base64.b64encode(raw).decode(),
                                  'checker_args': checker_args,
                                  'answer': base64.b64encode(answer).decode(),
                                  'candidate': base64.b64encode(candidate).decode(),
                                  'position': case.get('position', 0), 'batch': case.get('batch', 0),
                                  'submission_source': case.get('submission_source', ''),
                                  'binary_data': payload.get('binary_data', False),
                                  'submission_language': case.get('submission_language', 'CPP17')}).encode()
            elif operation == 'checker_probe':
                options = payload.get('checker', {})
                kind = options.get('type', 'default')
                if kind not in ('default', 'testlib', 'cms', 'lqdoj'):
                    raise ValueError('Unsupported checker protocol')
                paths = {}
                for name, data in (('input_file', raw), ('answer_file', case_bytes(case, 'expected_output')),
                                   ('output_file', case_bytes(case, 'candidate_output'))):
                    if len(data) > MAX_INPUT:
                        raise ValueError('Checker fixture exceeds limit')
                    path = os.path.join(directory, name)
                    with open(path, 'wb') as stream:
                        stream.write(data)
                    paths[name] = shlex.quote(path)
                    extra_fs.append(ExactFile(path))
                template = options.get('args_format_string') or contrib_modules[kind].ContribModule.get_checker_args_format_string()
                args = shlex.split(template.format(**paths))
                raw = b''
            elif operation == 'validator_probe':
                options = payload.get('validator', {})
                kind = options.get('type', 'default')
                if len(options.get('args_format_string') or '') > 4096:
                    raise ValueError('Validator arguments exceed limit')
                if kind not in ('default', 'testlib'):
                    raise ValueError('Unsupported validator protocol')
                template = options.get('args_format_string') or contrib_modules[kind].ContribModule.get_validator_args_format_string()
                args = shlex.split(template.format(batch_no=case.get('batch', 0), case_no=case.get('position', 0)))
                record['invocation'] = {'protocol': kind, 'batch': case.get('batch', 0),
                                        'position': case.get('position', 0), 'argv': args}
            elif operation == 'generator_probe':
                args = case.get('args', [])
                if not isinstance(args, list) or len(args) > 128 or not all(isinstance(arg, str) and len(arg) < 4096 for arg in args):
                    raise ValueError('Invalid generator arguments')
            captured = run_process(executor, raw, args=args, extra_fs=extra_fs, cancel_event=cancel_event,
                                   limits={'cpu_seconds': TIME, 'wall_seconds': TIME * 3,
                                           'memory_kib': MEMORY, 'output_bytes': OUTPUT})
            proc, output, error = captured.process, captured.stdout, captured.stderr
            record.update(captured.record())
            if captured.status == 'CANCELLED':
                result['cancelled'] = True
            if operation == 'extractor' and record['status'] == 'EXIT_ZERO':
                try:
                    parsed = json.loads(output, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite extractor metric')))
                    required = {'parse_status', 'input_consumption', 'logical_case_count', 'statistics', 'limitations'}
                    if not isinstance(parsed, dict) or not required <= parsed.keys():
                        raise ValueError('Extractor output contract missing fields')
                    if (parsed['parse_status'] not in ('ok', 'partial', 'error')
                            or not isinstance(parsed['statistics'], dict)
                            or not isinstance(parsed['input_consumption'], dict)
                            or not isinstance(parsed['logical_case_count'], int)
                            or isinstance(parsed['logical_case_count'], bool)
                            or parsed['logical_case_count'] < 0
                            or not isinstance(parsed['limitations'], list)
                            or not all(isinstance(item, str) for item in parsed['limitations'])):
                        raise ValueError('Invalid extractor output contract')
                    record['statistics'] = parsed
                    record['limitations'] = ['Input consumption is a helper claim, not independently verified parsing.']
                except (ValueError, TypeError, RecursionError):
                    record['status'] = 'INVALID_EXTRACTOR_OUTPUT'
            elif module_checker and record['status'] in ('EXIT_ZERO', 'EXIT_NONZERO'):
                try:
                    checked = json.loads(output)
                    if ('adapter_error' in checked or record['status'] != 'EXIT_ZERO'
                            or not isinstance(checked.get('passed'), bool)
                            or not isinstance(checked.get('points'), (int, float))):
                        raise ValueError('Python checker module adapter unavailable or invalid return')
                    record['checker_result'] = checked
                except (ValueError, TypeError, AttributeError):
                    record['status'] = 'CHECKER_PROTOCOL_ERROR'
                    record['protocol_error'] = 'Python module adapter could not establish a checker result; this is not rejection proof.'
            elif operation == 'checker_probe' and record['status'] in ('EXIT_ZERO', 'EXIT_NONZERO'):
                try:
                    checked = contrib_modules[kind].ContribModule.parse_return_code(
                        proc, executor, 1, TIME, MEMORY, feedback='', extended_feedback='',
                        name='review checker', stderr=error,
                        treat_checker_points_as_fraction=options.get('treat_checker_points_as_fraction', False),
                        treat_checker_points_as_percentage=options.get('treat_checker_points_as_percentage', False))
                    record['checker_result'] = {'passed': checked.passed, 'points': checked.points}
                except Exception as error:
                    record['status'] = 'CHECKER_PROTOCOL_ERROR'
                    record['protocol_error'] = str(error)[:OUTPUT]
            result['cases'].append(record)
            result['processed'] += 1
            if record['status'] not in ('EXIT_ZERO', 'EXIT_NONZERO'):
                result['failed'] += 1
    return result
