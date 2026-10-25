from pathlib import Path

import pytest

from node_manager import remote_install


@pytest.fixture
def ssh_files(tmp_path, monkeypatch):
    main = tmp_path / 'sshd_config'
    snippet = tmp_path / 'sshd_config.d/00-kingline-key-only.conf'
    snippet.parent.mkdir()
    main.write_text('Include /etc/ssh/sshd_config.d/00-template-access.conf\nPasswordAuthentication yes\n')
    snippet.write_text('PasswordAuthentication yes\n')
    original = (main.read_text(), snippet.read_text())
    monkeypatch.setattr(remote_install, 'Path', lambda p: {
        '/etc/ssh/sshd_config': main,
        '/etc/ssh/sshd_config.d/00-kingline-key-only.conf': snippet,
    }.get(str(p), Path(p)))
    return main, snippet, original


def test_provider_include_is_preceded_and_retries_keep_single_include(ssh_files, monkeypatch):
    main, snippet, original = ssh_files
    calls = []
    monkeypatch.setattr(remote_install, 'run', lambda *a: calls.append(a))
    def effective(args, **kwargs):
        assert main.read_text().splitlines()[0] == 'Include ' + str(snippet)
        assert snippet.read_text().startswith('PasswordAuthentication no\n')
        return (b'passwordauthentication no\nkbdinteractiveauthentication no\n'
                b'permitrootlogin without-password\npubkeyauthentication yes\n')
    monkeypatch.setattr(remote_install.subprocess, 'check_output', effective)
    for _ in range(2):
        assert remote_install.harden_ssh({'core_public_ip': '198.51.100.10'}) == {'ssh_key_only': True}
    assert main.read_text().count('Include ' + str(snippet)) == 1
    assert main.with_name('sshd_config.before-kingline').read_text() == original[0]
    assert calls.count(('systemctl', 'reload', 'ssh')) == 2


def test_unexpected_effective_settings_restore_working_ssh_files(ssh_files, monkeypatch):
    main, snippet, original = ssh_files
    calls = []
    def run(*args):
        calls.append(args)
        if args == ('systemctl', 'reload', 'ssh'):
            assert (main.read_text(), snippet.read_text()) == original
    monkeypatch.setattr(remote_install, 'run', run)
    monkeypatch.setattr(remote_install.subprocess, 'check_output', lambda *a, **kw:
                        b'passwordauthentication yes\nkbdinteractiveauthentication no\n')
    with pytest.raises(AssertionError):
        remote_install.harden_ssh({'core_public_ip': '198.51.100.10'})
    assert (main.read_text(), snippet.read_text()) == original
    assert calls.count(('systemctl', 'reload', 'ssh')) == 1
