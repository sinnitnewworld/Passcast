"""Runs the JavaScript flight-planning engine tests (needs node)."""
import shutil, subprocess, pathlib, pytest

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_planner_engine():
    js = pathlib.Path(__file__).parent / "js" / "planner.test.js"
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
