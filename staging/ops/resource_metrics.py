"""Small host sampler; CPU is a delta of /proc/stat, RAM excludes reclaimable cache."""
def sample_resources(previous=None, proc='/proc', disk_path='/'):
    import os
    import time
    from pathlib import Path
    previous = previous or {}
    fields = [int(v) for v in (Path(proc)/'stat').read_text().splitlines()[0].split()[1:9]]
    total = sum(fields)
    idle = fields[3] + fields[4]
    boot = (Path(proc)/'sys/kernel/random/boot_id').read_text().strip()
    delta = total - previous.get('total', total)
    cpu = None
    if previous.get('boot') == boot and delta > 0:
        cpu = round(max(0, min(100, 100 * (1 - (idle - previous['idle']) / delta))), 1)
    memory = {}
    for line in (Path(proc)/'meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        memory[key] = int(value.split()[0]) * 1024
    ram_total = memory['MemTotal']
    ram_used = max(0, ram_total - memory.get('MemAvailable', memory['MemFree']))
    disk = os.statvfs(disk_path)
    disk_total = disk.f_blocks * disk.f_frsize
    disk_used = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
    return {'at': int(time.time()), 'cpu_percent': cpu,
            'ram_percent': round(100 * ram_used / ram_total, 1),
            'ram_used': ram_used, 'ram_total': ram_total,
            'disk_percent': round(100 * disk_used / disk_total, 1) if disk_total else None,
            'disk_used': disk_used, 'disk_total': disk_total}, {'total': total, 'idle': idle, 'boot': boot}
