"""Private RU egress mesh. Invoked over authenticated SSH; never prints secrets."""
import json,os,subprocess,sys,ipaddress
from pathlib import Path
os.umask(0o077)
def run(*args,**kwargs):return subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
def write(path,text,mode=0o600):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.new');t.write_text(text);t.chmod(mode);t.replace(p)
def options(p):
    generation=int(p.get('generation',0))
    if not 0<=generation<=254:raise ValueError('invalid_generation')
    suffix=str(generation) if generation else ''
    return {'generation':generation,'interface':'wg-ru'+suffix,'key':'/etc/wireguard/kingline-ru'+suffix+'.key',
        'ru_ip':f'10.78.{generation}.1','management_ip':f'10.79.{generation}.2',
        'control_interface':'wg-ruc'+suffix if generation else 'wg-ru-control',
        'directory':'/opt/kingline-ru-node'+('-'+suffix if suffix else ''),
        'container':'kingline-ru-remnanode'+('-'+suffix if suffix else ''),
        'service':'kingline-ru-node'+('-'+suffix if suffix else ''),
        'firewall':'kingline-ru-firewall'+('-'+suffix if suffix else ''),
        'table':'kingline_ru_service'+suffix}
def key(payload=None):
    p=Path(options(payload or {})['key']);p.parent.mkdir(mode=0o700,exist_ok=True)
    if not p.exists():write(p,subprocess.check_output(['wg','genkey']).decode())
    return subprocess.check_output(['wg','pubkey'],input=p.read_bytes()).decode().strip()
def mesh(p):
    o=options(p);public=key(p);private=Path(o['key']).read_text().strip()
    address=ipaddress.IPv4Address(p['address']);assert address in ipaddress.IPv4Network('10.78.'+str(o['generation'])+'.0/24')
    port=int(p.get('listen_port',51821+o['generation']));assert 1024<=port<=65535
    config=f'[Interface]\nAddress = {p["address"]}/24\nListenPort = {port}\nMTU = 1380\nPrivateKey = {private}\n'
    for peer in p['peers']:
        config+='\n[Peer]\nPublicKey = '+peer['public_key']+'\nAllowedIPs = '+peer['address']+'/32\n'
        if p['role']=='pi':config+='Endpoint = '+peer.get('endpoint_ip',peer['public_ip'])+':'+str(peer.get('endpoint_port',peer.get('port',51821)))+'\nPersistentKeepalive = 25\n'
    write('/etc/wireguard/'+o['interface']+'.conf',config)
    if p['role']=='head':
        f=Path('/etc/kingline-firewall.nft');old=f.read_text()
        marker='kingline-ru'+(str(o['generation']) if o['generation'] else '')+'-mesh-'+str(port)
        if marker not in old:
            backup=Path('/opt/kingline-ru/old-firewall.nft');backup.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if not backup.exists():write(backup,old)
            new=old.replace('iifname "lo" accept','iifname "lo" accept\n  udp dport '+str(port)+' accept comment "'+marker+'"',1)
            assert new!=old,'unknown_firewall'
            temp=f.with_suffix('.ru-new');write(temp,new)
            # Validate a replacement transaction, including removal of named
            # meter sets in the old table. Never flush unrelated nftables.
            run('nft','-c','-f','-',input=('delete table inet kingline_filter\n'+new).encode())
            temp.replace(f);run('/usr/local/sbin/kingline-firewall')
    if p['role']=='head' and p.get('release_direct'):
        subprocess.run(['systemctl','disable','--now','kingline-ru-direct'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['ip','route','del','unreachable',o['ru_ip']+'/32'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        Path('/etc/systemd/system/kingline-ru-direct.service').unlink(missing_ok=True)
    if o['interface'] not in subprocess.check_output(['wg','show','interfaces'],text=True).split():run('systemctl','enable','--now','wg-quick@'+o['interface'])
    else:
        stripped=subprocess.check_output(['wg-quick','strip',o['interface']]);run('wg','syncconf',o['interface'],'/dev/stdin',input=stripped)
    return {'public_key':public,'address':p['address']}
def container(p):
    import shutil
    o=options(p)
    if not shutil.which('nft'):
        run('apt-get','-y','install','nftables',timeout=240)
    root=Path(o['directory']);root.mkdir(mode=0o700,exist_ok=True)
    assert '\n' not in p['secret'] and '\r' not in p['secret']
    write(root/'.env','NODE_PORT=2222\nSECRET_KEY='+p['secret']+'\n')
    write(root/'compose.yml',f'''services:
  remnanode:
    image: {p['image']}
    container_name: {o["container"]}
    hostname: {o["container"]}
    restart: always
    env_file: .env
    ports:
      - "{o["management_ip"]}:2222:2222/tcp"
      - "{o["ru_ip"]}:15443:15443/tcp"
    mem_limit: 512m
    pids_limit: 256
    ulimits:
      nofile: {{soft: 65536, hard: 65536}}
    logging:
      driver: local
      options: {{max-size: "5m", max-file: "2"}}
''')
    rules='''table inet kingline_ru_service {
 chain ingress {
  type filter hook prerouting priority -150; policy accept;
  iifname "lo" accept
  iifname "wg-ru-control" ip saddr 10.79.0.1 tcp dport 2222 accept
  iifname "wg-ru" ip saddr 10.78.0.0/24 tcp dport 15443 accept
  ip daddr 10.79.0.2 tcp dport 2222 drop
  ip daddr 10.78.0.1 tcp dport 15443 drop
 }
}
'''
    rules=rules.replace('kingline_ru_service',o['table']).replace('wg-ru-control',o['control_interface']).replace('iifname \"wg-ru\"','iifname \"'+o['interface']+'\"').replace('10.78.0.0/24','10.78.'+str(o['generation'])+'.0/24').replace('10.79.0.2',o['management_ip']).replace('10.78.0.1',o['ru_ip'])
    firewall_file='/etc/'+o['firewall']+'.nft'
    write(firewall_file,rules)
    firewall_script='''#!/bin/sh
set -eu
f=$(mktemp)
trap 'rm -f "$f"' EXIT
if nft list table inet kingline_ru_service >/dev/null 2>&1; then printf 'delete table inet kingline_ru_service\n' > "$f"; fi
cat /etc/kingline-ru-service.nft >> "$f"
nft -c -f "$f"
nft -f "$f"
'''
    firewall_script=firewall_script.replace('kingline_ru_service',o['table']).replace('/etc/kingline-ru-service.nft',firewall_file)
    write('/usr/local/sbin/'+o['firewall'],firewall_script,0o700)
    firewall_unit='''[Unit]
Description=Private RU node port restrictions
After=network-pre.target
Before=kingline-ru-node.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/kingline-ru-firewall
[Install]
WantedBy=multi-user.target
'''
    firewall_unit=firewall_unit.replace('kingline-ru-firewall',o['firewall']).replace('kingline-ru-node',o['service'])
    write('/etc/systemd/system/'+o['firewall']+'.service',firewall_unit,0o644)
    node_unit='''[Unit]
Description=Parallel Kingline RU Remnawave node
Requires=docker.service wg-quick@wg-ru.service wg-quick@wg-ru-control.service kingline-ru-firewall.service
After=docker.service wg-quick@wg-ru.service wg-quick@wg-ru-control.service kingline-ru-firewall.service
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/kingline-ru-node
ExecStart=/usr/bin/docker compose -f compose.yml up -d
ExecStop=/usr/bin/docker compose -f compose.yml stop
[Install]
WantedBy=multi-user.target
'''
    node_unit=node_unit.replace('wg-ru-control',o['control_interface']).replace('wg-quick@wg-ru.service','wg-quick@'+o['interface']+'.service').replace('kingline-ru-firewall',o['firewall']).replace('/opt/kingline-ru-node',o['directory'])
    write('/etc/systemd/system/'+o['service']+'.service',node_unit,0o644)
    run('systemctl','daemon-reload');run('systemctl','enable','--now',o['firewall'])
    run('docker','compose','-f',str(root/'compose.yml'),'pull',cwd=root,timeout=600)
    run('systemctl','enable','--now',o['service'],timeout=180)
    return {'ready':True}
def probe(p):
    o=options(p)
    import socket,tempfile,time,struct,secrets
    pid=subprocess.check_output(['docker','inspect','remnanode','--format','{{.State.Pid}}'],text=True).strip()
    binary=subprocess.check_output(['docker','exec','remnanode','sh','-c','command -v xray'],text=True).strip()
    executable='/proc/'+pid+'/root'+binary
    with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    config={'log':{'loglevel':'none'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'udp':True}}],
        'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':o['ru_ip'],'port':15443,'users':[{'id':p['vless_uuid'],'encryption':'none'}]}]},'streamSettings':{'network':'tcp','security':'none'}}]}
    with tempfile.TemporaryDirectory(prefix='ru-canary-') as folder:
        f=Path(folder)/'client.json';write(f,json.dumps(config));process=subprocess.Popen([executable,'run','-config',str(f)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(40):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.1)
            result=subprocess.check_output(['curl','-4fsS','--max-time','20','--proxy','socks5h://127.0.0.1:'+str(port),'https://api.ipify.org'],stderr=subprocess.DEVNULL).decode().strip()
            assert result==p['expected_ip'],'ru_exit_mismatch'
            with socket.create_connection(('127.0.0.1',port),timeout=5) as tcp:
                tcp.settimeout(10);tcp.sendall(b'\x05\x01\x00');assert tcp.recv(2)==b'\x05\x00'
                tcp.sendall(b'\x05\x03\x00\x01'+b'\x00'*6);reply=tcp.recv(32);assert reply[1]==0
                tid=secrets.randbelow(65536);query=struct.pack('!HHHHHH',tid,256,1,0,0,0)+b'\x07example\x03com\x00\x00\x01\x00\x01'
                packet=b'\x00\x00\x00\x01'+socket.inet_aton('9.9.9.9')+struct.pack('!H',53)+query
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                    udp.settimeout(10);udp.sendto(packet,('127.0.0.1',int.from_bytes(reply[8:10],'big')));answer=udp.recv(4096)[10:]
                assert struct.unpack('!H',answer[:2])[0]==tid and not struct.unpack('!H',answer[2:4])[0]&15
            return {'https':True,'udp_dns':True,'exit_ip':result}
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
if __name__=='__main__':
    p=json.load(sys.stdin);mode=p['mode']
    if mode=='key':result={'public_key':key(p)}
    elif mode=='mesh':result=mesh(p)
    elif mode=='container':result=container(p)
    elif mode=='probe':result=probe(p)
    elif mode in ('remove','direct'):
        o=options(p)
        if mode=='direct':run('ip','route','replace','unreachable',o['ru_ip']+'/32')
        subprocess.run(['systemctl','disable','--now','wg-quick@'+o['interface']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for f in ['/etc/wireguard/'+o['interface']+'.conf',o['key']]:Path(f).unlink(missing_ok=True)
        firewall=Path('/etc/kingline-firewall.nft')
        marker='kingline-ru'+(str(o['generation']) if o['generation'] else '')+'-mesh-'
        if firewall.exists():
            write(firewall,'\n'.join(line for line in firewall.read_text().splitlines() if marker not in line)+'\n');run('/usr/local/sbin/kingline-firewall')
        if mode=='direct':
            write('/etc/systemd/system/kingline-ru-direct.service',f"""[Unit]
Description=Keep Russian domains on the ordinary location exit
After=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ip route replace unreachable {o['ru_ip']}/32
[Install]
WantedBy=multi-user.target
""",0o644)
            run('systemctl','daemon-reload');run('systemctl','enable','kingline-ru-direct')
            run('systemctl','restart','kingline-ru-direct')
        elif p.get('remove_direct'):
            subprocess.run(['systemctl','disable','--now','kingline-ru-direct'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['ip','route','del','unreachable',o['ru_ip']+'/32'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            Path('/etc/systemd/system/kingline-ru-direct.service').unlink(missing_ok=True)
        result={'removed':True,'direct':mode=='direct'}
    else:raise ValueError('invalid_mode')
    print(json.dumps(result))
