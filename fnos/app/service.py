"""Single-container lifecycle: persistent credentials and supervised collectors."""
import os
import ctypes
import stat
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time


def prepare_credentials():
    data = Path(os.environ.get('NAS_STATUS_HISTORY_DB', '/data/traffic.sqlite3')).parent
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    from device_tokens import custom_path, validate
    custom = custom_path(data)
    if custom.exists():
        validate(custom.read_text(encoding='ascii').strip())
        os.environ['NAS_STATUS_TOKEN_FILE'] = str(custom)
        return data
    explicit = os.environ.get('NAS_STATUS_TOKEN_FILE')
    token = os.environ.get('NAS_STATUS_TOKEN', '').strip()
    if explicit:
        # A missing explicit secret is a deployment error, never silently rotate it.
        token = Path(explicit).read_text().strip()
    elif not token:
        filename = data / 'device-token'
        try:
            with open(filename, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
                out.write(secrets.token_hex(32) + '\n')
        except FileExistsError:
            pass
        token = filename.read_text().strip()
        os.environ['NAS_STATUS_TOKEN_FILE'] = str(filename)
    if not token or token == 'replace-with-a-long-random-token' or len(token) > 512 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError('只读 Token 无效；请检查配置，不能使用模板占位值')
    return data


def prepare_permissions():
    """Use the actual NUT socket group, then discard startup-only CAP_SETGID."""
    if sys.platform != 'linux':
        return
    groups = set(os.getgroups())
    root = Path(os.environ.get('NUT_ROOT', '/host/nut'))
    if root.exists():
        for path in root.iterdir():
            info = path.stat()
            if stat.S_ISSOCK(info.st_mode):
                groups.add(info.st_gid)
    if groups != set(os.getgroups()):
        os.setgroups(sorted(groups))
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
    libc = ctypes.CDLL(None, use_errno=True)
    header, values = Header(0x20080522, 0), (Data * 2)()
    if libc.prctl(38, 1, 0, 0, 0) or libc.capset(ctypes.byref(header), ctypes.byref(values)):
        raise OSError(ctypes.get_errno(), '无法撤销启动权限')


def supervise(commands):
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stopping:
            if any(child.poll() is not None for child in children):
                print('采集或网页进程已退出，重启整个服务以恢复一致状态', flush=True)
                return 1
            time.sleep(.2)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for s, handler in previous.items():
            signal.signal(s, handler)


def main():
    prepare_permissions()
    prepare_credentials()
    root = Path(__file__).parent
    snapshot = Path(os.environ.setdefault('NAS_STATUS_STORAGE_SNAPSHOT', '/tmp/nas-monitor/storage.json'))
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.unlink(missing_ok=True)  # Never advertise a snapshot from a previous run.
    return supervise([[sys.executable, '-u', str(root / 'storage_discovery.py')],
                      [sys.executable, '-u', str(root / 'api.py'), *sys.argv[1:]]])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('服务初始化失败：' + str(error), file=sys.stderr)
        sys.exit(1)
