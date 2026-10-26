import signal
import time
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zipfile import ZipFile, ZIP_LZMA

import requests

from dmoj.error import CompileError
from dmoj.graders.output_only import OutputOnlyGrader
from dmoj.utils.helper_files import FunctionTimeout, download_source_code


class OutputDownloadTests(unittest.TestCase):
    def response(self, chunks, length=0):
        response = MagicMock()
        response.headers = {'Content-Length': str(length)}
        response.iter_content.return_value = chunks
        return response

    def test_bounded_download_preserves_bytes_and_closes_connection(self):
        response = self.response([b'\x00\xff', b'\r\n'])
        with patch('dmoj.utils.helper_files.requests.get', return_value=response) as get:
            self.assertEqual(download_source_code('http://localhost/example.zip', 1), b'\x00\xff\r\n')
        self.assertEqual(get.call_args.kwargs['timeout'], (5, 15))
        response.close.assert_called_once()

    def test_header_and_stream_limits_close_connection(self):
        for response in (self.response([], 2 * 1024 * 1024), self.response([b'x' * (1024 * 1024 + 1)])):
            with self.subTest(response=response), patch('dmoj.utils.helper_files.requests.get', return_value=response):
                with self.assertRaises(CompileError):
                    download_source_code('http://localhost/example.zip', 1)
            response.close.assert_called_once()

    def test_stalled_or_failed_transfer_becomes_a_terminal_error(self):
        with patch('dmoj.utils.helper_files.requests.get', side_effect=requests.Timeout('stalled')):
            with self.assertRaises(CompileError):
                download_source_code('http://localhost/example.zip', 1)
        response = self.response([])
        response.iter_content.side_effect = requests.ConnectionError('stream interrupted')
        with patch('dmoj.utils.helper_files.requests.get', return_value=response):
            with self.assertRaises(CompileError):
                download_source_code('http://localhost/example.zip', 1)
        response.close.assert_called_once()

    def test_output_archive_download_has_a_wall_deadline(self):
        previous = signal.getsignal(signal.SIGALRM)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        fake = SimpleNamespace(
            problem=SimpleNamespace(config=SimpleNamespace(fize_size_limit=1)), source=b'http://localhost/example.zip'
        )
        started = time.monotonic()
        with patch(
            'dmoj.graders.output_only.FunctionTimeout', side_effect=lambda seconds: FunctionTimeout(0.05)
        ), patch('dmoj.graders.output_only.download_source_code', side_effect=lambda *args: time.sleep(1)):
            with self.assertRaises(CompileError):
                OutputOnlyGrader.get_zip_file(fake)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_high_memory_compression_is_rejected_before_decompression(self):
        data = BytesIO()
        with ZipFile(data, 'w', compression=ZIP_LZMA) as archive:
            archive.writestr('one.out', b'1\n')
        fake = SimpleNamespace(
            problem=SimpleNamespace(config=SimpleNamespace(fize_size_limit=1)), source=b'http://localhost/example.zip'
        )
        with patch('dmoj.graders.output_only.download_source_code', return_value=data.getvalue()), patch(
            'zipfile.ZipFile.open', side_effect=AssertionError('Unsafe decompressor started')
        ):
            with self.assertRaises(CompileError):
                OutputOnlyGrader.get_zip_file(fake)
