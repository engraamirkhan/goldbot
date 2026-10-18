"""The conftest guard: unit tests may not run the real gh/ssh/curl or reach beyond loopback (CI has no token or network)."""
import socket
import subprocess

import pytest

from tests.conftest import _blocked_cli


@pytest.mark.parametrize("args", [["gh", "api", "x"], ["/opt/homebrew/bin/gh"], ["ssh", "host"], "curl -s http://x",
                                  ["bash", "-c", "echo hi; gh pr list"], "echo a | curl x"])
def test_blocked_cli_names_the_real_tool(args):
    assert _blocked_cli(args)


@pytest.mark.parametrize("args", [["git", "status"], ["python", "-c", "print('gh')"], ["bash", "-c", "echo hello"],
                                  "ls -l"])
def test_other_commands_pass(args):
    assert _blocked_cli(args) is None


def test_a_fake_gh_script_under_tmp_is_not_the_real_tool(tmp_path):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    assert subprocess.run([str(fake)], capture_output=True, text=True).stdout.strip() == "fake"


def test_running_gh_fails_the_test():
    with pytest.raises(AssertionError, match="real `gh`"):
        subprocess.run(["gh", "--version"], capture_output=True)


def test_a_non_loopback_connect_fails_the_test():
    with pytest.raises(AssertionError, match="network socket"), socket.socket() as s:
        s.connect(("93.184.216.34", 80))


def test_a_hostname_lookup_fails_the_test():
    with pytest.raises(AssertionError, match="network socket"):
        socket.getaddrinfo("example.com", 80)


def test_loopback_lookup_still_works():
    assert socket.getaddrinfo("127.0.0.1", 80)
