"""FPK lifecycle; called by fnOS, never by the public HTTP service."""
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys

APP = 'routermonitor-fnos'
VOLUME = APP + '-data'


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=180, **kwargs)


def options(old, action):
    port = int(os.environ.get('wizard_port') or old.get('port', 18199))
    if not 1024 <= port <= 65535:
        raise ValueError('端口必须为 1024–65535')
    environment = {}
    if action == 'install_init' or (action == 'upgrade_init' and (os.environ.get('wizard_password') or os.environ.get('wizard_password_confirm'))):
        password = os.environ.get('wizard_password', '')
        if not password:
            raise ValueError('管理员密码不能为空')
        if password != os.environ.get('wizard_password_confirm', ''):
            raise ValueError('两次输入的管理员密码不一致')
    return {'port': port, 'environment': environment}


def configure(action):
    target = Path(os.environ['TRIM_APPDEST'])
    etc = Path(os.environ['TRIM_PKGETC'])
    settings = etc / 'settings.json'
    old = json.loads(settings.read_text()) if settings.exists() else {}
    selected = options(old, action)
    port = selected['port']
    if action.endswith('_init') and (action == 'install_init' or port != old.get('port', 18199)):
        with socket.socket() as sock:
            try:
                sock.bind(('0.0.0.0', port))
            except OSError:
                raise ValueError('端口已占用，请换一个空闲端口；不会停止其他服务。')
    if action == 'install_init':
        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
    if action == 'config_init':
        return
    etc.mkdir(parents=True, exist_ok=True)
    if action in ('install_init', 'upgrade_init'):
        bundle = Path(__file__).parent / 'image.tar'
        expected = (Path(__file__).parent / 'image.sha256').read_text().strip()
        with bundle.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                raise ValueError('镜像校验失败，请重新下载完整 FPK')
        run(['docker', 'load', '-i', str(bundle)])
        run(['docker', 'volume', 'create', VOLUME])
        if action == 'install_init' or os.environ.get('wizard_password'):
            # Password travels through stdin, never shell text, Docker env or logs.
            code = '''import hashlib,json,os,secrets,sys
from pathlib import Path
root=Path('/data'); path=root/'admin.json'
password=json.load(sys.stdin)['password']
salt=secrets.token_hex(16)
value={'salt':salt,'hash':hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),300000).hex()}
temporary=root/('admin-'+secrets.token_hex(8)+'.tmp')
try:
 with open(temporary,'x',opener=lambda p,f:os.open(p,f,0o600)) as out:
  json.dump(value,out); out.flush(); os.fsync(out.fileno())
 os.replace(temporary,path)
 (root/'initial-admin-password.txt').unlink(missing_ok=True)
finally:
 temporary.unlink(missing_ok=True)
'''
            image = (Path(__file__).parent / 'image.tag').read_text().strip()
            run(['docker', 'run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
                 '--security-opt', 'no-new-privileges:true', '--read-only', '-v', VOLUME + ':/data',
                 '--entrypoint', 'python3', image, '-c', code],
                input=json.dumps({'password': os.environ['wizard_password']}))
        return
    environment = selected['environment']
    extra = ''.join(k + '=' + json.dumps(v.replace('$', '$$')) + '\n' for k, v in environment.items())
    compose = target / 'docker' / 'docker-compose.yaml'
    content, count = re.subn(r'command: \["--port", "(?:\$\{wizard_port:-18199\}|[0-9]+)"\]',
                            'command: ["--port", "%d"]' % port, compose.read_text())
    if count != 1:
        raise ValueError('容器端口配置格式不匹配')
    # A concrete URL works on both older and newer fnOS entry parsers.
    ui = target / 'ui' / 'config'
    config = json.loads(ui.read_text())
    config['.url'][APP + '.main']['port'] = str(port)
    files = {settings: json.dumps(selected),
             target / 'docker' / '.env': 'wizard_port=%d\n' % port + extra,
             compose: content, ui: json.dumps(config, ensure_ascii=False)}
    previous = {path: path.read_bytes() if path.exists() else None for path in files}
    restart = ['docker', 'compose', '--project-directory', str(target / 'docker'),
               '-f', str(compose), 'up', '-d', '--pull', 'never']
    try:
        (target / 'docker' / 'host-metrics').mkdir(parents=True, exist_ok=True)
        for path, value in files.items():
            path.write_text(value)
        if action == 'config_callback':
            run(restart)
        elif action == 'upgrade_callback':
            run(restart + ['--force-recreate'])
        helper = target / 'docker' / 'host_collector.py'
        if helper.exists():
            if action == 'upgrade_callback':
                run(['python3', str(helper), 'stop', str(target / 'docker' / 'host-metrics')])
            run(['python3', str(helper), 'start', str(target / 'docker' / 'host-metrics')])
    except Exception:
        for path, value in previous.items():
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(value)
        if action == 'config_callback':
            try:
                run(restart)
            except Exception:
                raise RuntimeError('新配置启动失败，已恢复原配置文件，但原服务重启失败，请在应用中心重新启动应用。')
        raise


if __name__ == '__main__':
    try:
        configure(sys.argv[1])
    except Exception as error:
        # Do not include subprocess stdin / passwords in a user-facing error.
        detail = error.stderr[-1500:] if isinstance(error, subprocess.CalledProcessError) else str(error)
        Path(os.environ['TRIM_TEMP_LOGFILE']).write_text('NAS Monitor 安装失败：' + detail)
        sys.exit(1)
