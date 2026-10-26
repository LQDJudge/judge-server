"""Verify site requested artifact digests against actual worker package bytes."""
import hashlib
import os
import platform

from dmoj.judgeenv import get_problem_root, get_runtime_versions


def attest_package(problem_id, binding, actual_source_hash=None):
    if not binding:
        return {}
    root = get_problem_root(problem_id)
    artifacts = binding.get('requested_artifacts', {})
    verified = bool(root and artifacts and artifacts.get('init_yml'))
    observed = {}
    for key, metadata in artifacts.items():
        filename = metadata.get('filename', '')
        if filename != os.path.basename(filename) or not filename or (not key.startswith(('aux:', 'signature:')) and key not in (
                'init_yml', 'zipfile', 'generator', 'testcase_validator', 'custom_checker_cpp',
                'custom_checker', 'interactive_judge', 'communication_manager')):
            verified = False
            continue
        path = os.path.realpath(os.path.join(root or '', filename))
        if not root or os.path.commonpath((os.path.realpath(root), path)) != os.path.realpath(root):
            verified = False
            continue
        try:
            h = hashlib.sha256()
            size = 0
            with open(path, 'rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise ValueError('Artifact exceeds attestation limit')
                    h.update(chunk)
            observed[key] = h.hexdigest()
            verified = verified and h.hexdigest() == metadata.get('sha256') and size == metadata.get('size')
        except (OSError, ValueError):
            verified = False
    actual_source_hash = actual_source_hash or observed.get('testcase_validator')
    verified = verified and actual_source_hash == binding.get('source_hash')
    return {'artifacts_verified': verified, 'package_hash': binding.get('package_hash'),
            'source_hash': actual_source_hash, 'observed_artifacts': observed,
            'environment': {'platform': platform.platform(), 'runtimes': get_runtime_versions(), 'protocol': 1}}
