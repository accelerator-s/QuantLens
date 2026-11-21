#!/usr/bin/env python3
"""Repair the existing SJTU split tunnel without reading its credentials."""
import ipaddress
import json
import re
import socket
import subprocess


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=120).stdout


def installed(sas, child):
    return bool(re.search(rf'^\s+{re.escape(child)}:.*\bINSTALLED\b', sas, re.M))


def routed(addresses, routes):
    networks = [ipaddress.ip_network(r['dst'], strict=False) for r in routes
                if r.get('dst') not in (None, 'default') and r.get('prefsrc')]
    return bool(addresses) and all(any(ipaddress.ip_address(ip) in net for net in networks)
                                   for ip in addresses)


def ensure(run=command, resolve=socket.getaddrinfo):
    def state():
        sas = run('swanctl', '--list-sas', '--ike', 'vpn-student')
        routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', '220'))
        return sas, routes

    addresses = {entry[4][0] for entry in resolve('data.csmar.com', 443, socket.AF_INET)}
    sas, routes = state()
    if installed(sas, 'vpn-student') and installed(sas, 'vpn-extra') and routed(addresses, routes):
        return
    print('SJTU tunnel or CSMAR route missing; reconnecting.', flush=True)
    if 'ESTABLISHED' not in sas:
        run('/usr/local/bin/sjtuvpn', 'up')
    elif not installed(sas, 'vpn-student'):
        run('swanctl', '--initiate', '--child', 'vpn-student', '--timeout', '30')
    sas, routes = state()
    if not installed(sas, 'vpn-extra') or not routed(addresses, routes):
        # Also refresh DNS selectors when an established IKE session has lost
        # only its extra child. `sjtuvpn up` alone returns early in that case.
        run('/usr/local/bin/sjtuvpn', 'add', 'data.csmar.com')
    sas, routes = state()
    if not (installed(sas, 'vpn-student') and installed(sas, 'vpn-extra') and routed(addresses, routes)):
        raise RuntimeError('SJTU repair incomplete: both children and CSMAR routes are required')
    print('SJTU tunnel and CSMAR route restored.', flush=True)


if __name__ == '__main__':
    try:
        ensure()
    except Exception as exc:
        # Command output can contain VPN identities; leave it in strongSwan's
        # own logs instead of copying it into application/deployment output.
        raise SystemExit(f'SJTU health check failed: {type(exc).__name__}: {exc}')
