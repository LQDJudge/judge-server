"""Judge-only single-file invocation API and shared constrained execution primitives.

This module must not import executor implementations before judge initialization.
Only the authenticated bridge can submit packets; this is not an HTTP service.
"""

import base64
import hashlib
import math
import os
import re
import subprocess
import threading
from importlib import import_module
from typing import NamedTuple

from dmoj import executors
from dmoj.error import CompileError, InternalError, OutputLimitExceeded
from dmoj.utils.communicate import safe_communicate

PROTOCOL = 1
MAX_SOURCE = 256 * 1024
MAX_INPUT = 1024 * 1024
OUTPUT = 65536
RUNTIMES = frozenset(('C', 'C11', 'C23', 'CPP03', 'CPP11', 'CPP14', 'CPP17', 'CPP20', 'CPP23', 'PY3', 'JAVA', 'JAVA8'))
DEFAULT_LIMITS = {'cpu_seconds': 3, 'wall_seconds': 9, 'memory_kib': 262144, 'output_bytes': OUTPUT}
COMPILER_SECONDS = 20


def valid_request_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) is not None


def validate_request(request):
    if not isinstance(request, dict) or request.keys() - {'language', 'source', 'stdin', 'stdin_b64', 'limits'}:
        raise ValueError('Unsupported invocation fields')
    language, source = request.get('language'), request.get('source')
    if not isinstance(language, str) or language not in RUNTIMES:
        raise ValueError('Unsupported invocation language')
    if not isinstance(source, str) or not source or len(source) > MAX_SOURCE:
        raise ValueError('Invalid invocation source')
    try:
        code = source.encode('utf8')
        if len(code) > MAX_SOURCE:
            raise ValueError('Invocation source exceeds limit')
        if 'stdin' in request and 'stdin_b64' in request:
            raise ValueError('Provide stdin or stdin_b64, not both')
        if 'stdin_b64' in request:
            encoded = request['stdin_b64']
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_INPUT + 2) // 3):
                raise ValueError('Invalid invocation input')
            raw = base64.b64decode(encoded, validate=True)
        else:
            text = request.get('stdin', '')
            if not isinstance(text, str) or len(text) > MAX_INPUT:
                raise ValueError('Invalid invocation input')
            raw = text.encode('utf8')
    except (UnicodeError, TypeError) as error:
        raise ValueError('Invalid invocation encoding') from error
    if len(raw) > MAX_INPUT:
        raise ValueError('Invocation input exceeds limit')
    requested = request.get('limits', {})
    if not isinstance(requested, dict) or requested.keys() - DEFAULT_LIMITS.keys():
        raise ValueError('Unsupported invocation limits')
    limits = {**DEFAULT_LIMITS, **requested}
    for key, value in limits.items():
        lower = 0.05 if key in ('cpu_seconds', 'wall_seconds') else 16384 if key == 'memory_kib' else 1
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not lower <= value <= DEFAULT_LIMITS[key]
            or not math.isfinite(value)
            or key in ('memory_kib', 'output_bytes')
            and not isinstance(value, int)
        ):
            raise ValueError('Invalid invocation limit: ' + key)
    if limits['wall_seconds'] < limits['cpu_seconds']:
        raise ValueError('Wall limit must cover the CPU limit')
    return language, code, raw, limits


def _watch_cancellation(process, cancel_event):
    if cancel_event is None:
        return

    def cancel_if_requested():
        while process.returncode is None:
            if cancel_event.wait(0.05):
                if process.returncode is None:
                    process.kill()
                return

    threading.Thread(target=cancel_if_requested, daemon=True).start()


class _BoundedProcess:
    def __init__(self, process):
        self._process = process

    def __getattr__(self, name):
        return getattr(self._process, name)

    def communicate(self, *args, **kwargs):
        kwargs['outlimit'] = min(kwargs.get('outlimit', OUTPUT), OUTPUT)
        kwargs['errlimit'] = min(kwargs.get('errlimit', OUTPUT), OUTPUT)
        return self._process.communicate(*args, **kwargs)


def sandbox_executor(runtime, cancel_event=None):
    """Internal primitive: callers select registered runtimes, never host commands.

    Review adapters may supply frozen auxiliary files/argv; the public invocation
    schema cannot. Runtime/compile environment restrictions are shared by both.
    """
    if runtime not in executors.executors:
        raise InternalError('Requested sandbox runtime unavailable')
    module = executors.executors[runtime]
    base_executor = import_module('dmoj.executors.base_executor').BaseExecutor
    java = runtime in ('JAVA', 'JAVA8', 'JAVA9', 'JAVA10', 'JAVA11', 'JAVA15', 'JAVA17')

    class InvocationExecutor(module.Executor):
        address_grace = 2097152 if java else module.Executor.address_grace
        data_grace = 524288 if java else module.Executor.data_grace

        def _load_extra_fs(self):
            return []

        def get_compile_env(self):
            return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}

        def get_compile_popen_kwargs(self):
            return {
                **super().get_compile_popen_kwargs(),
                'memory': 3145728 if java else 1048576,
                'wall_time': COMPILER_SECONDS,
            }

        def create_compile_process(self, args):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError('Sandbox execution cancelled')
            if java:
                args = [
                    args[0],
                    '-J-Xmx256m',
                    '-J-XX:CompressedClassSpaceSize=64m',
                    '-J-XX:ReservedCodeCacheSize=32m',
                    '-J-XX:MaxMetaspaceSize=128m',
                    '-J-XX:ActiveProcessorCount=1',
                    *args[1:],
                ]
            process = super().create_compile_process(args)
            _watch_cancellation(process, cancel_event)
            return process

        def get_compile_output(self, process):
            # Independent of operator-configured submission diagnostic limits.
            # An overproducing compiler must not keep a pipe or process alive.
            truncated = False
            try:
                output = safe_communicate(process, None, outlimit=OUTPUT, errlimit=OUTPUT)[self.compile_output_index]
            except OutputLimitExceeded:
                truncated = True
                output = b'Compiler output exceeded the 65536-byte limit.'
                process.kill()
            except BaseException:
                process.kill()
                raise
            finally:
                process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            if truncated or self.is_failed_compile(process):
                if process.is_tle:
                    output = b'Compiler timed out.'
                self.handle_compile_error(output)
            return output

        def get_nproc(self):
            value = super().get_nproc()
            if value >= 0:
                return value
            # RLIMIT_NPROC counts all tasks owned by the real UID. JVMs need a
            # bounded allowance above existing tasks, not an unbounded exemption.
            current, uid = 0, os.getuid()
            for name in os.listdir('/proc'):
                if not name.isdigit():
                    continue
                try:
                    with open('/proc/%s/status' % name) as stream:
                        status = stream.read(8192)
                    owner = re.search(r'^Uid:\s+(\d+)', status, re.M)
                    threads = re.search(r'^Threads:\s+(\d+)', status, re.M)
                    if owner and threads and int(owner.group(1)) == uid:
                        current += int(threads.group(1))
                except (OSError, ValueError):
                    continue
            return min(current + 64, 8192)

        def get_cmdline(self, **kwargs):
            arguments = super().get_cmdline(**kwargs)
            if java:
                arguments = ['-Xss2m' if value == '-Xss128m' else value for value in arguments]
                arguments[1:1] = [
                    '-XX:CompressedClassSpaceSize=64m',
                    '-XX:ReservedCodeCacheSize=32m',
                    '-XX:MaxMetaspaceSize=128m',
                    '-XX:MaxDirectMemorySize=64m',
                    '-XX:ActiveProcessorCount=1',
                ]
            return arguments

        def launch(self, *args, **kwargs):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError('Sandbox execution cancelled')
            if java:
                kwargs['orig_memory'] = min(65536, kwargs['memory'] // 2)
                process = base_executor.launch(self, *args, **kwargs)
            else:
                process = super().launch(*args, **kwargs)
            _watch_cancellation(process, cancel_event)
            return _BoundedProcess(process)

    return InvocationExecutor


class CapturedExecution(NamedTuple):
    process: object
    stdout: bytes
    stderr: bytes
    status: str

    def record(self):
        return {
            'status': self.status,
            'returncode': self.process.returncode,
            'time': self.process.execution_time,
            'memory': self.process.max_memory,
            'stdout': self.stdout.decode('utf8', 'replace'),
            'stderr': self.stderr.decode('utf8', 'replace'),
            'output_sha256': hashlib.sha256(self.stdout).hexdigest(),
        }


def run_process(executor, raw, *, limits, args=(), extra_fs=(), cancel_event=None):
    """Internal trusted adapter, with bounded pipes and deterministic cleanup."""
    process = executor.launch(
        *args,
        time=limits['cpu_seconds'],
        memory=limits['memory_kib'],
        wall_time=limits['wall_seconds'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        nproc=1,
        extra_fs=list(extra_fs)
    )
    output, error, status = b'', b'', None
    try:
        output, error = process.communicate(raw, outlimit=limits['output_bytes'], errlimit=limits['output_bytes'])
    except OutputLimitExceeded:
        status = 'OLE'
        process.kill()
    except BaseException:
        process.kill()
        raise
    finally:
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    if cancel_event is not None and cancel_event.is_set():
        status = 'CANCELLED'
    if status is None:
        status = (
            'TLE'
            if process.is_tle
            else (
                'MLE'
                if process.is_mle
                else (
                    'PROTECTION'
                    if process.protection_fault
                    else (
                        'EXIT_ZERO'
                        if process.returncode == 0
                        else 'SIGNALLED' if process.returncode < 0 else 'EXIT_NONZERO'
                    )
                )
            )
        )
    return CapturedExecution(process, output, error, status)


def run_custom_invocation(request, cancel_event=None):
    """In-process API. Invalid schemas raise ValueError before compilation."""
    language, code, raw, limits = validate_request(request)
    result = {'protocol': PROTOCOL, 'language': language, 'limits': limits, 'compiler_seconds': COMPILER_SECONDS}
    if cancel_event is not None and cancel_event.is_set():
        return {**result, 'status': 'CANCELLED'}
    if language not in executors.executors:
        return {**result, 'status': 'UNAVAILABLE'}
    executor = None
    try:
        executor = sandbox_executor(language, cancel_event)(
            'custom_invocation',
            code,
            compiler_time_limit=COMPILER_SECONDS,
            fs=[],
            cached=False,
        )
        captured = run_process(executor, raw, limits=limits, cancel_event=cancel_event)
        result.update(captured.record())
        result.update(
            stdout_b64=base64.b64encode(captured.stdout).decode('ascii'),
            stderr_b64=base64.b64encode(captured.stderr).decode('ascii'),
            output_truncated=captured.status == 'OLE',
        )
    except CompileError as error:
        result.update(
            status='CANCELLED' if cancel_event is not None and cancel_event.is_set() else 'COMPILE_ERROR',
            compile_error=str(error.message).encode('utf8')[:OUTPUT].decode('utf8', 'replace'),
        )
    except InterruptedError:
        result['status'] = 'CANCELLED'
    finally:
        if executor is not None:
            executor.cleanup()
    return result
