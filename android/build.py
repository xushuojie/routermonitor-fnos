#!/usr/bin/env python3
"""Build a dependency-free Android 4.3 APK with Android SDK tools and a JDK."""
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
SDK = Path(os.environ.get('ANDROID_HOME', '/opt/homebrew/share/android-commandlinetools'))
JAVA = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk')) / 'bin'
BT = SDK / 'build-tools' / os.environ.get('ANDROID_BUILD_TOOLS', '35.0.1')
JAR = SDK / 'platforms' / os.environ.get('ANDROID_PLATFORM', 'android-37.0') / 'android.jar'
OUT = ROOT / 'build'

def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)

def main():
    if not JAR.exists():
        raise SystemExit('Install platforms;android-37.0 and build-tools;35.0.1 with sdkmanager first.')
    OUT.mkdir(exist_ok=True)
    for name in ('classes', 'dex', 'test'):
        target = OUT / name
        if target.exists(): shutil.rmtree(target)
        target.mkdir()
    sources = sorted((ROOT / 'src').rglob('*.java'))
    run(JAVA/'javac', '--release', '8', '-encoding', 'UTF-8', '-classpath', JAR, '-d', OUT/'classes', *sources)
    run(JAVA/'javac', '--release', '8', '-encoding', 'UTF-8', '-d', OUT/'test', ROOT/'src/io/github/routermonitor/fnos/DisplayMath.java', ROOT/'src/io/github/routermonitor/fnos/DisplayLayout.java', *sorted((ROOT/'test').rglob('*.java')))
    run(JAVA/'java', '-cp', OUT/'test', 'io.github.routermonitor.fnos.DisplayMathTest')
    env = dict(os.environ, JAVA_HOME=str(JAVA.parent))
    run(BT/'d8', '--min-api', '18', '--lib', JAR, '--output', OUT/'dex', *sorted((OUT/'classes').rglob('*.class')), env=env)
    raw = OUT/'unsigned.apk'
    run(BT/'aapt', 'package', '-f', '-M', ROOT/'AndroidManifest.xml', '-I', JAR, '-F', raw)
    with zipfile.ZipFile(raw, 'a', zipfile.ZIP_DEFLATED) as z:
        z.write(OUT/'dex/classes.dex', 'classes.dex')
    run(BT/'zipalign', '-f', '4', raw, OUT/'aligned.apk')
    keystore = Path(os.environ.get('ANDROID_KEYSTORE', str(Path.home()/'.android/debug.keystore')))
    alias = os.environ.get('ANDROID_KEY_ALIAS', 'androiddebugkey')
    if not keystore.exists():
        raise SystemExit('Set ANDROID_KEYSTORE and ANDROID_KEY_ALIAS to an existing signing key.')
    env.setdefault('ANDROID_KEYSTORE_PASSWORD', 'android')
    env.setdefault('ANDROID_KEY_PASSWORD', env['ANDROID_KEYSTORE_PASSWORD'])
    apk = OUT/'nas-monitor-android.apk'
    run(BT/'apksigner', 'sign', '--ks', keystore, '--ks-key-alias', alias, '--ks-pass', 'env:ANDROID_KEYSTORE_PASSWORD', '--key-pass', 'env:ANDROID_KEY_PASSWORD', '--min-sdk-version', '18', '--v1-signing-enabled', 'true', '--out', apk, OUT/'aligned.apk', env=env)
    run(BT/'apksigner', 'verify', '--verbose', '--min-sdk-version', '18', apk, env=env)
    print(apk)

if __name__ == '__main__':
    main()
