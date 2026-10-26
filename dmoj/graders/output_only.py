import hashlib
import time
from io import BytesIO
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile, ZIP_LZMA

from dmoj.error import CompileError
from dmoj.executors import executors
from dmoj.executors.base_executor import BaseExecutor
from dmoj.graders.standard import StandardGrader
from dmoj.problem import Problem, TestCase
from dmoj.result import CheckerResult, Result
from dmoj.utils.helper_files import FunctionTimeout, download_source_code
from dmoj.utils.unicode import utf8text

if TYPE_CHECKING:
    from dmoj.judge import JudgeWorker


class OutputOnlyGrader(StandardGrader):
    def __init__(self, judge: 'JudgeWorker', problem: Problem, language: str, source: bytes) -> None:
        super().__init__(judge, problem, language, source)
        if language == 'OUTPUT':
            self.zip_file = self.get_zip_file()

    def _generate_binary(self) -> BaseExecutor:
        if self.language == 'OUTPUT':
            return executors[self.language].Executor(self.problem.id, self.source)
        return super()._generate_binary()

    def _interact_with_zipfile(self, result: Result, case: TestCase) -> None:
        output_name = case.config['out']

        try:
            info = self.zip_file.getinfo(output_name)
        except KeyError:
            result.feedback = '`' + output_name + '` not found in zip file'
            result.result_flag = Result.WA
            return

        if info.file_size > case.config.output_limit_length:
            result.feedback = f'Output is too long ({info.file_size} > {case.config.output_limit_length})'
            result.result_flag = Result.OLE
            return

        result.proc_output = self.zip_file.open(output_name).read()

    def get_zip_file(self) -> ZipFile:
        fize_size_limit = self.problem.config.fize_size_limit or 1
        try:
            # This happens before contestant scoring and its resource limits.
            # The wall deadline also bounds peers that trickle bytes forever.
            with FunctionTimeout(seconds=30):
                zip_data = download_source_code(utf8text(self.source).strip(), fize_size_limit)
        except TimeoutError as error:
            raise CompileError('Timed out downloading the output archive') from error
        self.output_artifact_sha256 = hashlib.sha256(zip_data).hexdigest()
        try:
            archive = ZipFile(BytesIO(zip_data))
            if any(item.compress_type == ZIP_LZMA or item.flag_bits & 1 for item in archive.infolist()):
                archive.close()
                raise CompileError(
                    'LZMA-compressed or encrypted output ZIPs are not supported; use an unencrypted Deflate or stored ZIP'
                )
            return archive
        except BadZipFile as e:
            raise CompileError(repr(e))

    def grade(self, case: TestCase) -> Result:
        if self.language != 'OUTPUT':
            return super().grade(case)

        result = Result(case)

        start_time = time.time()
        try:
            with FunctionTimeout(seconds=self.problem.time_limit):
                self._interact_with_zipfile(result, case)
        except TimeoutError:
            result.result_flag |= Result.TLE

        result.execution_time = time.time() - start_time

        check = self.check_result(case, result)

        # Copy from StandardGrader

        # checkers must either return a boolean (True: full points, False: 0 points)
        # or a CheckerResult, so convert to CheckerResult if it returned bool
        if not isinstance(check, CheckerResult):
            check = CheckerResult(check, case.points if check else 0.0)

        result.result_flag |= [Result.WA, Result.AC][check.passed]
        result.points = check.points
        result.feedback = check.feedback or result.feedback
        result.extended_feedback = check.extended_feedback or result.extended_feedback

        case.free_data()

        return result
