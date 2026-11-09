from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from awlib import oscompat

# A child that starts a grandchild; the grandchild writes a file after a delay.
GRANDPARENT = r'''
import subprocess, sys
marker = sys.argv[1]
code = "import time, pathlib, sys; time.sleep(3); pathlib.Path(sys.argv[1]).write_text('alive')"
subprocess.Popen([sys.executable, "-c", code, marker])
import time; time.sleep(60)
'''


def test_timeout_kills_whole_process_tree(tmp_path):
    marker = tmp_path / "grandchild-survived.txt"
    script = tmp_path / "gp.py"
    script.write_text(GRANDPARENT, encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        oscompat.run_process_with_group_timeout([sys.executable, str(script), str(marker)], cwd=tmp_path, timeout_sec=1.0)
    assert time.monotonic() - start < 30
    time.sleep(4.5)
    assert not marker.exists(), "grandchild process survived the timeout kill"


def test_normal_exit_code_is_returned(tmp_path):
    rc = oscompat.run_process_with_group_timeout([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path, timeout_sec=30)
    assert rc == 7


def test_exclusive_lock_is_exclusive(tmp_path):
    lock = tmp_path / "x.lock"
    code =("import sys, time; sys.path.insert(0, sys.argv[2]);"
            "from awlib.oscompat import FileLock;"
            "l = FileLock(sys.argv[1]).acquire(); print('locked', flush=True); time.sleep(4)")
    agentwork_dir = str(Path(oscompat.__file__).resolve().parent.parent)
    holder = subprocess.Popen([sys.executable, "-c", code, str(lock), agentwork_dir], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(oscompat.LockBusy):
            oscompat.FileLock(lock, blocking=False).acquire()
    finally:
        holder.wait(timeout=30)
    with oscompat.FileLock(lock, blocking=False):
        pass


@pytest.mark.parametrize("cmd, expected", [
    ('python -c "import sys; print(sys.argv[1])" "hello world"', "hello world"),
    ('python -c "print(1)" && python -c "print(2)"', "1\n2"),
])
def test_shell_commands_keep_quoting(tmp_path, cmd, expected):
    cmd = cmd.replace("python", f'"{sys.executable}"' if " " in sys.executable else sys.executable.replace("\\", "/"))
    out = tmp_path / "out.txt"
    full = f"({cmd}) > {out.name}"
    rc = oscompat.run_shell_command(full, cwd=tmp_path)
    assert rc == 0
    assert out.read_text(encoding="utf-8").replace("\r\n", "\n").strip() == expected


def test_shell_command_failure_and_timeout(tmp_path):
    assert oscompat.run_shell_command(f'"{sys.executable}" -c "raise SystemExit(3)"', cwd=tmp_path) == 3
    with pytest.raises(subprocess.TimeoutExpired):
        oscompat.run_shell_command(f'"{sys.executable}" -c "import time; time.sleep(30)"', cwd=tmp_path, timeout=1)


def test_resolve_shell():
    prefix = oscompat.resolve_shell("auto")
    if os.name == "nt":
        assert prefix[0].lower().endswith("cmd.exe") or prefix[0].lower() == "cmd.exe"
    else:
        assert prefix[-1] == "-c"
    with pytest.raises(ValueError):
        oscompat.resolve_shell("fish-and-chips")


def test_newline_normalisation(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"x\r\ny\r\n")
    b.write_bytes(b"x\ny\n")
    assert oscompat.stable_file_bytes(a) == oscompat.stable_file_bytes(b)
    binf = tmp_path / "c.bin"
    binf.write_bytes(b"\0\r\n")
    assert oscompat.stable_file_bytes(binf) == b"\0\r\n"


def test_state_dirs_honour_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTWORK_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("AGENTWORK_CACHE_HOME", str(tmp_path / "c"))
    assert oscompat.user_state_root() == tmp_path / "s"
    assert oscompat.user_cache_root() == tmp_path / "c"
