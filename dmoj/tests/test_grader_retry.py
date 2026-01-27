import unittest
from unittest import mock

from dmoj.graders.standard import StandardGrader
from dmoj.result import Result


class MockProcess:
    """Mock process that can simulate TLE behavior."""

    def __init__(self, is_tle=False, execution_time=0.0):
        self.is_tle = is_tle
        self.execution_time = execution_time
        self.kill_called = False

    def kill(self):
        self.kill_called = True


class TestGraderRetryLogic(unittest.TestCase):
    def setUp(self):
        # Create mock objects
        self.mock_problem = mock.MagicMock()
        self.mock_problem.time_limit = 1.0  # 1 second time limit
        self.mock_problem.memory_limit = 65536

        self.mock_case = mock.MagicMock()
        self.mock_case.problem = self.mock_problem
        self.mock_case.points = 10
        self.mock_case.position = 1
        self.mock_case.batch = None
        self.mock_case.has_binary_data = False
        self.mock_case.config.output_limit_length = 1024
        self.mock_case.config.wall_time_factor = 3
        self.mock_case.config.symlinks = {}
        self.mock_case.config.file_io = None

        # Mock input_data_io to return a fresh mock each time
        self.mock_case.input_data_io.return_value = mock.MagicMock()

        # Mock checker
        mock_checker = mock.MagicMock(return_value=True)
        mock_checker.func = mock.MagicMock()
        mock_checker.func.run_on_error = False
        self.mock_case.checker.return_value = mock_checker
        self.mock_case.output_data.return_value = b'expected output'
        self.mock_case.input_data = b'input data'

    def _create_grader(self):
        """Create a StandardGrader with mocked dependencies."""
        with mock.patch.object(StandardGrader, '__init__', lambda x, *args, **kwargs: None):
            grader = StandardGrader()
            grader.problem = self.mock_problem
            grader.binary = mock.MagicMock()
            grader.binary.populate_result = mock.MagicMock()
            grader.source = b'source code'
            grader.language = 'PY3'
            grader._current_proc = None
            return grader

    def test_no_retry_when_not_tle(self):
        """Test that no retry happens when process doesn't TLE."""
        grader = self._create_grader()

        # Process that completes normally (no TLE)
        process = MockProcess(is_tle=False, execution_time=0.5)

        launch_count = 0

        def mock_launch(case, input_file=None):
            nonlocal launch_count
            launch_count += 1
            grader._current_proc = process

        def mock_interact(case, result):
            result.proc_output = b'output'
            return b''

        grader._launch_process = mock_launch
        grader._interact_with_process = mock_interact

        result = grader.grade(self.mock_case)

        self.assertEqual(launch_count, 1, "Should only launch once when not TLE")
        self.assertFalse(process.kill_called, "Should not kill process when not TLE")

    def test_no_retry_when_tle_but_execution_time_too_high(self):
        """Test that no retry happens when TLE but execution time is not close to limit."""
        grader = self._create_grader()

        # Process that TLEs but execution time is >= time_limit + 0.5
        process = MockProcess(is_tle=True, execution_time=1.6)  # 1.0 + 0.5 = 1.5, so 1.6 >= 1.5

        launch_count = 0

        def mock_launch(case, input_file=None):
            nonlocal launch_count
            launch_count += 1
            grader._current_proc = process

        def mock_interact(case, result):
            result.proc_output = b'output'
            return b''

        grader._launch_process = mock_launch
        grader._interact_with_process = mock_interact

        result = grader.grade(self.mock_case)

        self.assertEqual(launch_count, 1, "Should only launch once when execution time >= limit + 0.5")
        self.assertFalse(process.kill_called, "Should not kill process when not retrying")

    def test_retry_when_tle_close_to_limit(self):
        """Test that retry happens when TLE and execution time is close to limit."""
        grader = self._create_grader()

        launch_count = 0
        processes = []

        def mock_launch(case, input_file=None):
            nonlocal launch_count
            launch_count += 1
            # First two attempts: TLE with execution time close to limit
            # Third attempt: success (no TLE)
            if launch_count <= 2:
                process = MockProcess(is_tle=True, execution_time=1.2)  # Close to 1.0 limit
            else:
                process = MockProcess(is_tle=False, execution_time=0.8)
            processes.append(process)
            grader._current_proc = process

        def mock_interact(case, result):
            result.proc_output = b'output'
            return b''

        grader._launch_process = mock_launch
        grader._interact_with_process = mock_interact

        result = grader.grade(self.mock_case)

        self.assertEqual(launch_count, 3, "Should retry twice then succeed on third attempt")
        self.assertTrue(processes[0].kill_called, "First TLE process should be killed")
        self.assertTrue(processes[1].kill_called, "Second TLE process should be killed")
        self.assertFalse(processes[2].kill_called, "Successful process should not be killed")

    def test_max_retries_exhausted(self):
        """Test that retry stops after 3 attempts even if still TLE."""
        grader = self._create_grader()

        launch_count = 0
        processes = []

        def mock_launch(case, input_file=None):
            nonlocal launch_count
            launch_count += 1
            # Always TLE with execution time close to limit
            process = MockProcess(is_tle=True, execution_time=1.2)
            processes.append(process)
            grader._current_proc = process

        def mock_interact(case, result):
            result.proc_output = b'output'
            return b''

        grader._launch_process = mock_launch
        grader._interact_with_process = mock_interact

        result = grader.grade(self.mock_case)

        # With retry_count=3, all 3 attempts are made and all processes are killed
        # The loop exits when retry_count becomes 0
        self.assertEqual(launch_count, 3, "Should stop after 3 attempts")
        self.assertTrue(processes[0].kill_called, "First process should be killed")
        self.assertTrue(processes[1].kill_called, "Second process should be killed")
        self.assertTrue(processes[2].kill_called, "Third process should be killed")

    def test_fresh_input_io_for_each_retry(self):
        """Test that input_data_io() is called for each retry attempt."""
        grader = self._create_grader()

        launch_count = 0

        def mock_launch(case, input_file=None):
            nonlocal launch_count
            launch_count += 1
            if launch_count < 3:
                grader._current_proc = MockProcess(is_tle=True, execution_time=1.2)
            else:
                grader._current_proc = MockProcess(is_tle=False, execution_time=0.8)

        def mock_interact(case, result):
            result.proc_output = b'output'
            return b''

        grader._launch_process = mock_launch
        grader._interact_with_process = mock_interact

        result = grader.grade(self.mock_case)

        # input_data_io should be called once per attempt
        self.assertEqual(
            self.mock_case.input_data_io.call_count, 3, "input_data_io should be called for each retry"
        )


if __name__ == '__main__':
    unittest.main()
