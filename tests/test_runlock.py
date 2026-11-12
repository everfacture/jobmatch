import subprocess
import sys
import textwrap

import pytest

from jobmatch.runlock import RunLocked, run_lock


def test_second_holder_is_refused_then_allowed_after_release(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(RunLocked):
            with run_lock(tmp_path):
                pass
    with run_lock(tmp_path):  # released on exit
        pass


def test_lock_is_held_across_processes(tmp_path):
    code = textwrap.dedent(f"""
        import sys
        from jobmatch.runlock import RunLocked, run_lock
        try:
            with run_lock({str(tmp_path)!r}):
                sys.exit(0)
        except RunLocked:
            sys.exit(3)
    """)
    with run_lock(tmp_path):
        held = subprocess.run([sys.executable, "-c", code], check=False)
    free = subprocess.run([sys.executable, "-c", code], check=False)
    assert (held.returncode, free.returncode) == (3, 0)


def test_lock_released_when_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path):
            raise ValueError("boom")
    with run_lock(tmp_path):
        pass
