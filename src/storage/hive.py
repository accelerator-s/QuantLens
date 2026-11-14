"""Run the current Hive CLI with the terminal it requires, without interactive input."""
from storage.hdfs import compose


def execute(sql: str, *, timeout=300):
    return compose('exec', '--interactive=false', '--no-TTY=false', 'hive', 'beeline',
                   '-u', 'jdbc:hive2://localhost:10000/default', '-n', 'root',
                   '--silent=true', '--showHeader=false', '--outputformat=tsv2', '-e', sql,
                   timeout=timeout)
