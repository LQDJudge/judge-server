import unittest

from dmoj.graders.communication import CommunicationGrader


class MockProcess:
    def __init__(self, fail_kill=False):
        self.fail_kill = fail_kill
        self.kill_called = False

    def kill(self):
        self.kill_called = True
        if self.fail_kill:
            raise OSError


class CommunicationAbortTest(unittest.TestCase):
    def test_abort_kills_manager_and_user_processes(self):
        grader = CommunicationGrader.__new__(CommunicationGrader)
        manager = MockProcess()
        user_procs = [MockProcess(), MockProcess()]
        grader._current_proc = manager
        grader._user_procs = user_procs

        grader.abort_grading()

        self.assertTrue(grader._abort_requested)
        self.assertTrue(manager.kill_called)
        self.assertTrue(all(process.kill_called for process in user_procs))

    def test_abort_ignores_user_process_kill_errors(self):
        grader = CommunicationGrader.__new__(CommunicationGrader)
        manager = MockProcess()
        user_procs = [MockProcess(fail_kill=True), MockProcess()]
        grader._current_proc = manager
        grader._user_procs = user_procs

        grader.abort_grading()

        self.assertTrue(manager.kill_called)
        self.assertTrue(all(process.kill_called for process in user_procs))


if __name__ == '__main__':
    unittest.main()
