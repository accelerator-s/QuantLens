"""Standalone isolated worker. Security controls precede all third-party imports."""
from __future__ import annotations

import ctypes
from contextlib import redirect_stdout
import errno
import json
import os
from pathlib import Path
import platform
import resource
import sys


def restrict(root):
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise RuntimeError("Linux Landlock/seccomp is required")
    libc = ctypes.CDLL(None, use_errno=True)
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "no_new_privs")
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError("Landlock ABI >= 3 is required")

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]

    # Handle every filesystem action through ABI 3, plus device ioctl on ABI 5+.
    handled = (1 << 15) - 1
    if abi >= 5:
        handled |= 1 << 15
    attr = Ruleset(handled)
    ruleset = libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    try:
        # No writable path is granted. The result uses an already open stdout FD.
        for path in dict.fromkeys(["/usr", "/lib", "/lib64", sys.prefix, sys.base_prefix, str(root)]):
            if not Path(path).exists():
                continue
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule((1 << 2) | (1 << 3), fd)  # READ_FILE | READ_DIR
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError(ctypes.get_errno(), "landlock_add_rule")
            finally:
                os.close(fd)
        if libc.syscall(446, ruleset, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(ruleset)

    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = seccomp.seccomp_init(0x00050000 | errno.EPERM)  # Default deny.
    if not ctx:
        raise RuntimeError("seccomp_init failed")
    try:
        # No sockets, exec, fork/clone, ptrace, process signalling, filesystem
        # mutation, io_uring, mount, bpf, or changes to sandbox/resource controls.
        allowed = """read write readv writev pread64 pwrite64 close close_range
            fstat newfstatat stat lstat statx access faccessat faccessat2 lseek
            open openat readlink readlinkat getdents getdents64 fcntl
            mmap mprotect munmap mremap madvise brk futex futex_waitv
            clock_gettime gettimeofday time nanosleep clock_nanosleep
            rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack rt_sigtimedwait
            restart_syscall getpid getppid gettid getuid geteuid getgid getegid
            getrandom arch_prctl set_tid_address set_robust_list rseq
            sched_yield sched_getaffinity getcpu uname getcwd sysinfo
            statfs fstatfs getrlimit exit exit_group""".split()
        for name in allowed:
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(ctx, 0x7fff0000, number, 0) != 0:
                raise RuntimeError("seccomp_rule_add failed")
        # Native numeric libraries need threads. Permit CLONE_THREAD only;
        # clone3 returns ENOSYS so libc uses the inspectable clone ABI.
        class ArgCompare(ctypes.Structure):
            _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                        ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]
        clone = seccomp.seccomp_syscall_resolve_name(b"clone")
        comparison = ArgCompare(0, 7, 0x10000, 0x10000)
        if clone < 0 or seccomp.seccomp_rule_add(ctx, 0x7fff0000, clone, 1, comparison) != 0:
            raise RuntimeError("Thread isolation rule failed")
        clone3 = seccomp.seccomp_syscall_resolve_name(b"clone3")
        if clone3 >= 0 and seccomp.seccomp_rule_add(ctx, 0x00050000 | errno.ENOSYS, clone3, 0) != 0:
            raise RuntimeError("clone3 isolation rule failed")
        if seccomp.seccomp_attr_set(ctx, 4, 1) != 0 or seccomp.seccomp_load(ctx) != 0:
            raise RuntimeError("seccomp_load failed")
    finally:
        seccomp.seccomp_release(ctx)


def main():
    root = Path(sys.argv[1]).resolve()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        restrict(root)
    except Exception as exc:
        print(f"Sandbox initialization failed: {exc}", file=sys.stderr)
        return 78

    import pandas as pd
    import pyarrow as pa
    import pyarrow.ipc as ipc

    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    panel = {name: ipc.open_file(root / f"{index}.arrow").read_all().to_pandas()
             for index, name in enumerate(request["fields"])}
    for name, frequency in zip(request["fields"], request["frequencies"]):
        if frequency is not None:
            panel[name].index.freq = frequency
    originals = {name: frame.copy(deep=True) for name, frame in panel.items()} if request["check_inputs"] else None
    namespace = {"__name__": "quantlens_factor"}
    with redirect_stdout(sys.stderr):
        exec(compile(request["source"], "<factor>", "exec"), namespace)
        compute = namespace.get("compute")
        if not callable(compute):
            return 81
        value = compute(panel)
    if not isinstance(value, pd.DataFrame):
        return 80
    if originals is not None and (panel.keys() != originals.keys() or any(
            not isinstance(panel[name], pd.DataFrame) or not panel[name].equals(frame)
            for name, frame in originals.items())):
        print("Factor modified the input panel", file=sys.stderr)
        return 79
    table = pa.Table.from_pandas(value, preserve_index=True)
    with ipc.new_file(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
