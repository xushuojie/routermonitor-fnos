"""Reproducible overlay rebuild of the user's original offline FPK chain."""
import copy, hashlib, io, json, re, tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT.parent/"dist"
VERSION='1.3.0-5'
def unpack(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return {m.name:t.extractfile(m).read() for m in t if m.isfile()}
def pack(data,changes):
    output=io.BytesIO(); changes=dict(changes)
    with tarfile.open(fileobj=io.BytesIO(data)) as src,tarfile.open(fileobj=output,mode='w:gz' if data[:2]==b'\x1f\x8b' else 'w') as dst:
        for member in src:
            m=copy.copy(member)
            if m.name in changes:
                payload=changes.pop(m.name);m.size=len(payload);dst.addfile(m,io.BytesIO(payload))
            else:dst.addfile(m,src.extractfile(member) if member.isfile() else None)
        for name,payload in changes.items():
            m=tarfile.TarInfo(name);m.size=len(payload);m.mode=0o644;dst.addfile(m,io.BytesIO(payload))
    return output.getvalue()
def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
def build():
    source=(ROOT.parent/'downloads/routermonitor-fnos-1.3.0-5-amd64.fpk').read_bytes();p=unpack(source);im=unpack(p['cmd/image.tar'])
    manifest=json.loads(im['manifest.json']);index=json.loads(im['index.json']);oci=json.loads(im['blobs/sha256/'+index['manifests'][0]['digest'].split(':')[1]])
    config=json.loads(im[manifest[0]['Config']]);changes={}
    def blob(data,media):
        digest=hashlib.sha256(data).hexdigest();changes['blobs/sha256/'+digest]=data
        return {'digest':'sha256:'+digest,'size':len(data),'mediaType':media}
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w') as t:
        for path in sorted((ROOT/'app').rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix=='.pyc':continue
            payload=path.read_bytes()
            if path.suffix=='.py':compile(payload,str(path),'exec')
            m=tarfile.TarInfo(path.relative_to(ROOT).as_posix());m.size=len(payload);m.mode=0o644;t.addfile(m,io.BytesIO(payload))
    layer=blob(buf.getvalue(),'application/vnd.oci.image.layer.v1.tar');config['rootfs']['diff_ids'].append(layer['digest'])
    config['history'].append({'created_by':'Two-page hardware monitor with persistent hardware selection'})
    cfg=blob(dump(config),oci['config']['mediaType']);oci['config']=cfg;oci['layers'].append(layer)
    index['manifests'][0].update(blob(dump(oci),oci['mediaType']))
    tag='routermonitor-fnos:'+VERSION
    index['manifests'][0]['annotations']={'io.containerd.image.name':'docker.io/library/'+tag,'org.opencontainers.image.ref.name':VERSION}
    manifest[0]['Config']='blobs/sha256/'+cfg['digest'].split(':')[1];manifest[0]['RepoTags']=[tag]
    manifest[0]['Layers'].append('blobs/sha256/'+layer['digest'].split(':')[1]);manifest[0]['LayerSources'][layer['digest']]=layer
    changes.update({'index.json':dump(index),'manifest.json':dump(manifest),'repositories':dump({'routermonitor-fnos':{VERSION:layer['digest'].split(':')[1]}})})
    image=pack(p['cmd/image.tar'],changes)
    changes={'cmd/image.tar':image,'cmd/image.tag':(tag+'\n').encode(),'cmd/image.sha256':(hashlib.sha256(image).hexdigest()+'\n').encode()}
    for path in (ROOT/'cmd').iterdir():
        if path.is_file():changes['cmd/'+path.name]=path.read_bytes()
    for name in ('install', 'upgrade', 'config'):
        wizard=json.loads(p['wizard/'+name])
        for step in wizard:
            for item in step['items']:
                if item.get('helpText','').startswith('网卡、数据卷和可用传感器自动发现'):
                    item['helpText']='安装后进入“设置”选择存储卷、网卡、温度、GPU、UPS 和采集频率。修改端口后，安卓和 ESP8266 的服务地址也需同步修改。升级端口留空保留当前值。'
        changes['wizard/'+name]=dump(wizard)
    appchanges={'docker/host-metrics/.keep':b''}
    for path in (ROOT/'docker').iterdir():
        if path.is_file():appchanges['docker/'+path.name]=path.read_bytes()
    appchanges['docker/docker-compose.yaml']=appchanges['docker/docker-compose.yaml'].replace(b'routermonitor-fnos:1.2.0-6',tag.encode())
    app=pack(p['app.tgz'],appchanges);changes['app.tgz']=app
    meta=p['manifest'].decode().replace('1.2.0-6',VERSION)
    meta=re.sub(r'checksum\s*=\s*\w+','checksum              = '+hashlib.md5(app).hexdigest(),meta);changes['manifest']=meta.encode()
    OUTPUT.mkdir(exist_ok=True)
    out=OUTPUT/('routermonitor-fnos-'+VERSION+'-amd64.fpk');out.write_bytes(pack(source,changes))
    out.with_suffix('.fpk.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest()+'  '+out.name+'\n')
    print(out)
if __name__=='__main__':build()
