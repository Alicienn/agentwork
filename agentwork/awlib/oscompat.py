"""Operating-system portability helpers.

- process-group timeouts (POSIX sessions + killpg, Windows Job Objects with a
  taskkill fallback),
- advisory file locks (fcntl on POSIX, msvcrt on Windows),
- shell selection for user-provided verification commands,
- per-user state / cache directories.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, IO, List, Mapping, Optional, Sequence, Union

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

PathLike = Union[str, "os.PathLike[str]"]


# --------------------------------------------------------------------------- #
# Directories
# --------------------------------------------------------------------------- #

def user_state_root() -> Path:
    """Per-user directory for durable harness state (outside every repository)."""
    override = os.environ.get("AGENTWORK_STATE_HOME")
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "agentwork" / "state"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "agentwork"
    return Path.home() / ".local" / "state" / "agentwork"


def user_cache_root() -> Path:
    """Per-user cache directory (compiled helper tools, etc.)."""
    override = os.environ.get("AGENTWORK_CACHE_HOME")
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "agentwork" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "agentwork"
    if IS_MACOS:
        return Path.home() / "Library" / "Caches" / "agentwork"
    return Path.home() / ".cache" / "agentwork"


# --------------------------------------------------------------------------- #
# File locks
# --------------------------------------------------------------------------- #

class LockBusy(Exception):
    """Raised by FileLock(blocking=False) when another process holds the lock."""


class FileLock:
    """Cross-platform advisory lock on a sidecar file.

    POSIX uses fcntl.flock (shared or exclusive). Windows has no shared byte
    locks in msvcrt, so every lock is exclusive there; this is safe, only a
    little less concurrent.
    """

    def __init__(self, path: PathLike, exclusive: bool = True, blocking: bool = True, timeout: float = 120.0):
        self.path = Path(path)
        self.exclusive = exclusive
        self.blocking = blocking
        self.timeout = timeout
        self._fh: Optional[IO[str]] = None

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if IS_WINDOWS:
                import msvcrt

                deadline = time.monotonic() + self.timeout
                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not self.blocking:
                            raise LockBusy(str(self.path))
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Timed out waiting for lock {self.path}")
                        time.sleep(0.05)
            else:
                import fcntl

                flags = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
                if not self.blocking:
                    flags |= fcntl.LOCK_NB
                try:
                    fcntl.flock(fh.fileno(), flags)
                except BlockingIOError:
                    raise LockBusy(str(self.path))
        except BaseException:
            fh.close()
            raise
        self._fh = fh
        return self

    def release(self) -> None:
        fh = self._fh
        if fh is None:
            return
        self._fh = None
        try:
            if IS_WINDOWS:
                import msvcrt

                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# --------------------------------------------------------------------------- #
# Process trees with a hard timeout
# --------------------------------------------------------------------------- #

class _WindowsJob:
    """Minimal Win32 Job Object wrapper (kill-on-close) via ctypes."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self.handle = handle

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXTENDED()
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        JobObjectExtendedLimitInformation = 9
        ok = k32.SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                                         ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            err = ctypes.get_last_error()
            k32.CloseHandle(handle)
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, proc_handle: int) -> None:
        if not self._k32.AssignProcessToJobObject(self.handle, int(proc_handle)):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self, code: int = 1) -> None:
        self._k32.TerminateJobObject(self.handle, code)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def _resume_process(proc_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    status = ntdll.NtResumeProcess(int(proc_handle))
    if status != 0:
        raise OSError(status, "NtResumeProcess failed")


class ProcessTree:
    """A child process started so that it and *all* its descendants can be killed.

    POSIX: new session (``start_new_session``) + ``os.killpg``.
    Windows: the child is created suspended, put in a kill-on-close Job Object,
    then resumed, so even grandchildren that outlive their parent are killed.
    If Job Objects are unavailable, falls back to ``taskkill /T /F``.
    """

    def __init__(self, cmd: Sequence[str], cwd: Optional[PathLike] = None,
                 env: Optional[Mapping[str, str]] = None, **popen_kwargs) -> None:
        self.job: Optional[_WindowsJob] = None
        kwargs = dict(popen_kwargs)
        if IS_WINDOWS:
            CREATE_SUSPENDED = 0x00000004
            flags = kwargs.pop("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                job = _WindowsJob()
            except Exception:
                job = None
            if job is not None:
                flags |= CREATE_SUSPENDED
            self.proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None,
                                         creationflags=flags, **kwargs)
            if job is not None:
                try:
                    job.assign(self.proc._handle)  # type: ignore[attr-defined]
                    self.job = job
                except Exception:
                    job.close()
                finally:
                    _resume_process(self.proc._handle)  # type: ignore[attr-defined]
        else:
            self.proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None,
                                         start_new_session=True, **kwargs)

    @property
    def pid(self) -> int:
        return self.proc.pid

    def kill_tree(self) -> None:
        if IS_WINDOWS:
            if self.job is not None:
                self.job.terminate(1)
            else:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and self.proc.poll() is None:
                time.sleep(0.05)
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def wait(self, timeout: Optional[float] = None) -> int:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill_tree()
            raise
        except KeyboardInterrupt:
            self.kill_tree()
            raise
        finally:
            if self.proc.poll() is not None and self.job is not None:
                # Kill any orphaned grandchildren still attached to the job.
                self.job.terminate(1)
                self.job.close()
                self.job = None


def run_process_with_group_timeout(cmd: Sequence[str], cwd: Optional[PathLike], timeout_sec: Optional[float],
                                   env: Optional[Mapping[str, str]] = None) -> int:
    """Run ``cmd``; on timeout or Ctrl-C kill the whole process tree.

    Raises ``subprocess.TimeoutExpired`` on timeout (after killing the tree).
    """
    tree = ProcessTree(cmd, cwd=cwd, env=env)
    return tree.wait(timeout=timeout_sec)


# --------------------------------------------------------------------------- #
# Shell commands written by humans in specs (Verify:, done_when:, test_command)
# --------------------------------------------------------------------------- #

def resolve_shell(preference: str = "auto") -> List[str]:
    """Return the argv prefix used to run a one-line shell command string.

    auto: POSIX -> bash if present else /bin/sh; Windows -> cmd.exe.
    bash | sh | cmd | powershell | pwsh may be forced via project.yaml
    (``verification.shell``).
    """
    pref = (preference or "auto").strip().lower()
    if pref == "auto":
        if IS_WINDOWS:
            pref = "cmd"
        else:
            pref = "bash" if shutil.which("bash") else "sh"
    if pref == "cmd":
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/d", "/s", "/c"]
    if pref in ("powershell", "pwsh"):
        exe = shutil.which("pwsh") if pref == "pwsh" else (shutil.which("powershell") or shutil.which("pwsh"))
        if not exe:
            raise FileNotFoundError(f"Requested shell '{pref}' not found in PATH")
        return [exe, "-NoProfile", "-NonInteractive", "-Command"]
    if pref == "bash":
        exe = shutil.which("bash")
        if not exe:
            raise FileNotFoundError("Requested shell 'bash' not found in PATH")
        return [exe, "-c"]
    if pref == "sh":
        exe = shutil.which("sh") or "/bin/sh"
        return [exe, "-c"]
    raise ValueError(f"Unknown shell '{preference}' (expected auto|bash|sh|cmd|powershell|pwsh)")


def shell_argv(command: str, preference: str = "auto") -> List[str]:
    prefix = resolve_shell(preference)
    if IS_WINDOWS and os.path.basename(prefix[0]).lower().startswith("cmd"):
        # cmd /s /c "<command>" keeps the command line exactly as written.
        return prefix + [f'"{command}"']
    return prefix + [command]


def run_shell_command(command: str, cwd: PathLike, env: Optional[Mapping[str, str]] = None,
                      timeout: Optional[float] = None, preference: str = "auto") -> int:
    argv = shell_argv(command, preference)
    if IS_WINDOWS and argv[0].lower().endswith(("cmd.exe", "cmd")):
        # Pass a raw command line so cmd.exe sees our quoting verbatim.
        line = subprocess.list2cmdline(argv[:-1]) + " " + argv[-1]
        tree_cmd: Union[str, List[str]] = line
    else:
        tree_cmd = argv
    if isinstance(tree_cmd, str):
        tree = _ProcessTreeFromLine(tree_cmd, cwd=cwd, env=env)
        return tree.wait(timeout=timeout)
    return run_process_with_group_timeout(tree_cmd, cwd=cwd, timeout_sec=timeout, env=env)


class _ProcessTreeFromLine(ProcessTree):
    """ProcessTree variant taking a raw Windows command line string."""

    def __init__(self, line: str, cwd=None, env=None):  # noqa: D401
        self.job = None
        CREATE_SUSPENDED = 0x00000004
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        try:
            job: Optional[_WindowsJob] = _WindowsJob()
        except Exception:
            job = None
        if job is not None:
            flags |= CREATE_SUSPENDED
        self.proc = subprocess.Popen(line, cwd=cwd, env=dict(env) if env is not None else None, creationflags=flags)
        if job is not None:
            try:
                job.assign(self.proc._handle)  # type: ignore[attr-defined]
                self.job = job
            except Exception:
                job.close()
            finally:
                _resume_process(self.proc._handle)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

def which(name: str) -> Optional[str]:
    return shutil.which(name)


def merged_env(extra: Optional[Mapping[str, Optional[str]]] = None, base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Copy of the environment with ``extra`` applied (a value of None removes the key)."""
    env = dict(os.environ if base is None else base)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def prepend_pythonpath(env: Dict[str, str], *paths: PathLike) -> Dict[str, str]:
    parts = [str(p) for p in paths]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def normalize_newlines(data: bytes) -> bytes:
    """CRLF -> LF so hashes match between Windows (autocrlf) and POSIX checkouts."""
    return data.replace(b"\r\n", b"\n")


def is_probably_text(data: bytes) -> bool:
    return b"\0" not in data[:8192]


def stable_file_bytes(path: PathLike) -> bytes:
    data = Path(path).read_bytes()
    return normalize_newlines(data) if is_probably_text(data) else data


WINDOWS_CMDLINE_LIMIT = 30000
