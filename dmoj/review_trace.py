"""Bounded byte relays for private review diagnostics, never a protocol oracle."""
import base64
import os
import select
import tempfile
import threading


class ReviewTrace:
    def __init__(self):
        self.events = []
        self.total = 0
        self.limit_exceeded = False
        self.truncated = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._fifo_paths = []
        self._directory = None

    def _capture(self, label, raw):
        with self._lock:
            self.total += len(raw)
            if self.total > 1024 * 1024:
                self.limit_exceeded = True
                self._stop.set()
            if len(self.events) < 64:
                self.events.append({'direction': label, 'bytes': len(raw),
                                    'preview': raw[:256].decode('utf8', 'replace'),
                                    'preview_b64': base64.b64encode(raw[:256]).decode()})
            else:
                self.truncated = True
            self.truncated |= len(raw) > 256

    def relay(self, read_fd, write_fd, label):
        self._start(lambda: (read_fd, write_fd), label)

    def _start(self, opener, label):
        def pump():
            source = target = None
            try:
                source, target = opener()
                os.set_blocking(source, False)
                os.set_blocking(target, False)
                while not self._stop.is_set():
                    readable, _, _ = select.select([source], [], [], .05)
                    if not readable:
                        continue
                    try:
                        raw = os.read(source, 4096)
                    except BlockingIOError:
                        continue
                    if not raw:
                        break
                    self._capture(label, raw)
                    pending = memoryview(raw)
                    while pending and not self._stop.is_set():
                        _, writable, _ = select.select([], [target], [], .05)
                        if not writable:
                            continue
                        try:
                            written = os.write(target, pending)
                        except BlockingIOError:
                            continue
                        pending = pending[written:]
            except OSError:
                # EOF/broken pipe is interpreted by the actual grader. It is
                # not promoted to a successful expected rejection here.
                pass
            finally:
                for descriptor in (source, target):
                    if descriptor is not None:
                        os.close(descriptor)
        thread = threading.Thread(target=pump, daemon=True)
        self._threads.append(thread)
        thread.start()

    def communication_args(self, arguments):
        self._directory = tempfile.TemporaryDirectory(prefix='review-fifos-')
        replacement = []
        for index in range(0, len(arguments), 2):
            native_to_manager, native_to_user = arguments[index:index + 2]
            proxy_to_manager = os.path.join(self._directory.name, 'to-manager-%s' % index)
            proxy_to_user = os.path.join(self._directory.name, 'to-user-%s' % index)
            for path in (proxy_to_manager, proxy_to_user):
                os.mkfifo(path, 0o600)
                self._fifo_paths.append(path)
            # Wait for the manager side FIRST. Connecting original FIFOs early
            # would incorrectly turn never-contacted users into participants.
            def incoming(proxy=proxy_to_manager, native=native_to_manager):
                target = os.open(proxy, os.O_WRONLY)
                try:
                    return os.open(native, os.O_RDONLY), target
                except BaseException:
                    os.close(target)
                    raise
            def outgoing(proxy=proxy_to_user, native=native_to_user):
                source = os.open(proxy, os.O_RDONLY)
                try:
                    return source, os.open(native, os.O_WRONLY)
                except BaseException:
                    os.close(source)
                    raise
            self._start(incoming, 'process %s -> manager' % (index // 2))
            self._start(outgoing, 'manager -> process %s' % (index // 2))
            replacement.extend((proxy_to_manager, proxy_to_user))
        return replacement

    @property
    def fifo_paths(self):
        return self._fifo_paths

    def close(self):
        self._stop.set()
        release = []
        for path in self._fifo_paths:
            try:
                release.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            except OSError:
                pass
        for thread in self._threads:
            thread.join(.2)
        for descriptor in release:
            os.close(descriptor)
        if self._directory is not None:
            self._directory.cleanup()

    def summary(self):
        return {'events': list(self.events), 'bytes_observed': self.total,
                'truncated': self.truncated, 'limit_exceeded': self.limit_exceeded,
                'scope': 'Ordered byte chunks with bounded previews; chunks are not protocol message boundaries.'}
