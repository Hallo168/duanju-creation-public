import subprocess
from unittest.mock import Mock, patch
import pytest
from app.runner import SubprocessRunner, CLITimeout

def test_runner_uses_shell_false_and_captures_text() -> None:
    process = Mock(returncode=0); process.communicate.return_value = ("{}", "")
    with patch("app.runner.subprocess.Popen", return_value=process) as popen:
        result = SubprocessRunner().run(["node", "cli.js"], timeout=10)
    assert result.exit_code == 0
    popen.assert_called_once_with(["node", "cli.js"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False)
    process.communicate.assert_called_once_with(timeout=10)

def test_runner_timeout_kills_child_process() -> None:
    process = Mock(); process.communicate.side_effect = [subprocess.TimeoutExpired(["node"], 2), ("late", "stopped")]
    with patch("app.runner.subprocess.Popen", return_value=process) as popen:
        with pytest.raises(CLITimeout) as caught: SubprocessRunner().run(["node", "cli.js"], timeout=2)
    assert caught.value.timeout == 2
    process.kill.assert_called_once_with()
    assert process.communicate.call_count == 2
    assert popen.call_args.kwargs["shell"] is False
