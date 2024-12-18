import builtins
import re
from collections import deque
from typing import Dict, List, Optional

from pygments import highlight
from pygments.formatters import Terminal256Formatter
from pygments.lexers import get_lexer_by_name

from dmoj.cptbox import TracedPopen
from dmoj.executors.base_executor import VersionFlags
from dmoj.executors.compiled_executor import CompiledExecutor
from dmoj.utils.unicode import utf8bytes, utf8text

retraceback = re.compile(r'Traceback \(most recent call last\):\n.*?\n([a-zA-Z_]\w*)(?::[^\n]*?)?$', re.S | re.M)


class PythonExecutor(CompiledExecutor):
    loader_script = """\
import runpy, sys, os
del sys.argv[0]
sys.stdin = os.fdopen(0, 'r', 65536)
sys.stdout = os.fdopen(1, 'w', 65536)
runpy.run_path(sys.argv[0], run_name='__main__')
"""

    unbuffered_loader_script = """\
import runpy, sys
del sys.argv[0]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    syscalls = ['clock_nanosleep']
    address_grace = 131072
    data_grace = 2048
    ext = 'py'
    pygments_traceback_lexer: Optional[str] = None

    def __init__(self, problem_id: str, source_code: bytes, **kwargs) -> None:
        self.source_dict = kwargs.pop('aux_sources', {})
        super().__init__(problem_id, source_code, **kwargs)

    def get_compile_args(self) -> List[str]:
        command = self.get_command()
        assert self._dir is not None
        assert command is not None
        return [command, '-m', 'compileall', '-q', self._dir]

    def get_cmdline(self, **kwargs) -> List[str]:
        # -B: Don't write .pyc/.pyo, since sandbox will kill those writes
        # -S: Disable site module for speed (no loading dist-packages nor site-packages)
        command = self.get_command()
        assert command is not None
        assert self._code is not None
        return [command, '-BS' + ('u' if self.unbuffered else ''), self._loader, self._code]

    def get_executable(self) -> str:
        command = self.get_command()
        assert command is not None
        return command

    def get_env(self) -> Dict[str, str]:
        env = super().get_env()
        # Disable integer string conversion length limitation
        env['PYTHONINTMAXSTRDIGITS'] = '0'
        return env

    def handle_compile_error(self, output: bytes) -> None:
        if self.pygments_traceback_lexer:
            lexer = get_lexer_by_name(self.pygments_traceback_lexer)
            output = utf8bytes(highlight(utf8text(output), lexer, Terminal256Formatter()))
        super().handle_compile_error(output)

    def create_files(self, problem_id: str, source_code: bytes, *args, **kwargs) -> None:
        super().create_files(problem_id, source_code, **kwargs)
        self._loader = self._file('-loader.py')
        assert self._code is not None
        with open(self._code, 'wb') as fo, open(self._loader, 'w') as loader:
            # We want source code to be UTF-8, but the normal (Python 2) way of having
            # "# -*- coding: utf-8 -*-" in header changes line numbers, so we write
            # UTF-8 BOM instead.
            fo.write(b'\xef\xbb\xbf')
            fo.write(utf8bytes(source_code))
            loader.write(self.unbuffered_loader_script if self.unbuffered else self.loader_script)

        self.source_paths = []
        for name, source in self.source_dict.items():
            if '.' not in name:
                name += '.' + self.ext
            with open(self._file(name), 'wb') as fo:
                fo.write(utf8bytes(source))
            self.source_paths.append(name)

    def parse_feedback_from_stderr(self, stderr: bytes, process: TracedPopen) -> str:
        if not stderr or len(stderr) > 2048:
            return ''
        match = deque(retraceback.finditer(utf8text(stderr, 'replace')), maxlen=1)
        if not match:
            return ''
        exception = match[0].group(1)
        if exception in builtins.__dict__ and issubclass(builtins.__dict__[exception], BaseException):
            return exception
        else:
            return ''

    @classmethod
    def get_version_flags(cls, command: str) -> List[VersionFlags]:
        return ['-V']
