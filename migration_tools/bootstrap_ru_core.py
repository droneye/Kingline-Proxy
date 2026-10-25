"""Core-side isolated management peer for the explicitly requested Raspberry node."""
import os,subprocess
from pathlib import Path
os.umask(0o077)
root=Path('/opt/kingline-control/ru-egress-backups/before-20261023');root.mkdir(parents=True,exist_ok=True)
firewall=Path('/etc/kingline-firewall.nft');text=firewall.read_text()
backup=root/'core-firewall.nft'
if not backup.exists():backup.write_text(text)
if 'kingline-ru-control' not in text:
    text=text.replace('        iifname "lo" accept','        iifname "lo" accept\n        udp dport 51821 accept comment "kingline-ru-control"',1)
    assert 'kingline-ru-control' in text
    temp=firewall.with_suffix('.ru-new');temp.write_text(text)
    subprocess.run(['nft','-c','-f',str(temp)],check=True)
    temp.replace(firewall)
    subprocess.run(['/usr/local/sbin/kingline-firewall'],check=True)
key=Path('/etc/wireguard/ru-control.key').read_text().strip()
conf=Path('/etc/wireguard/wg-ru-control.conf')
assert not conf.exists(),'already_bootstrapped'
conf.write_text('[Interface]\nAddress = 10.79.0.1/30\nListenPort = 51821\nMTU = 1380\nPrivateKey = '+key+'\n\n[Peer]\nPublicKey = rUhsuZjCxEjrOwh8yrOrBMn9868465Qy6EMg+I3gNUQ=\nAllowedIPs = 10.79.0.2/32\n');conf.chmod(0o600)
subprocess.run(['systemctl','enable','--now','wg-quick@wg-ru-control'],check=True)
print('Isolated Raspberry management interface ready')
