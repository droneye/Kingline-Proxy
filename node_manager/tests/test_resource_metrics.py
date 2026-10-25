from pathlib import Path
import importlib.util

path=Path(__file__).resolve().parents[2]/'staging/ops/resource_metrics.py'
spec=importlib.util.spec_from_file_location('resources',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def test_cpu_delta_memory_available_and_reboot(tmp_path):
    (tmp_path/'sys/kernel/random').mkdir(parents=True)
    (tmp_path/'sys/kernel/random/boot_id').write_text('boot-1')
    (tmp_path/'stat').write_text('cpu  10 0 10 80 0 0 0 0 0 0\n')
    (tmp_path/'meminfo').write_text('MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 600 kB\n')
    first,cursor=module.sample_resources(proc=tmp_path)
    assert first['cpu_percent'] is None
    assert first['ram_percent']==40
    (tmp_path/'stat').write_text('cpu  15 0 15 170 0 0 0 0 0 0\n')
    second,_=module.sample_resources(cursor,proc=tmp_path)
    assert second['cpu_percent']==10
    assert 0<=second['disk_percent']<=100
    (tmp_path/'sys/kernel/random/boot_id').write_text('boot-2')
    assert module.sample_resources(cursor,proc=tmp_path)[0]['cpu_percent'] is None
