"""Build-time reduction and checksum-pinned updates of bundled Java dependencies."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import urllib.request

LOCK = json.loads(Path(__file__).with_name('java-dependencies.json').read_text())
REPOSITORY = 'https://repo.maven.apache.org/maven2/'


def target(name):
    match = re.fullmatch(r'(netty-.+?)-4\.1\.(\d+)\.Final(-[^/]+)?\.jar', name)
    if match:
        if int(match.group(2)) > 137:
            return None
        artifact, classifier = match.group(1), match.group(3) or ''
        return f'io/netty/{artifact}/4.1.137.Final/{artifact}-4.1.137.Final{classifier}.jar'
    match = re.fullmatch(r'(bc(?:prov|pkix|util)-jdk18on)-([\d.]+)\.jar', name)
    if match:
        if tuple(map(int, match.group(2).split('.'))) > (1, 85):
            return None
        artifact = match.group(1)
        return f'org/bouncycastle/{artifact}/1.85/{artifact}-1.85.jar'
    match = re.fullmatch(r'(avro(?:-ipc|-mapred|-ipc-jetty)?)-1\.11\.(\d+)\.jar', name)
    if match:
        if int(match.group(2)) > 5:
            return None
        artifact = match.group(1)
        return f'org/apache/avro/{artifact}/1.11.5/{artifact}-1.11.5.jar'
    match = re.fullmatch(r'(hadoop-client-(?:api|runtime))-3\.4\.1\.jar', name)
    if match:
        artifact = match.group(1)
        return f'org/apache/hadoop/{artifact}/3.4.3/{artifact}-3.4.3.jar'
    if name == 'hive-exec-4.0.1.jar':
        return 'org/apache/hive/hive-exec/4.0.1/hive-exec-4.0.1-core.jar'


def install(artifact, directory, cache):
    expected = LOCK[artifact]  # Unknown artifacts fail the build.
    if artifact not in cache:
        with urllib.request.urlopen(REPOSITORY + artifact, timeout=120) as response:
            if not response.url.startswith(REPOSITORY):
                raise ValueError('Unexpected dependency download redirect')
            blob = response.read(128 * 1024 * 1024 + 1)
        if len(blob) > 128 * 1024 * 1024 or hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError(f'Dependency checksum mismatch: {artifact}')
        cache[artifact] = blob
    destination = directory / artifact.rsplit('/', 1)[1]
    destination.write_bytes(cache[artifact])
    return destination


def harden(scope):
    # ResourceManager links the collector classes even when timeline storage
    # is disabled. Keep Hadoop's core classes; remove the unused server's
    # separate third-party library directory below.
    timeline = Path('/opt/hadoop/share/hadoop/yarn/timelineservice')
    for core in timeline.glob('hadoop-*.jar'):
        shutil.move(core, timeline.parent / core.name)
    patterns = [
        '/opt/hadoop/share/hadoop/client/*minicluster*.jar',
        '/opt/hadoop/share/hadoop/yarn/*catalog-webapp*.war',
        '/opt/hadoop/share/hadoop/yarn/timelineservice',
        '/opt/hive/lib/htrace-core-*.jar', '/opt/hive/lib/avatica-*.jar',
        '/opt/hive/lib/hive-druid-handler-*.jar', '/opt/hive/lib/hive-iceberg-handler-*.jar',
        '/opt/hive/lib/hive-kudu-handler-*.jar', '/opt/hive/lib/kudu-client-*.jar',
        '/opt/hive/lib/netty-3.*.jar', '/opt/hive/lib/derby*.jar',
        '/opt/hbase/lib/ruby',
        '/opt/spark/jars/derby*.jar', '/opt/spark/jars/connect-repl',
        '/opt/spark/hive-jackson', '/opt/spark/yarn',
    ]
    for pattern in patterns:
        for path in Path('/').glob(pattern.lstrip('/')):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    roots = {'hadoop': ['/opt/hadoop'], 'hive': ['/opt/hive', '/opt/hbase'],
             'hbase': ['/opt/hbase'], 'spark': ['/opt/hive', '/opt/spark']}[scope]
    cache = {}
    if scope == 'spark':
        for artifact in [
            'org/apache/calcite/calcite-core/1.32.0/calcite-core-1.32.0.jar',
            'org/apache/calcite/calcite-linq4j/1.32.0/calcite-linq4j-1.32.0.jar',
            'org/apache/calcite/avatica/avatica-core/1.22.0/avatica-core-1.22.0.jar',
        ]:
            install(artifact, Path('/opt/hive/lib'), cache)
    for root in roots:
        for path in sorted(Path(root).rglob('*.jar')):
            if re.match(r'netty-transport-(rxtx|udt)-', path.name):
                path.unlink()  # Serial/UDT transports are unused by this TCP cluster.
                continue
            artifact = target(path.name)
            if artifact:
                destination = install(artifact, path.parent, cache)
                if destination != path:
                    path.unlink()


if __name__ == '__main__':
    harden(sys.argv[1])
