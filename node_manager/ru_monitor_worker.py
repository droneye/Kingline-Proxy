"""Raspberry-only active probe. Standard library + curl + a pinned local Xray binary."""
import concurrent.futures,fcntl,hashlib,ipaddress,json,os,re,selectors,socket,subprocess,tempfile,time,uuid
from pathlib import Path
ROOT=Path('/opt/kingline-ru-monitor')

def atomic(path,body):
    file=Path(path);temporary=file.with_suffix('.new');temporary.write_text(json.dumps(body));temporary.chmod(0o600);temporary.replace(file)

def transfer(url,port,size,expected_sha=None,*,small=False,resolve=None):
    command=['curl','--silent','--show-error','--fail','--noproxy','', '--connect-timeout','8','--max-time','15' if small else '55', '--speed-limit','1024','--speed-time','12']
    if port:command+=['--proxy','socks5h://127.0.0.1:'+str(port)]
    if resolve:command+=['--resolve',resolve]
    if small:command+=['--range','0-2047']
    else:command+=['--limit-rate','32k']
    command+=[url]
    start=time.monotonic();first=None;last=None;received=0;checksum=hashlib.sha256()
    with tempfile.TemporaryFile() as errors,tempfile.NamedTemporaryFile() as headers:
        command[1:1]=['--dump-header',headers.name]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors);os.set_blocking(child.stdout.fileno(),False)
        selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic()-start>60:child.kill();break
                events=selector.select(.5)
                if events:
                    data=os.read(child.stdout.fileno(),65536)
                    if not data:break
                    if first is None:first=time.monotonic()-start
                    received+=len(data);last=time.monotonic()-start;checksum.update(data)
                    if received>size:child.kill();break
                elif child.poll() is not None:break
            code=child.wait(timeout=3)
        finally:
            selector.close();child.stdout.close()
            if child.poll() is None:child.kill();child.wait()
        errors.seek(0);diagnostic=errors.read(4096).decode(errors='replace')
        headers.seek(0);metadata=headers.read(8192).decode(errors='replace');match=re.search(r'^X-Kingline-Probe-IP:\s*([^\r\n]+)',metadata,re.I|re.M)
        try:source_ip=str(ipaddress.IPv4Address(match.group(1).strip())) if match else None
        except ValueError:source_ip=None
    ok=code==0 and received==size and (not expected_sha or checksum.hexdigest()==expected_sha)
    error=None if ok else ('reset' if 'reset' in diagnostic.lower() else 'timeout' if code==28 else 'tls' if code in (35,51,60) else 'short_transfer' if received else 'connect')
    return {'ok':ok,'source_ip':source_ip,'bytes':received,'duration_ms':round((time.monotonic()-start)*1000),'first_byte_ms':round(first*1000) if first is not None else None,'last_progress_ms':round(last*1000) if last is not None else None,'error':error,'curl_code':code}

def client_config(target,port):
    config={'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'auth':'noauth','udp':True}}],
      'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':target['address'],'port':target['port'],'users':[{'id':target['probe_uuid'],'encryption':'none','flow':'xtls-rprx-vision'}]}]},'streamSettings':{'network':'tcp','security':'reality','realitySettings':{'serverName':target['sni'],'fingerprint':target['fingerprint'],'publicKey':target['public_key'],'shortId':target['short_id']}}}]}
    if target.get('transport')=='xhttp':
        outbound=config['outbounds'][0];outbound['settings']['vnext'][0]['users'][0].pop('flow',None)
        outbound['streamSettings']={'network':'xhttp','security':'tls','tlsSettings':{'serverName':target['sni'],'fingerprint':target['fingerprint'],'alpn':['h2']},'xhttpSettings':{'path':target['xhttp_path'],'mode':'auto'}}
    return config

def attempt(target,manifest,binary=None):
    start=time.monotonic()
    try:
        with socket.create_connection((target['address'],target['port']),timeout=5):pass
        tcp={'ok':True,'ms':round((time.monotonic()-start)*1000)}
    except OSError:return {'ok':False,'tcp':{'ok':False},'error':'tcp_unreachable'}
    with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='ru-monitor-') as folder,tempfile.TemporaryFile() as errors:
        file=Path(folder)/'client.json';file.write_text(json.dumps(client_config(target,port)));file.chmod(0o600)
        child=subprocess.Popen([str(binary or ROOT/'xray'),'run','-config',str(file)],stdout=errors,stderr=errors)
        try:
            for _ in range(50):
                if child.poll() is not None:return {'ok':False,'tcp':tcp,'error':'probe_engine_failed'}
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                except OSError:time.sleep(.1)
            small=transfer(manifest['sample_url'],port,2048,small=True)
            large=transfer(manifest['sample_url'],port,manifest['sample_bytes'],manifest['sample_sha256']) if small['ok'] else None
            result={'ok':bool(small['ok'] and large and large['ok']),'tcp':tcp,'small':small,'large':large}
            if result['ok'] and large.get('source_ip')!=target['ip']:return {**result,'ok':False,'error':'unexpected_exit'}
            # Raw engine output never leaves the probe; only fixed diagnostic codes.
            errors.seek(0);log=errors.read(16384).decode(errors='replace').lower()
            result['error']=None if result['ok'] else 'reality_rejected' if 'received real certificate' in log else 'reset' if 'connection reset' in log else (large or small).get('error','tunnel_failed')
            return result
        finally:
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait()

def check(target,manifest,confirm=False):
    attempts=[]
    for index in range(3):
        attempts.append(attempt(target,manifest))
        if index==0 and attempts[0]['ok'] and not confirm:break
        if len(attempts)>=2 and all(a['ok'] for a in attempts[-2:]):break
        if index<2:time.sleep(4)
    passed=sum(bool(a['ok']) for a in attempts)
    return {'node':target['id'],'revision':target['revision'],'ok':passed>=2 if len(attempts)>1 else bool(passed),'attempts':attempts,'at':time.time()}

def main():
    os.umask(0o077);state=ROOT/'state'
    with (state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        manifest=json.loads((ROOT/'manifest.json').read_text());requests=[]
        for file in sorted((state/'requests').glob('*.json'))[:100]:
            try:body=json.loads(file.read_text());requests.append(body);file.unlink()
            except (OSError,ValueError):continue
        previous=json.loads((state/'report.json').read_text()) if (state/'report.json').exists() else {}
        full=not requests or time.time()-previous.get('last_full_at',0)>=3300
        wanted=set(manifest['entry_ids'] if full else [])|{r.get('node') for r in requests if r.get('node')}
        if any(not r.get('node') for r in requests):wanted=set(manifest['entry_ids']);full=True
        targets=[t for t in manifest['targets'] if t['id'] in wanted]
        status_file=state/'node-status.json';status=json.loads(status_file.read_text()) if status_file.exists() else {}
        started=time.time()
        atomic(state/'inflight.json',{'requests':[r['id'] for r in requests],'manifest_revision':manifest['revision']})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda t:check(t,manifest,confirm=status.get(t['id'],{}).get('ok') is False),targets))
        # Pin the owned reference host: do not let home FakeDNS route the control through an old VPN.
        control=transfer(manifest['sample_url'],None,2048,small=True,resolve=manifest['sample_resolve'])
        clock=subprocess.run(['timedatectl','show','-p','NTPSynchronized','--value'],capture_output=True,text=True).stdout.strip()=='yes'
        report={'id':str(uuid.uuid4()),'manifest_revision':manifest['revision'],'started_at':started,'at':time.time(),'full':full,'last_full_at':started if full else previous.get('last_full_at',0),'requests':[r['id'] for r in requests], 'control':{'reference_https':control['ok'],'source_ip':control.get('source_ip'),'clock_synced':clock},'nodes':results}
        atomic(state/'report.json',report)
        for result in results:status[result['node']]={'ok':result['ok'],'at':result['at']}
        atomic(status_file,{k:v for k,v in status.items() if k in {t['id'] for t in manifest['targets']}})
        file=state/'processed.json';processed=json.loads(file.read_text()) if file.exists() else {}
        for request in requests:processed[request['id']]={'revision':manifest['revision'],'at':time.time()}
        processed={k:v for k,v in processed.items() if time.time()-v['at']<7*86400}
        atomic(file,dict(sorted(processed.items(),key=lambda item:item[1]['at'])[-500:]))
        (state/'inflight.json').unlink(missing_ok=True)
if __name__=='__main__':main()
