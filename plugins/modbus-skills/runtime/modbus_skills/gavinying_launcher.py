"""Standalone, bounded Gavinying final-output launcher copied into exports.

Only the generated COMPILED_SETUP assignment differs in the delivered script.
This module does not decode Modbus values or change the native client's retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

COMPILED_SETUP = None
MAX_OUTPUT_BYTES = 1_048_576
MAX_JSON_BYTES = 16_777_216


class LauncherError(ValueError):
    """An unsafe, stale, incomplete or failed native result."""


def parse_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise LauncherError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise LauncherError(f"Nonfinite JSON constant: {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as error:
        raise LauncherError(f"Invalid JSON: {error}") from error


def validate_values(data, expected):
    if not expected or not isinstance(data, dict) or set(data) != set(expected):
        raise LauncherError("Export device keys differ from the compiled setup")
    for device, refs in expected.items():
        values = data[device]
        if not isinstance(values, dict) or set(values) != set(refs):
            raise LauncherError("Export reference keys differ from the compiled setup")
        for name, spec in refs.items():
            value = values[name]
            datatype, scale = spec['datatype'], spec['scale']
            if datatype.startswith('string') and datatype[6:].isdigit() and scale == 1:
                if not isinstance(value, str) or len(value.encode('utf-8')) > int(datatype[6:]):
                    raise LauncherError(f"Invalid bounded string reading: {device}/{name}")
                continue
            if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
                raise LauncherError(f"Missing, nonnumeric or nonfinite reading: {device}/{name}")
            if datatype.startswith(('int', 'uint')) and scale == 1:
                bits = int(datatype[4:] if datatype.startswith('uint') else datatype[3:])
                low = 0 if datatype.startswith('uint') else -(1 << (bits - 1))
                high = (1 << bits) - 1 if datatype.startswith('uint') else (1 << (bits - 1)) - 1
                if type(value) is not int or not low <= value <= high:
                    raise LauncherError(f"Invalid exact integer reading: {device}/{name}")
            else:
                if datatype not in {'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'float16', 'float32', 'float64', 'double'}:
                    raise LauncherError("Unsupported compiled numeric type")
                # Native float decoders and nonidentity multipliers export JSON
                # floating-point values. Do not accept an unrelated huge integer.
                if type(value) is not float:
                    raise LauncherError(f"Invalid engineering floating-point reading: {device}/{name}")
    return data


def execute_native(argv, *, cwd, deadline_seconds, max_output_bytes):
    """Run one child, drain bounded output, and reap the owned process group."""
    start = time.monotonic()
    child = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    timed_out = output_limited = False
    interrupted = None
    try:
        while selector.get_map():
            if time.monotonic() - start >= deadline_seconds:
                timed_out = True
                break
            for key, _ in selector.select(.1):
                part = os.read(key.fileobj.fileno(), 65536)
                if not part:
                    selector.unregister(key.fileobj)
                    continue
                remaining = max_output_bytes - sum(map(len, chunks.values()))
                chunks[key.data].extend(part[:max(0, remaining)])
                if len(part) > remaining:
                    output_limited = True
                    break
            if output_limited:
                break
        if timed_out or output_limited:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=max(.1, deadline_seconds - (time.monotonic() - start)))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=2)
    except LauncherError as error:
        interrupted = str(error)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=2)
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)
    return {'pid': child.pid, 'child_reaped': child.poll() is not None,
            'returncode': child.returncode, 'stdout': chunks['stdout'].decode('utf-8', 'replace'),
            'stderr': chunks['stderr'].decode('utf-8', 'replace'), 'timed_out': timed_out,
            'output_limited': output_limited, 'interrupted': interrupted is not None,
            'error': interrupted, 'elapsed_seconds': time.monotonic() - start}


def _secure_platform():
    required = (os.open, os.stat, os.mkdir, os.unlink, os.rmdir, os.rename)
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not shutil.rmtree.avoids_symlink_attacks or any(f not in os.supports_dir_fd for f in required):
        raise LauncherError("Safe publication requires POSIX directory-fd/O_NOFOLLOW support; no native connection opened")


def _open_directory(path):
    """Walk absolute directory components without following symlinks."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except OSError as error:
        os.close(fd)
        raise LauncherError(f"Unsafe or unavailable directory: {error}") from error


def _read_regular(directory_fd, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
            raise LauncherError("Expected a bounded regular file with one link")
        value = bytearray()
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - len(value)))
            if not chunk:
                return bytes(value)
            value.extend(chunk)
            if len(value) > limit:
                raise LauncherError("File exceeds the declared byte limit")
    finally:
        os.close(fd)


def _write_new(directory_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def _encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def _check_result(directory_fd, binding):
    try:
        prior = parse_json(_read_regular(directory_fd, 'result.json', MAX_JSON_BYTES))
    except FileNotFoundError:
        return
    if not isinstance(prior, dict) or prior.get('schema_version') != 'gavinying-final-result/v1' or prior.get('binding_sha256') != binding:
        raise LauncherError("Refusing to replace a foreign result file")


def _publish(directory_fd, result, binding):
    _check_result(directory_fd, binding)
    payload = _encoded(result)
    if len(payload) > MAX_JSON_BYTES:
        raise LauncherError("Result envelope exceeds byte limit")
    _write_new(directory_fd, '.result.tmp', payload)
    try:
        os.replace('.result.tmp', 'result.json', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink('.result.tmp', dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _open_output(path, binding):
    path = Path(os.path.abspath(path))
    parent = _open_directory(path.parent)
    created = False
    try:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as error:
        raise LauncherError(f"Unsafe result directory: {error}") from error
    finally:
        os.close(parent)
    owner = {'schema_version': 'gavinying-output-owner/v1', 'binding_sha256': binding}
    try:
        metadata = os.fstat(fd)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise LauncherError("Result directory must be private and owned by this user")
        if created:
            _write_new(fd, '.owner.json', _encoded(owner))
        elif parse_json(_read_regular(fd, '.owner.json', 4096)) != owner:
            raise LauncherError("Output directory belongs to a different compiled setup")
        allowed = {'.owner.json', 'result.json', '.lock', '.stage', '.result.tmp'}
        if set(os.listdir(fd)) - allowed:
            raise LauncherError("Output directory contains foreign files")
        _check_result(fd, binding)
        return fd
    except (OSError, LauncherError) as error:
        os.close(fd)
        raise LauncherError(f"Unsafe output ownership: {error}") from error


def run(setup, *, config_directory, output_directory, host, port, executable='modpoll'):
    _secure_platform()
    if not isinstance(host, str) or not host.strip() or host.startswith('-') or any(c.isspace() for c in host):
        raise LauncherError("An explicit endpoint is required")
    if type(port) is not int or not 1 <= port <= 65535:
        raise LauncherError("Port must be1..65535")
    binding = hashlib.sha256(_encoded(setup)).hexdigest()
    output_directory = Path(os.path.abspath(output_directory))
    directory_fd = _open_output(output_directory, binding)
    locked = staged = False
    stage_fd = None
    result = {'schema_version': 'gavinying-final-result/v1', 'status': 'running',
              'binding_sha256': binding, 'run_id': uuid.uuid4().hex,
              'started_at_unix': time.time(), 'route_id': setup['route_id'],
              'map_sha256': setup['map_sha256'], 'plan_sha256': setup['plan_sha256'],
              'config_sha256': setup['config_sha256'], 'requests': setup['requests'],
              'endpoint': {'host': host, 'port': port}, 'values_current': False}
    try:
        try:
            _write_new(directory_fd, '.lock', _encoded({'run_id': result['run_id'], 'pid': os.getpid()}))
            locked = True
        except FileExistsError as error:
            raise LauncherError("Another run or retained lock owns this output; no native connection opened") from error
        _publish(directory_fd, result, binding)
        try:
            config_fd = _open_directory(config_directory)
            try:
                name = setup['config_filename']
                if Path(name).name != name or name in {'.', '..'}:
                    raise LauncherError("Compiled config filename must be a basename")
                config = _read_regular(config_fd, name, MAX_JSON_BYTES)
            finally:
                os.close(config_fd)
            if hashlib.sha256(config).hexdigest() != setup['config_sha256']:
                raise LauncherError("Config bytes no longer match the compiled setup")
            os.mkdir('.stage', mode=0o700, dir_fd=directory_fd)
            staged = True
            stage_fd = os.open('.stage', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            _write_new(stage_fd, 'config.csv', config)
            stage = output_directory / '.stage'
            # Bind the path handed to the child to the directory already opened safely.
            check = _open_directory(stage)
            try:
                observed, opened = os.fstat(check), os.fstat(stage_fd)
                if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
                    raise LauncherError("Staging directory identity changed")
            finally:
                os.close(check)
            argv = [executable, '--once', '--tcp', host, '--tcp-port', str(port),
                    '--config', str(stage / 'config.csv'), '--export', str(stage / 'native.json')]
            result['native_command'] = argv
            result['native'] = execute_native(argv, cwd=stage, deadline_seconds=setup['max_runtime_seconds'], max_output_bytes=MAX_OUTPUT_BYTES)
            native = result['native']
            if native['returncode'] != 0 or native['timed_out'] or native['output_limited'] or native.get('interrupted'):
                raise LauncherError(native.get('error') or "Native process failed or reached a safety bound")
            raw = _read_regular(stage_fd, 'native.json', MAX_JSON_BYTES)
            values = validate_values(parse_json(raw), setup['expected'])
            result.update(status='succeeded', values_current=True, values=values,
                          native_export_sha256=hashlib.sha256(raw).hexdigest())
        except (OSError, ValueError, KeyError) as error:
            result.update(status='failed', values_current=False, error=str(error))
            result.pop('values', None)
        finally:
            if stage_fd is not None:
                owned = os.fstat(stage_fd)
                os.close(stage_fd)
                stage_fd = None
                try:
                    current = os.stat('.stage', dir_fd=directory_fd, follow_symlinks=False)
                    if (owned.st_dev, owned.st_ino) != (current.st_dev, current.st_ino):
                        raise LauncherError("Staging ownership changed; refusing cleanup")
                    # This exact fresh private staging directory is owned by this
                    # invocation; fd-based rmtree never follows child symlinks.
                    shutil.rmtree('.stage', dir_fd=directory_fd)
                except (OSError, LauncherError) as error:
                    result.update(status='failed', values_current=False, error=f"Staging cleanup failed: {error}")
                    result.pop('values', None)
            elif staged:
                try:
                    os.rmdir('.stage', dir_fd=directory_fd)
                except OSError as error:
                    result.update(status='failed', values_current=False, error=f"Staging cleanup failed: {error}")
                    result.pop('values', None)
        result['finished_at_unix'] = time.time()
        _publish(directory_fd, result, binding)
        return result
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        if locked:
            os.unlink('.lock', dir_fd=directory_fd)
        os.close(directory_fd)


def invocation_receipt(result, result_path, *, published):
    """Compact invocation outcome; full values/diagnostics stay in result.json."""
    succeeded = published and result.get('status') == 'succeeded'
    receipt = {'schema_version': 'gavinying-final-invocation/v1',
               'status': 'succeeded' if succeeded else 'failed', 'published': published,
               'result_path': str(result_path) if published else None,
               'run_id': result.get('run_id') if published else None,
               'binding_sha256': result.get('binding_sha256') if published else None,
               'value_count': sum(len(refs) for refs in result.get('values', {}).values()) if succeeded else 0}
    if not succeeded:
        receipt['error'] = str(result.get('error', 'Native final output was not accepted'))[:300]
    return receipt


def _interrupted(signum, frame):
    # One ordinary interruption is handled. Repeated SIGKILL/power loss cannot
    # be cleaned up; retained owned lock/staging is never guessed away later.
    signal.signal(signum, signal.SIG_IGN)
    raise LauncherError(f"Interrupted by signal {signum}")


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            print(json.dumps(invocation_receipt({'status': 'failed', 'error': message}, None, published=False)))
            raise SystemExit(2)

    parser = Parser(description='Run one bounded native final polling pass; publish validated full-precision JSON.')
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--modpoll', default='modpoll', help='Installed native modpoll executable')
    parser.add_argument('--output-directory', help='Private output directory; default is the generated route-specific directory')
    parser.add_argument('--confirm-read', required=True, choices=['READ'])
    args = parser.parse_args()
    previous_signals = {}
    try:
        if COMPILED_SETUP is None:
            raise LauncherError("Use a generated, source-bound launcher")
        base = Path(__file__).absolute().parent
        output = Path(os.path.abspath(args.output_directory or base / COMPILED_SETUP['output_directory']))
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signals[signum] = signal.signal(signum, _interrupted)
        result = run(COMPILED_SETUP, config_directory=base,
                     output_directory=output,
                     host=args.host, port=args.port, executable=args.modpoll)
    except (OSError, ValueError) as error:
        print(json.dumps(invocation_receipt({'status': 'failed', 'error': str(error)}, None, published=False)))
        return 1
    finally:
        for signum, previous in previous_signals.items():
            signal.signal(signum, previous)
    print(json.dumps(invocation_receipt(result, output / 'result.json', published=True), allow_nan=False))
    return 0 if result['status'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
