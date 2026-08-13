import unittest
from unittest import mock

from dmoj.config import ConfigNode
from dmoj.executors import OUTPUT, executors
from dmoj.graders.output_only import OutputOnlyGrader


class OutputOnlyGraderTest(unittest.TestCase):
    def test_output_submission_ignores_signature_grader(self):
        problem = mock.Mock()
        problem.id = 'sigout'
        problem.config = ConfigNode(
            {
                'output_only': True,
                'signature_grader': {
                    'entry': 'grader.cpp',
                    'header': 'sigout.h',
                },
            }
        )

        with mock.patch.dict(executors, {'OUTPUT': OUTPUT}), mock.patch.object(
            OutputOnlyGrader, 'get_zip_file', return_value=mock.Mock()
        ):
            grader = OutputOnlyGrader(mock.Mock(), problem, 'OUTPUT', b'https://example.com/output.zip')

        try:
            self.assertEqual(grader.binary.get_executor_name(), 'OUTPUT')
        finally:
            grader.binary.cleanup()


if __name__ == '__main__':
    unittest.main()
