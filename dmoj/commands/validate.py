import os
import shlex
import subprocess
from itertools import groupby
from operator import itemgetter
from typing import Generator, List, Optional, Tuple

from dmoj import executors
from dmoj.commands.base_command import Command
from dmoj.contrib import contrib_modules
from dmoj.error import CompileError, InvalidCommandException, OutputLimitExceeded
from dmoj.judgeenv import env, get_problem_root, get_supported_problems
from dmoj.problem import BatchedTestCase, Problem, ProblemConfig, ProblemDataManager, TestCase
from dmoj.result import Result
from dmoj.utils.ansi import print_ansi
from dmoj.utils.helper_files import compile_with_auxiliary_files
from dmoj.utils.unicode import utf8text


all_executors = executors.executors


def validate_problem_cases(problem_id: str) -> Generator[Tuple[str, dict], None, None]:
    """Generator yielding validation events for a problem.

    Yields tuples of (event_type, data_dict):
      ('begin', {'total_cases': int})
      ('case', {'case': int, 'batch': int|None, 'status': str, 'feedback': str})
      ('end', {'passed': bool, 'total': int, 'failed': int})
      ('error', {'error': str})
      ('compile-error', {'error': str})
      ('skip', {'reason': str})
    """
    problem_root = get_problem_root(problem_id)
    if problem_root is None:
        yield ('skip', {'reason': f'Problem {problem_id} not found'})
        return

    config = ProblemConfig(ProblemDataManager(problem_root))
    if not config.validator:
        yield ('skip', {'reason': 'No validator found'})
        return

    validator_config = config['validator']
    language = validator_config['language']
    if language not in all_executors:
        yield ('skip', {'reason': 'Language not supported'})
        return

    time_limit = validator_config.time_limit or env.validator_time_limit
    memory_limit = validator_config.memory_limit or env.validator_memory_limit
    compiler_time_limit = validator_config.get('compiler_time_limit', env.validator_compiler_time_limit)
    read_feedback_from = validator_config.get('read_feedback_from', 'stderr')
    if read_feedback_from not in ('stdout', 'stderr'):
        yield ('error', {'error': 'Feedback option should be (stdout, stderr)'})
        return

    if isinstance(validator_config.source, str):
        filenames = [validator_config.source]
    elif isinstance(validator_config.source.unwrap(), list):
        filenames = list(validator_config.source.unwrap())
    else:
        yield ('error', {'error': 'No validator source found'})
        return

    filenames = [os.path.abspath(os.path.join(problem_root, name)) for name in filenames]
    try:
        executor = compile_with_auxiliary_files(
            None, filenames, lang=language, compiler_time_limit=compiler_time_limit
        )
    except CompileError as compilation_error:
        yield ('compile-error', {'error': compilation_error.message.rstrip()})
        return

    problem = Problem(problem_id, time_limit, memory_limit, {})

    flattened_cases: List[Tuple[Optional[int], TestCase]] = []
    batch_number = 0
    for case in problem.cases():
        if isinstance(case, BatchedTestCase):
            batch_number += 1
            for batched_case in case.batched_cases:
                assert isinstance(batched_case, TestCase)
                flattened_cases.append((batch_number, batched_case))
        else:
            assert isinstance(case, TestCase)
            flattened_cases.append((None, case))

    contrib_type = validator_config.get('type', 'default')
    if contrib_type not in contrib_modules:
        yield ('error', {'error': f'{contrib_type} is not a valid contrib module'})
        return

    args_format_string = (
        validator_config.args_format_string
        or contrib_modules[contrib_type].ContribModule.get_validator_args_format_string()
    )

    total_cases = len(flattened_cases)
    yield ('begin', {'total_cases': total_cases})

    case_number = 0
    failed_count = 0
    for batch_number, cases in groupby(flattened_cases, key=itemgetter(0)):
        for _, case in cases:
            case_number += 1

            result = Result(case)
            input = case.input_data()

            validator_args = shlex.split(args_format_string.format(batch_no=case.batch, case_no=case.position))
            process = executor.launch(
                *validator_args,
                time=time_limit,
                memory=memory_limit,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                wall_time=case.config.wall_time_factor * time_limit,
            )
            try:
                proc_output, proc_error = process.communicate(
                    input, outlimit=case.config.output_limit_length, errlimit=1048576
                )
            except OutputLimitExceeded:
                proc_error = b''
                process.kill()
            finally:
                process.wait()

            executor.populate_result(proc_error, result, process)
            feedback = (
                utf8text({'stdout': proc_output, 'stderr': proc_error}[read_feedback_from].rstrip())
                or result.feedback
            )
            code = result.readable_codes()[0]
            if code == 'AC':
                code = 'OK'

            status = code
            if result.result_flag:
                failed_count += 1

            yield ('case', {
                'case': case_number,
                'batch': batch_number,
                'status': status,
                'feedback': feedback or '',
            })

    yield ('end', {
        'passed': failed_count == 0,
        'total': total_cases,
        'failed': failed_count,
    })


class ValidateCommand(Command):
    name = 'validate'
    help = 'Validates input for problems.'

    def _populate_parser(self) -> None:
        self.arg_parser.add_argument('problem_ids', nargs='+', help='ids of problems to validate input')

    def execute(self, line: str) -> int:
        args = self.arg_parser.parse_args(line)

        problem_ids = args.problem_ids
        supported_problems = set(get_supported_problems())

        unknown_problems = ', '.join(
            f"'{problem_id}'" for problem_id in problem_ids if problem_id not in supported_problems
        )
        if unknown_problems:
            raise InvalidCommandException(f'unknown problem(s) {unknown_problems}')

        total_fails = 0
        for problem_id in problem_ids:
            if not self.validate_problem(problem_id):
                print_ansi(f'Problem #ansi[{problem_id}](cyan|bold) #ansi[failed validation](red|bold).')
                total_fails += 1
            else:
                print_ansi(f'Problem #ansi[{problem_id}](cyan|bold) passed with flying colours.')
            print()

        print()
        print('Input validation complete.')
        if total_fails:
            print_ansi(f'#ansi[A total of {total_fails} problem(s) have invalid input.](red|bold)')
        else:
            print_ansi('#ansi[All problems validated.](green|bold)')

        return total_fails

    def validate_problem(self, problem_id: str) -> bool:
        print_ansi(f'Validating problem #ansi[{problem_id}](cyan|bold)...')

        ok = True
        for event_type, data in validate_problem_cases(problem_id):
            if event_type == 'skip':
                print_ansi(f'\t#ansi[Skipped](magenta|bold) - {data["reason"]}')
                return True
            elif event_type == 'compile-error':
                print_ansi('#ansi[Failed compiling validator!](red|bold)')
                print(data['error'])
                return False
            elif event_type == 'error':
                print_ansi(f'\t#ansi[Failed](red|bold) - {data["error"]}')
                return False
            elif event_type == 'begin':
                pass
            elif event_type == 'case':
                code = data['status']
                code_colour = Result.COLORS_BYID.get(code, Result.COLORS_BYID.get('WA', ''))
                if code == 'OK':
                    code_colour = Result.COLORS_BYID.get('AC', 'green')
                colored_code = f'#ansi[{code}]({code_colour}|bold)'
                feedback = data['feedback']
                colored_feedback = f'(#ansi[{feedback}](|underline))' if feedback else ''
                case_padding = '  ' if data['batch'] is not None else ''
                print_ansi(f'{case_padding}Test case {data["case"]:2d} {colored_code:3s} {colored_feedback}')

                if code != 'OK':
                    ok = False
            elif event_type == 'end':
                ok = data['passed']

        return ok
