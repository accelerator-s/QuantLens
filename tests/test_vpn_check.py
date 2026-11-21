import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('vpn_check', Path(__file__).resolve().parents[1] / 'deploy/ensure_sjtuvpn.py')
vpn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vpn)

UP = 'vpn-student: #1, ESTABLISHED\n  vpn-student: #2, INSTALLED\n  vpn-extra: #3, INSTALLED'
ROUTES = '[{"dst":"45.124.127.24","prefsrc":"111.186.48.217"}]'


@pytest.mark.parametrize('initial,route,repair', [
    (UP, ROUTES, None),
    ('', '[]', ('/usr/local/bin/sjtuvpn', 'up')),
    (UP.replace('  vpn-extra: #3, INSTALLED', ''), ROUTES, ('/usr/local/bin/sjtuvpn', 'add', 'data.csmar.com')),
    (UP, '[]', ('/usr/local/bin/sjtuvpn', 'add', 'data.csmar.com')),
    (UP.replace('  vpn-student: #2, INSTALLED', ''), ROUTES, ('swanctl', '--initiate', '--child', 'vpn-student', '--timeout', '30')),
])
def test_checks_actual_tunnels_and_repairs_only_missing_state(initial, route, repair):
    calls = []
    def run(*args):
        if args[0:2] == ('swanctl', '--list-sas'):
            return UP if calls else initial
        if args[0] == 'ip':
            return ROUTES if calls else route
        calls.append(args)
        return ''
    vpn.ensure(run, lambda *args: [(None, None, None, None, ('45.124.127.24', 443))])
    assert calls == ([repair] if repair else [])


def test_repair_must_verify_result_even_when_commands_succeed():
    def run(*args):
        return '[]' if args[0] == 'ip' else ''
    with pytest.raises(RuntimeError, match='repair incomplete'):
        vpn.ensure(run, lambda *args: [(None, None, None, None, ('45.124.127.24', 443))])


def test_all_resolved_addresses_need_routes():
    routes = [{'dst': '45.124.127.24/32', 'prefsrc': '111.186.48.217'}]
    assert not vpn.routed(['45.124.127.24', '45.124.127.25'], routes)
    assert not vpn.routed(['45.124.127.24'], [{'dst': 'default', 'prefsrc': '167.172.118.93'}])
