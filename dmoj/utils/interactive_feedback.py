import errno
import os
import threading
from typing import AnyStr, List, Optional

from dmoj.utils.unicode import utf8text

TRUNCATION_MESSAGE = '\n...'
SHORT_FEEDBACK_LIMIT = 50


def trim_extended_feedback(feedback: str, max_length: int) -> str:
    if max_length <= 0:
        return ''
    if len(feedback) <= max_length:
        return feedback
    if max_length <= len(TRUNCATION_MESSAGE):
        return feedback[:max_length]
    return feedback[: max_length - len(TRUNCATION_MESSAGE)] + TRUNCATION_MESSAGE


class InteractiveTranscript:
    def __init__(self, max_length: int) -> None:
        self.max_length = max_length
        self._entries: List[str] = []
        self._length = 0
        self._truncated = False
        self._lock = threading.Lock()

    def record(self, label: str, data: AnyStr) -> None:
        with self._lock:
            if self._truncated:
                return

        text = utf8text(data, 'replace') if isinstance(data, bytes) else str(data)
        text = text.replace('\r\n', '\n').replace('\r', '\n').rstrip('\n')
        if not text:
            return

        entry = '\n'.join(f'{label}: {line}' for line in text.split('\n'))
        with self._lock:
            if self._truncated:
                return
            self._append(entry)

    def _append(self, entry: str) -> None:
        if self._truncated:
            return

        prefix = '\n' if self._entries else ''
        addition = prefix + entry
        if self._length + len(addition) <= self.max_length:
            self._entries.append(addition)
            self._length += len(addition)
            return

        remaining = self.max_length - self._length
        if remaining > len(TRUNCATION_MESSAGE):
            self._entries.append((prefix + entry)[: remaining - len(TRUNCATION_MESSAGE)] + TRUNCATION_MESSAGE)
        elif self._entries:
            self._entries[-1] = self._entries[-1][: max(0, len(self._entries[-1]) - len(TRUNCATION_MESSAGE))]
            self._entries[-1] += TRUNCATION_MESSAGE
        else:
            self._entries.append(TRUNCATION_MESSAGE.lstrip()[: self.max_length])
        self._truncated = True
        self._length = self.max_length

    def render(self) -> str:
        return ''.join(self._entries)


def combine_interactive_feedback(existing: Optional[str], transcript: str, max_length: int) -> Optional[str]:
    if not transcript:
        return existing
    if not existing:
        return transcript
    return f'{transcript}\n\n{existing.rstrip()}'


def attach_interaction_transcript(result, transcript: str, max_length: int) -> None:
    existing = result.extended_feedback
    if existing and not result.feedback:
        short_feedback = existing.strip()
        if short_feedback and '\n' not in short_feedback and len(short_feedback) <= SHORT_FEEDBACK_LIMIT:
            result.feedback = short_feedback
            existing = None

    result.extended_feedback = combine_interactive_feedback(existing, transcript, max_length)


def proxy_interaction_stream(src_fd: int, dst_fd: int, label: str, transcript: InteractiveTranscript) -> None:
    try:
        while True:
            data = os.read(src_fd, 4096)
            if not data:
                return

            transcript.record(label, data)
            view = memoryview(data)
            while view:
                try:
                    written = os.write(dst_fd, view)
                except OSError as e:
                    if e.errno == errno.EPIPE:
                        return
                    raise
                view = view[written:]
    finally:
        for fd in (src_fd, dst_fd):
            try:
                os.close(fd)
            except OSError:
                pass
