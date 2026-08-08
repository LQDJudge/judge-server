import os
import threading
import unittest

from dmoj.utils.interactive_feedback import (
    InteractiveTranscript,
    attach_interaction_transcript,
    combine_interactive_feedback,
    proxy_interaction_stream,
    trim_extended_feedback,
)


class InteractiveFeedbackTest(unittest.TestCase):
    def test_records_prefixed_lines(self):
        transcript = InteractiveTranscript(max_length=128)

        transcript.record('USER', b'12\n34\n')
        transcript.record('JUDGE', 'OK\r\n')

        self.assertEqual(transcript.render(), 'USER: 12\nUSER: 34\nJUDGE: OK')

    def test_trims_transcript(self):
        transcript = InteractiveTranscript(max_length=32)

        transcript.record('USER', 'x' * 100)

        self.assertLessEqual(len(transcript.render()), 32)
        self.assertTrue(transcript.render().endswith('...'))

    def test_combines_existing_feedback_with_transcript(self):
        self.assertEqual(
            combine_interactive_feedback('interactor says no\n', 'USER: 1\nJUDGE: no', max_length=128),
            'USER: 1\nJUDGE: no\n\ninteractor says no',
        )

    def test_moves_short_existing_feedback_to_feedback(self):
        result = type('Result', (), {'feedback': None, 'extended_feedback': 'ok checker: ok\n'})()

        attach_interaction_transcript(result, 'USER: 1\nJUDGE: OK', max_length=128)

        self.assertEqual(result.feedback, 'ok checker: ok')
        self.assertEqual(result.extended_feedback, 'USER: 1\nJUDGE: OK')

    def test_moves_short_non_verdict_existing_feedback_to_feedback(self):
        result = type('Result', (), {'feedback': None, 'extended_feedback': '3 token(s)'})()

        attach_interaction_transcript(result, 'USER: 1\nJUDGE: OK', max_length=128)

        self.assertEqual(result.feedback, '3 token(s)')
        self.assertEqual(result.extended_feedback, 'USER: 1\nJUDGE: OK')

    def test_preserves_existing_feedback_after_full_transcript(self):
        transcript = 'USER: ' + ('x' * 122)
        existing = 'checker detail that should not be dropped'

        self.assertEqual(
            combine_interactive_feedback(existing, transcript, max_length=128),
            transcript + '\n\n' + existing,
        )

    def test_trim_extended_feedback(self):
        self.assertEqual(trim_extended_feedback('abcdef', max_length=4), 'abcd')
        self.assertTrue(trim_extended_feedback('x' * 100, max_length=64).endswith('...'))

    def test_proxy_interaction_stream_records_and_forwards_data(self):
        src_read, src_write = os.pipe()
        dst_read, dst_write = os.pipe()
        transcript = InteractiveTranscript(max_length=128)
        thread = threading.Thread(
            target=proxy_interaction_stream,
            args=(src_read, dst_write, 'USER', transcript),
        )

        thread.start()
        os.write(src_write, b'hello\n')
        os.close(src_write)
        forwarded = os.read(dst_read, 1024)
        os.close(dst_read)
        thread.join()

        self.assertEqual(forwarded, b'hello\n')
        self.assertEqual(transcript.render(), 'USER: hello')
