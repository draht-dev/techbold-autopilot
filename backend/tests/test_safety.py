"""Safety layer: hard-fail denylist, mutation gating, reads, and redaction."""
import pytest

from app.safety import decide, redact

# Every one of these is a documented hard-fail and must be DENY.
DENY_CASES = [
    "rm -rf /",
    "rm -rf /etc",
    "sudo rm -rf /var/lib/postgresql",
    "rm -rf /var/log",
    "chmod -R 777 /",
    "chmod 777 /etc",
    "chown -R root:root /home",
    "ufw disable",
    "iptables -F",
    "nft flush ruleset",
    "setenforce 0",
    "systemctl stop ufw",
    "systemctl disable ssh",
    "dropdb production",
    'psql -c "DROP DATABASE app"',
    'mysql -e "TRUNCATE TABLE users"',
    "cat /etc/shadow",
    "cat /home/user/.env",
    "cat /root/.ssh/id_rsa",
    "grep secret /opt/app/credentials.json",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
    "journalctl --vacuum-time=1s",
    "history -c",
    "echo x > /var/log/syslog",
    ":(){ :|:& };:",
]

CONFIRM_CASES = [
    "systemctl restart nginx",
    "apt-get install nginx",
    "sed -i s/foo/bar/ /etc/app.conf",
    "kill 1234",
    "mv /tmp/a /tmp/b",
    "rm /tmp/file.txt",
    "chmod 644 /etc/app.conf",
    "curl https://example.com",
    "env",
]

INTERACTIVE_DENY_CASES = [
    "vim /etc/nginx/nginx.conf",
    "less /var/log/syslog",
    "top",
    "htop",
    "watch -n1 systemctl status nginx",
    "tail -f /var/log/syslog",
    "journalctl -f",
    "python3",
    "psql",
    "mysql",
]

ALLOW_CASES = [
    "ls -la /var/www",
    "cat /var/log/syslog",
    "systemctl status nginx",
    "systemctl --failed",
    "journalctl -u nginx -n 50",
    "df -h",
    "ss -tulpn",
    "uname -a",
    "ps aux",
    "curl http://localhost:8080/health",
]


@pytest.mark.parametrize("cmd", INTERACTIVE_DENY_CASES)
def test_interactive_commands_denied(cmd):
    assert decide(cmd, auto_approve_reads=True).action == "DENY", cmd


@pytest.mark.parametrize("cmd", DENY_CASES)
def test_dangerous_commands_denied(cmd):
    assert decide(cmd, auto_approve_reads=True).action == "DENY", cmd


@pytest.mark.parametrize("cmd", CONFIRM_CASES)
def test_mutations_require_confirmation(cmd):
    assert decide(cmd, auto_approve_reads=True).action == "CONFIRM", cmd


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_reads_allowed_in_auto_mode(cmd):
    assert decide(cmd, auto_approve_reads=True).action == "ALLOW", cmd


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_reads_confirm_in_manual_mode(cmd):
    assert decide(cmd, auto_approve_reads=False).action == "CONFIRM", cmd


def test_compound_command_takes_worst_category():
    # safe read piped into a dangerous delete -> DENY wins
    assert decide("ls / && rm -rf /etc", True).action == "DENY"


def test_redaction_of_assignments_and_keys():
    text = "password=hunter2\nAKIA1234567890ABCD12\napi_key: sk-abc123def456\nplain line"
    out, n = redact(text)
    assert "hunter2" not in out
    assert "AKIA1234567890ABCD12" not in out
    assert "sk-abc123def456" not in out
    assert "plain line" in out
    assert n >= 3


def test_redaction_of_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEnotreal\n-----END RSA PRIVATE KEY-----"
    out, n = redact(pem)
    assert "MIIEnotreal" not in out
    assert n == 1
