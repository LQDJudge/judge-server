"""Bounded Python helper execution against installed official ZIP inputs.

Only the authenticated bridge may request this operation. Helpers see one input
on stdin, never the problem directory, answer files or operator extra_fs grants.
This module deliberately leaves executor loading to normal judge initialization.
"""

import json
import math
import re
import time
import uuid
from zipfile import ZIP_DEFLATED, ZIP_STORED

from dmoj import executors
from dmoj.custom_invocation import MAX_SOURCE, run_process, sandbox_executor
from dmoj.error import CompileError, InternalError
from dmoj.problem import BatchedTestCase, Problem

PROTOCOL = 1
MAX_CASES = 1000
MAX_INPUT = 32 * 1024 * 1024
MAX_TOTAL_INPUT = 256 * 1024 * 1024
MAX_RESULT = 512 * 1024
MAX_JOB_SECONDS = 120
MAX_HELPER_ERRORS = 5
LIMITS = {'cpu_seconds': 2, 'wall_seconds': 6, 'memory_kib': 131072, 'output_bytes': 8192}


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {'problem_code', 'phase_id', 'attempt_token', 'helper_source'}:
        raise ValueError('Invalid statistics request fields')
    if (
        not isinstance(request['problem_code'], str)
        or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request['problem_code']) is None
    ):
        raise ValueError('Invalid problem code')
    phase = request['phase_id']
    if not isinstance(phase, int) or isinstance(phase, bool) or not 0 < phase < 2**63:
        raise ValueError('Invalid phase identity')
    token = request['attempt_token']
    if not isinstance(token, str) or len(token) != 36 or str(uuid.UUID(token)) != token:
        raise ValueError('Invalid attempt token')
    source = request['helper_source']
    if not isinstance(source, str) or not source or len(source) > MAX_SOURCE:
        raise ValueError('Invalid helper source')
    if len(source.encode('utf8')) > MAX_SOURCE:
        raise ValueError('Helper source exceeds limit')


def _reject_constant(value):
    raise ValueError('Nonfinite number')


def parse_metrics(output):
    """Reject non-JSON/nonfinite/deep or excessive structures before transport."""
    parsed = json.loads(output, parse_constant=_reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError('Expected JSON object')
    pending = [(parsed, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 2048 or depth > 16:
            raise ValueError('Metrics structure exceeds limit')
        if isinstance(value, dict):
            if any(len(key) > 128 for key in value):
                raise ValueError('Metric name exceeds limit')
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, str) and len(value) > 2048:
            raise ValueError('Metric text exceeds limit')
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite metric')
    return parsed


def _cases(problem):
    pending = list(reversed(problem.cases()))
    count = 0
    while pending:
        case = pending.pop()
        if isinstance(case, BatchedTestCase):
            pending.extend(reversed(case.batched_cases))
            continue
        count += 1
        if count > MAX_CASES:
            raise ValueError('Official test count exceeds statistics limit')
        yield case


def run_test_statistics(request, cancel_event=None):
    validate_request(request)
    result = {
        'protocol': PROTOCOL,
        'phase_id': request['phase_id'],
        'attempt_token': request['attempt_token'],
        'status': 'completed',
        'cases': [],
    }
    if cancel_event is not None and cancel_event.is_set():
        return {**result, 'status': 'cancelled'}
    if 'PY3' not in executors.executors:
        return {**result, 'status': 'infrastructure_error', 'error': 'Python sandbox is unavailable'}
    problem = executor = None
    start = time.monotonic()
    try:
        problem = Problem(request['problem_code'], 2, LIMITS['memory_kib'], {})
        archive = problem.problem_data.archive
        if archive is None:
            return {**result, 'status': 'unavailable', 'error': 'No official test ZIP'}
        cases = list(_cases(problem))
        result['total_cases'] = len(cases)
        if not cases:
            return {**result, 'status': 'unavailable', 'error': 'No official inputs'}
        # Do not run generators here or read inputs from arbitrary host paths.
        for case in cases:
            name = case.config['in']
            if not isinstance(name, str) or len(name) > 512:
                raise ValueError('Official input is not a ZIP member')
            member = archive.getinfo(name)
            if member.file_size > MAX_INPUT or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                raise ValueError('Official input exceeds statistics format/size limits')
            if member.flag_bits & 1:
                raise ValueError('Encrypted test ZIP is unsupported')
        executor = sandbox_executor('PY3', cancel_event)(
            'test_statistics', request['helper_source'].encode('utf8'), cached=False, compiler_time_limit=20, fs=[]
        )
        total_input = helper_errors = 0
        # Reserve space for envelope fields and a terminal error message.
        total_output = 1024
        for case in cases:
            if cancel_event is not None and cancel_event.is_set():
                result['status'] = 'cancelled'
                break
            if time.monotonic() - start >= MAX_JOB_SECONDS:
                result.update(status='unavailable', error='Statistics execution reached its time limit')
                break
            name = case.config['in']
            with archive.open(name) as stream:
                raw = stream.read(MAX_INPUT + 1)
            total_input += len(raw)
            if len(raw) > MAX_INPUT or total_input > MAX_TOTAL_INPUT:
                result.update(status='unavailable', error='Official inputs exceed statistics size limit')
                break
            captured = run_process(executor, raw, limits=LIMITS, cancel_event=cancel_event)
            record = {'name': name, 'case': case.position + 1, 'batch': case.batch or None}
            if captured.status == 'CANCELLED':
                result['status'] = 'cancelled'
                break
            try:
                if captured.status != 'EXIT_ZERO':
                    raise ValueError(captured.status)
                record.update(status='ok', metrics=parse_metrics(captured.stdout))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                # Do not return raw stderr/stdout: helpers can echo sensitive inputs.
                record.update(status='helper_error', error='Helper failed or returned invalid metrics')
                helper_errors += 1
            total_output += len(json.dumps(record, ensure_ascii=True).encode()) + 2
            if total_output > MAX_RESULT:
                result.update(status='unavailable', error='Statistics results exceed output limit')
                break
            result['cases'].append(record)
            if helper_errors >= MAX_HELPER_ERRORS:
                result.update(status='unavailable', error='Helper failed repeatedly')
                break
        if result['status'] == 'completed' and not any(row['status'] == 'ok' for row in result['cases']):
            result.update(status='unavailable', error='No usable helper metrics')
        return result
    except CompileError:
        return {**result, 'status': 'unavailable', 'error': 'Helper could not be compiled'}
    except InterruptedError:
        return {**result, 'status': 'cancelled'}
    except (ValueError, KeyError):
        return {**result, 'status': 'unavailable', 'error': 'Official ZIP inputs are unavailable or exceed limits'}
    except (OSError, InternalError, AssertionError):
        return {**result, 'status': 'infrastructure_error', 'error': 'Official test package could not be loaded'}
    finally:
        if executor is not None:
            executor.cleanup()
        if problem is not None and problem.problem_data.archive is not None:
            problem.problem_data.archive.close()
