import ast,hashlib,io,json,tarfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def files(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return {m.name:t.extractfile(m).read() for m in t if m.isfile()}
path=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT.parent/'downloads/routermonitor-fnos-1.3.0-5-amd64.fpk';p=files(path.read_bytes())
assert hashlib.md5(p['app.tgz']).hexdigest() in p['manifest'].decode()
assert hashlib.sha256(p['cmd/image.tar']).hexdigest()==p['cmd/image.sha256'].decode().strip()
app=files(p['app.tgz']);assert b'routermonitor-fnos:1.3.0-5' in app['docker/docker-compose.yaml']
assert b'./host-metrics:/host/monitor:ro' in app['docker/docker-compose.yaml']
assert app['docker/host_collector.py']==(ROOT/'docker/host_collector.py').read_bytes()
im=files(p['cmd/image.tar'])
for name,data in im.items():
    if name.startswith('blobs/sha256/'):assert hashlib.sha256(data).hexdigest()==name.split('/')[-1]
def resolve(desc):
    data=im['blobs/sha256/'+desc['digest'].split(':')[1]];assert len(data)==desc['size'];return data
index=json.loads(im['index.json']);oci=json.loads(resolve(index['manifests'][0]));cfg=json.loads(resolve(oci['config']))
assert cfg['architecture']=='amd64'
assert len(oci['layers'])==len(cfg['rootfs']['diff_ids'])
for desc,diff in zip(oci['layers'],cfg['rootfs']['diff_ids']):assert 'sha256:'+hashlib.sha256(resolve(desc)).hexdigest()==diff
mf=json.loads(im['manifest.json']);assert mf[0]['RepoTags']==['routermonitor-fnos:1.3.0-5']
layer=files(resolve(oci['layers'][-1]))
for file in (ROOT/'app').rglob('*'):
    if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc':
        assert layer[file.relative_to(ROOT).as_posix()]==file.read_bytes()
for name,data in {**layer,**p,**app}.items():
    if name.endswith('.py'):ast.parse(data,feature_version=(3,11))
for name in ('install','upgrade','config'):
    wizard=json.loads(p['wizard/'+name])
    for step in wizard:
        for item in step['items']:
            assert item.get('field') not in ('wizard_access_mode','wizard_public_origin','wizard_trusted_proxies')
            if item.get('field','').startswith('wizard_password'):assert not any('min' in r or 'max' in r for r in item.get('rules',[]))
with tarfile.open(path) as t:
    for m in t:
        if m.name.startswith('cmd/') and not m.name.endswith(('.py','.tag','.sha256','.tar')):assert m.mode&0o111,m.name
print('PASS: FPK checksum, OCI hashes and sizes, amd64 tag, compose/helper, Python 3.11 syntax, source contents, wizard and executable modes')
