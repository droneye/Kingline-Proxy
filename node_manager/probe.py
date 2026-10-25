"""Real tunnel verification with a bounded, private, temporary client process."""
import copy
import json
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import tempfile
import time

from .ssh import SafeError

def probe_identity(control, state):
    # Dedicated service identity keeps synthetic health traffic out of users'
    # online status, locations and device statistics. Legacy setups still work.
    path=Path(control)/'probe-user.json'
    if not path.exists():return state['user']['vlessUuid']
    try:
        import uuid
        return str(uuid.UUID(json.loads(path.read_text())['vlessUuid']))
    except (OSError,ValueError,KeyError,TypeError):
        raise SafeError('probe_identity_invalid') from None


def udp_dns(port, resolver='9.9.9.9'):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as tcp:
        tcp.settimeout(10)
        tcp.sendall(b'\x05\x01\x00')
        if tcp.recv(2) != b'\x05\x00':
            raise SafeError('probe_socks_failed')
        tcp.sendall(b'\x05\x03\x00\x01' + b'\x00' * 6)
        reply = tcp.recv(32)
        if len(reply) < 10 or reply[1] != 0 or reply[3] != 1:
            raise SafeError('probe_udp_association_failed')
        transaction = secrets.randbelow(65536)
        query = struct.pack('!HHHHHH', transaction, 0x0100, 1, 0, 0, 0)
        query += b'\x07example\x03com\x00\x00\x01\x00\x01'
        packet = b'\x00\x00\x00\x01' + socket.inet_aton(resolver) + struct.pack('!H', 53) + query
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(10)
            udp.sendto(packet, ('127.0.0.1', int.from_bytes(reply[8:10], 'big')))
            response = udp.recv(4096)[10:]
        if len(response) < 4:
            raise SafeError('probe_udp_short_response')
        received, flags = struct.unpack('!HH', response[:4])
        if received != transaction or not flags & 0x8000 or flags & 15:
            raise SafeError('probe_udp_failed')


def verify_tunnel(control, address, *, state_override=None, connect_address=None, server_port=None,
                  exit_urls=('https://api.ipify.org',), dns_resolvers=('9.9.9.9',),expected_exit_ip=None,
                  transport='raw', xhttp_path=None):
    if not exit_urls or not dns_resolvers:
        raise ValueError('Both HTTPS and UDP checks are required')
    control = Path(control)
    state = state_override or json.loads((control / 'owner-profile.json').read_text())
    if state_override is None and (control/'node-paths.json').exists():
        selected=json.loads((control/'node-paths.json').read_text()).get(address,{})
        state={**state,'sni':selected.get('sni',state['sni'])}
        if connect_address is None:connect_address=selected.get('connect_address')
        if server_port is None:server_port=selected.get('server_port',443)
    server_port=server_port or 443
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    config = {'log': {'loglevel': 'none'},
        'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks',
                      'settings': {'auth': 'noauth', 'udp': True}}],
        'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [{
            'address': connect_address or address, 'port': server_port, 'users': [{'id': probe_identity(control,state),
                'encryption': 'none', 'flow': 'xtls-rprx-vision'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                'serverName': state['sni'], 'fingerprint': state['fingerprint'],
                'publicKey': state['reality']['publicKey'], 'shortId': state['short_id']}}}]}
    if transport=='xhttp':
        if not xhttp_path:raise ValueError('xhttp_path_required')
        outbound=config['outbounds'][0]
        outbound['settings']['vnext'][0]['users'][0].pop('flow',None)
        outbound['streamSettings']={'network':'xhttp','security':'tls',
            'tlsSettings':{'serverName':state['sni'],'fingerprint':state['fingerprint'],'alpn':['h2']},
            'xhttpSettings':{'path':xhttp_path,'mode':'auto'}}
    elif transport!='raw':raise ValueError('invalid_transport')
    with tempfile.TemporaryDirectory(prefix='node-canary-', dir=control) as folder:
        path = Path(folder) / 'client.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        process = subprocess.Popen([str(control / 'client-26.6.27/xray'), 'run', '-config', str(path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise SafeError('probe_client_start_failed')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            start = time.monotonic()
            https_ok = False
            for url in exit_urls:
                result = subprocess.run(['curl', '-fsS', '--noproxy', '', '--max-time', '25',
                                         '--proxy', f'socks5h://127.0.0.1:{port}', url],
                                        capture_output=True, timeout=30)
                value=result.stdout.decode().strip().strip('"')
                if not result.returncode and value == (expected_exit_ip or address):
                    https_ok = True
                    break
            if not https_ok:
                raise SafeError('encrypted_https_exit_check_failed')
            https_done = time.monotonic()
            for index, resolver in enumerate(dns_resolvers):
                try:
                    udp_dns(port, resolver)
                    break
                except (OSError, SafeError):
                    if index == len(dns_resolvers) - 1:
                        raise
            return {'https': True, 'udp_dns': True, 'exit_ip': expected_exit_ip or address,
                    'https_ms': round((https_done - start) * 1000, 1),
                    'udp_dns_ms': round((time.monotonic() - https_done) * 1000, 1),
                    'elapsed_ms': round((time.monotonic() - start) * 1000, 1), 'source': 'LT-control'}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
