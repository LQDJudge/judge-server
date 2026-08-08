import os
import random
import shlex
import subprocess
import threading
from typing import TYPE_CHECKING

from dmoj.checkers import CheckerOutput
from dmoj.config import ConfigNode
from dmoj.contrib import contrib_modules
from dmoj.cptbox.filesystem_policies import ExactFile
from dmoj.error import CompileError, InternalError
from dmoj.executors.base_executor import BaseExecutor
from dmoj.graders.standard import StandardGrader
from dmoj.judgeenv import env, get_problem_root
from dmoj.problem import Problem, TestCase
from dmoj.result import CheckerResult, Result
from dmoj.utils.helper_files import compile_with_auxiliary_files, mktemp
from dmoj.utils.interactive_feedback import (
    InteractiveTranscript,
    attach_interaction_transcript,
    proxy_interaction_stream,
)
from dmoj.utils.unicode import utf8text

if TYPE_CHECKING:
    from dmoj.judge import JudgeWorker


class BridgedInteractiveGrader(StandardGrader):
    handler_data: ConfigNode
    interactor_binary: BaseExecutor
    contrib_type: str

    def __init__(self, judge: 'JudgeWorker', problem: Problem, language: str, source: bytes) -> None:
        super().__init__(judge, problem, language, source)
        self.handler_data = self.problem.config.interactive

        try:
            self.interactor_binary = self._generate_interactor_binary()
        except CompileError as compilation_error:
            # Rethrow as IE to differentiate from the user's submission failing to compile.
            raise InternalError('interactor failed compiling') from compilation_error

        self.contrib_type = self.handler_data.get('type', 'default')
        if self.contrib_type not in contrib_modules:
            raise InternalError(f'{self.contrib_type} is not a valid contrib module')

    def check_result(self, case: TestCase, result: Result) -> CheckerOutput:
        # We parse the return code first in case the grader crashed, so it can raise the IE.
        # Usually a grader crash will result in IR/RTE/TLE,
        # so checking submission return code first will cover up the grader fail.
        parsed_result = contrib_modules[self.contrib_type].ContribModule.parse_return_code(
            self._interactor,
            self.interactor_binary,
            case.points,
            self._interactor_time_limit,
            self._interactor_memory_limit,
            feedback='',
            extended_feedback=utf8text(self._interactor_stderr, 'replace'),
            name='interactor',
            stderr=self._interactor_stderr,
        )
        attach_interaction_transcript(
            parsed_result,
            self._interaction_transcript.render(),
            case.output_prefix_length,
        )

        if result.result_flag:
            return False

        if parsed_result.passed and (case.config['checker'] or 'standard') != 'standard':
            # Run the custom checker iff a custom checker is specified and the interactor returns a passed verdict.
            check = super().check_result(case, result)
            if isinstance(check, CheckerResult):
                attach_interaction_transcript(
                    check,
                    self._interaction_transcript.render(),
                    case.output_prefix_length,
                )
            return check

        return parsed_result

    def _launch_process(self, case: TestCase, input_file=None) -> None:
        self._interaction_transcript = InteractiveTranscript(max_length=case.output_prefix_length)
        self._submission_stdout_read, submission_stdout_write = os.pipe()
        submission_stdin_read, self._submission_stdin_write = os.pipe()
        self._current_proc = self.binary.launch(
            time=self.problem.time_limit,
            memory=self.problem.memory_limit,
            symlinks=case.config.symlinks,
            stdin=submission_stdin_read,
            stdout=submission_stdout_write,
            stderr=subprocess.PIPE,
            wall_time=case.config.wall_time_factor * self.problem.time_limit,
        )
        os.close(submission_stdin_read)
        os.close(submission_stdout_write)

    def _interact_with_process(self, case: TestCase, result: Result) -> bytes:
        assert self._current_proc is not None
        assert self._current_proc.stderr is not None

        judge_output = case.output_data()
        # Give TL + 2s by default, so we do not race (and incorrectly throw IE) if submission gets TLE
        self._interactor_time_limit = (self.handler_data.preprocessing_time or 2) + self.problem.time_limit
        self._interactor_memory_limit = self.handler_data.memory_limit or env['generator_memory_limit']
        args_format_string = (
            self.handler_data.args_format_string
            or contrib_modules[self.contrib_type].ContribModule.get_interactor_args_format_string()
        )

        with mktemp(judge_output) as answer_file:
            input_path = case.input_data_io().to_path()
            interactor_stdin_read, interactor_stdin_write = os.pipe()
            interactor_stdout_read, interactor_stdout_write = os.pipe()

            # Take advantage of File IO to support log file (required by testlib).
            # Collision is not a concern here because the log file, which is just a symlink to /dev/fd/4,
            # is created inside a temporary directory.
            interactor_log_file = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789_', k=8))

            interactor_args = shlex.split(
                args_format_string.format(
                    input_file=shlex.quote(input_path),
                    output_file=shlex.quote(interactor_log_file),
                    answer_file=shlex.quote(answer_file.name),
                )
            )
            self._interactor = self.interactor_binary.launch(
                *interactor_args,
                time=self._interactor_time_limit,
                memory=self._interactor_memory_limit,
                stdin=interactor_stdin_read,
                stdout=interactor_stdout_write,
                stderr=subprocess.PIPE,
                file_io=ConfigNode({'output': interactor_log_file}),
                extra_fs=[ExactFile(input_path)],
            )

            os.close(interactor_stdin_read)
            os.close(interactor_stdout_write)
            interaction_proxy_threads = [
                threading.Thread(
                    target=proxy_interaction_stream,
                    args=(
                        self._submission_stdout_read,
                        interactor_stdin_write,
                        'USER',
                        self._interaction_transcript,
                    ),
                ),
                threading.Thread(
                    target=proxy_interaction_stream,
                    args=(
                        interactor_stdout_read,
                        self._submission_stdin_write,
                        'JUDGE',
                        self._interaction_transcript,
                    ),
                ),
            ]
            for thread in interaction_proxy_threads:
                thread.start()

            result.proc_output, self._interactor_stderr = self._interactor.communicate()
            for thread in interaction_proxy_threads:
                thread.join()
            self._current_proc.wait()
            result.extended_feedback = self._interaction_transcript.render()

            return self._current_proc.stderr.read()

    def _generate_interactor_binary(self) -> BaseExecutor:
        files = self.handler_data.files
        if isinstance(files, str):
            filenames = [files]
        elif isinstance(files.unwrap(), list):
            filenames = list(files.unwrap())
        problem_root = get_problem_root(self.problem.id, self.problem.storage_namespace)
        assert problem_root is not None
        filenames = [os.path.join(problem_root, f) for f in filenames]
        flags = self.handler_data.get('flags', [])
        unbuffered = self.handler_data.get('unbuffered', True)
        return compile_with_auxiliary_files(
            self.problem.storage_namespace,
            filenames,
            flags,
            self.handler_data.lang,
            self.handler_data.compiler_time_limit,
            unbuffered,
        )
