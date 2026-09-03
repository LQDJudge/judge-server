from typing import Any, TYPE_CHECKING

from dmoj.executors.base_executor import BaseExecutor

if TYPE_CHECKING:
    from dmoj.cptbox import TracedPopen


class BaseContribModule:
    name: str
    AC = 0
    WA = 1

    def catch_internal_error(f: Any) -> Any:
        # Checker/helper failures are judge infrastructure errors, not wrong
        # answers. Let InternalError reach the judge worker so the site receives
        # an internal-error packet instead of a failed CheckerResult.
        return f

    @classmethod
    def get_checker_args_format_string(cls) -> str:
        raise NotImplementedError

    @classmethod
    def get_interactor_args_format_string(cls) -> str:
        raise NotImplementedError

    @classmethod
    def get_validator_args_format_string(cls) -> str:
        raise NotImplementedError

    @classmethod
    def parse_return_code(
        cls,
        proc: 'TracedPopen',
        executor: BaseExecutor,
        point_value: float,
        time_limit: float,
        memory_limit: int,
        feedback: str,
        extended_feedback: str,
        name: str,
        stderr: bytes,
        **kwargs,
    ):
        raise NotImplementedError
