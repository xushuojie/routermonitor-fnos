#!/usr/bin/env python3
"""Build the version-adaptive UI and sign a release with the existing publisher key."""
import os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def run(*args):subprocess.run([str(x) for x in args],cwd=ROOT,check=True)
sdk=Path(os.environ.get('ANDROID_HOME',os.environ.get('ANDROID_SDK_ROOT','')))
bt=sdk/'build-tools'/os.environ.get('ANDROID_BUILD_TOOLS','35.0.1')
gradle=ROOT/('gradlew.bat' if os.name=='nt' else 'gradlew')
run(*([gradle] if os.name=='nt' else ['sh',gradle]),'--no-daemon','testDebugUnitTest','assembleRelease')
key=Path(os.environ.get('ANDROID_KEYSTORE',str(Path.home()/'.android/debug.keystore')))
if not key.exists():raise SystemExit('Set ANDROID_KEYSTORE to the existing signing key.')
os.environ.setdefault('ANDROID_KEYSTORE_PASSWORD','android')
os.environ.setdefault('ANDROID_KEY_PASSWORD',os.environ['ANDROID_KEYSTORE_PASSWORD'])
signer=bt/('apksigner.bat' if os.name=='nt' else 'apksigner')
out=ROOT/'build/nas-monitor-android.apk'
run(signer,'sign','--ks',key,'--ks-key-alias',os.environ.get('ANDROID_KEY_ALIAS','androiddebugkey'),'--ks-pass','env:ANDROID_KEYSTORE_PASSWORD','--key-pass','env:ANDROID_KEY_PASSWORD','--min-sdk-version','18','--v1-signing-enabled','true','--out',out,ROOT/'build/outputs/apk/release/NasMonitor-release-unsigned.apk')
run(signer,'verify','--verbose',out)
print(out)
