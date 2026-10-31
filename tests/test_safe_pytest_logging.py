"""공공 API 전송 실패를 기본 pytest 옵션으로 실행해 인증 인자 노출을 점검한다."""
from pathlib import Path
import subprocess
import sys


def test_default_test_traceback_does_not_print_auth_arguments(tmp_path):
    root = Path(__file__).resolve().parents[1]
    test_file = tmp_path / "test_expected_auth_failure.py"
    # 가짜 인증값만 사용한다. 이 하위 검사는 의도적으로 실패해야 한다.
    test_file.write_text('''import os
import requests
from src.ingest import sgis

def test_auth_failure(monkeypatch):
    monkeypatch.setenv("SGIS_CONSUMER_KEY", "example-sensitive-key")
    monkeypatch.setenv("SGIS_CONSUMER_SECRET", "example-sensitive-secret")
    monkeypatch.setattr(sgis, "_token_cache", {})
    def fail(*args, **kwargs):
        raise requests.ConnectionError("example-sensitive-url-token")
    monkeypatch.setattr(sgis.requests, "get", fail)
    sgis.get_access_token()
''')
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--color=no", "-c",
                             str(root / "pyproject.toml"), str(test_file)],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "SGIS" in output and "RuntimeError" in output
    assert "example-sensitive-key" not in output
    assert "example-sensitive-secret" not in output
    assert "example-sensitive-url-token" not in output
