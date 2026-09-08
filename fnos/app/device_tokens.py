"""Administrator-managed device token override, persisted independently of passwords."""
import os
from pathlib import Path
import secrets
import threading
from network_sources import SelectionError

def custom_path(data_dir):
    return Path(data_dir) / 'device-token-custom'

def validate(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 512 or any(not 33 <= ord(c) <= 126 for c in token):
        raise SelectionError('Token 需为 1–512 个英文、数字或符号，不能包含空格、中文或换行')
    if token == 'replace-with-a-long-random-token':
        raise SelectionError('请填写自定义 Token，不能使用模板占位值')
    return token

class DeviceTokens:
    def __init__(self, data_dir, initial):
        self.path = custom_path(data_dir)
        self.lock = threading.RLock()
        self.value = validate(self.path.read_text(encoding='ascii').strip() if self.path.exists() else initial)

    def current(self):
        with self.lock:
            return self.value

    def save(self, token):
        token = validate(token)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name('.device-token-' + secrets.token_hex(8))
            try:
                with open(temporary, 'x', encoding='ascii', opener=lambda p, f: os.open(p, f, 0o600)) as out:
                    out.write(token + '\n'); out.flush(); os.fsync(out.fileno())
                os.replace(temporary, self.path)
                self.value = token
                if os.name == 'posix':
                    try:
                        descriptor = os.open(self.path.parent, os.O_RDONLY)
                        try: os.fsync(descriptor)
                        finally: os.close(descriptor)
                    except OSError:
                        pass  # The atomic replacement already committed; do not report a false failure.
            finally:
                temporary.unlink(missing_ok=True)
