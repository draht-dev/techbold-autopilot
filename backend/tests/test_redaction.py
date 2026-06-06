"""
test_redaction.py — WRITE FIRST (RED phase)

Tests for secret redaction. Written before implementation.

Coverage:
  - Password/secret env assignments redacted
  - DB connection URIs with credentials redacted (host/db preserved)
  - API keys (sk-..., AKIA..., ghp_..., xox...) redacted
  - Private key blocks redacted
  - /etc/shadow lines redacted
  - JWT tokens redacted
  - Long hex tokens redacted
  - Ordinary diagnostic output PRESERVED unchanged
  - make_redacting_log_filter() works
"""

import logging
import pytest
from app.safety.redaction import redact, make_redacting_log_filter


# ===========================================================================
# 1. Env-style assignments
# ===========================================================================

class TestEnvAssignmentRedaction:
    def test_password_assignment(self):
        result = redact("PASSWORD=supersecret123")
        assert "supersecret123" not in result
        assert "PASSWORD=" in result
        assert "redacted" in result

    def test_passwd_assignment(self):
        result = redact("PASSWD=mysecretpassword")
        assert "mysecretpassword" not in result
        assert "PASSWD=" in result

    def test_secret_assignment(self):
        result = redact("SECRET=abc123xyz")
        assert "abc123xyz" not in result
        assert "SECRET=" in result

    def test_token_assignment(self):
        result = redact("TOKEN=eyJhbGciOiJIUzI1NiJ9.abc.def")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_api_key_assignment(self):
        result = redact("API_KEY=sk-1234567890abcdefghij")
        assert "sk-1234567890abcdefghij" not in result
        assert "API_KEY=" in result

    def test_generic_key_suffix(self):
        result = redact("STRIPE_KEY=sk_live_abcdefghij12345678")
        assert "sk_live_abcdefghij12345678" not in result

    def test_generic_token_suffix(self):
        result = redact("GITHUB_TOKEN=ghp_abcdefghij1234567890ABCD")
        assert "ghp_abcdefghij1234567890ABCD" not in result

    def test_generic_secret_suffix(self):
        result = redact("DB_SECRET=verysecretvalue")
        assert "verysecretvalue" not in result

    def test_phoenix_api_token(self):
        result = redact("PHOENIX_API_TOKEN=ph_live_abc123xyz")
        assert "ph_live_abc123xyz" not in result


# ===========================================================================
# 2. DB connection URIs
# ===========================================================================

class TestDBUriRedaction:
    def test_postgres_uri(self):
        result = redact("postgres://myuser:mypassword@localhost/mydb")
        assert "mypassword" not in result
        assert "myuser" not in result
        assert "localhost" in result
        assert "mydb" in result
        assert "redacted" in result

    def test_postgresql_uri(self):
        result = redact("postgresql://admin:secret@db.example.com:5432/production")
        assert "secret" not in result
        assert "admin" not in result
        assert "db.example.com" in result
        assert "production" in result

    def test_mysql_uri(self):
        result = redact("mysql://root:rootpass@127.0.0.1/appdb")
        assert "rootpass" not in result
        assert "127.0.0.1" in result
        assert "appdb" in result

    def test_mongodb_uri(self):
        result = redact("mongodb://appuser:pass123@mongo.host/mydb")
        assert "pass123" not in result
        assert "mongo.host" in result

    def test_redis_uri(self):
        result = redact("redis://:redispassword@localhost:6379/0")
        assert "redispassword" not in result
        assert "localhost" in result

    def test_amqp_uri(self):
        result = redact("amqp://rabbitmq:rabbitpass@rabbitmq.host/vhost")
        assert "rabbitpass" not in result
        assert "rabbitmq.host" in result


# ===========================================================================
# 3. API keys and tokens
# ===========================================================================

class TestAPIKeyRedaction:
    def test_openai_key(self):
        result = redact("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD")
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD" not in result
        assert "redacted" in result

    def test_anthropic_key(self):
        result = redact("sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "sk-ant-api03" not in result

    def test_aws_access_key(self):
        result = redact("AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "redacted" in result

    def test_github_pat(self):
        result = redact("ghp_abcdefghijklmnopqrstuvwxyz123456")
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in result

    def test_slack_token_b(self):
        result = redact("xoxb-12345-67890-abcdefghijklmnopqrstuvwxyz")
        assert "xoxb-12345-67890" not in result

    def test_slack_token_p(self):
        result = redact("xoxp-12345-67890-abcdefghijklmnopqrstuvwxyz")
        assert "xoxp-12345-67890" not in result

    def test_long_hex_token(self):
        # 64 hex chars looks like a secret
        hex_token = "a" * 64
        result = redact(hex_token)
        assert hex_token not in result
        assert "redacted" in result

    def test_jwt_token(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = redact(jwt)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "redacted" in result


# ===========================================================================
# 4. Private key blocks
# ===========================================================================

class TestPrivateKeyRedaction:
    def test_rsa_private_key_block(self):
        key_block = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtNaHpj/rnLobBqAqHZGJQmRo
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
-----END RSA PRIVATE KEY-----"""
        result = redact(key_block)
        assert "MIIEowIBAAKCAQEA" not in result
        assert "redacted" in result
        assert "private-key" in result

    def test_openssh_private_key_block(self):
        key_block = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END OPENSSH PRIVATE KEY-----"
        result = redact(key_block)
        assert "b3BlbnNzaC1rZXktdjEAAAA" not in result
        assert "redacted" in result

    def test_ec_private_key_block(self):
        key_block = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEINqWo9IAasfasdflkj\n-----END EC PRIVATE KEY-----"
        result = redact(key_block)
        assert "MHQCAQEEINqWo9IAasfasdflkj" not in result


# ===========================================================================
# 5. /etc/shadow lines
# ===========================================================================

class TestShadowLineRedaction:
    def test_shadow_line_with_hash(self):
        shadow_line = "root:$6$rounds=656000$somesalt$longhashvalue1234567890:19000:0:99999:7:::"
        result = redact(shadow_line)
        assert "$6$rounds=656000$somesalt$longhashvalue1234567890" not in result
        assert "root:" in result  # username preserved
        assert "redacted" in result

    def test_shadow_line_with_yescrypt(self):
        shadow_line = "ubuntu:$y$j9T$somesalt$longhashvalue123456789012345678:19500:0:99999:7:::"
        result = redact(shadow_line)
        assert "longhashvalue123456789012345678" not in result


# ===========================================================================
# 6. Ordinary diagnostic output MUST be preserved
# ===========================================================================

class TestDiagnosticPreservation:
    def test_nginx_status_preserved(self):
        text = "nginx.service - A high performance web server\n Active: active (running) since Mon 2026-01-01"
        result = redact(text)
        assert result == text

    def test_ls_output_preserved(self):
        text = "drwxr-xr-x 2 root root 4096 Jan  1 00:00 nginx"
        result = redact(text)
        assert result == text

    def test_systemctl_status_preserved(self):
        text = (
            "● nginx.service - A high performance web server\n"
            "     Loaded: loaded (/lib/systemd/system/nginx.service; enabled; vendor preset: enabled)\n"
            "     Active: active (running) since Mon 2026-01-01 00:00:00 UTC; 1h 23min ago\n"
        )
        result = redact(text)
        assert result == text

    def test_ps_output_preserved(self):
        text = "  PID TTY          TIME CMD\n 1234 ?        00:00:01 nginx"
        result = redact(text)
        assert result == text

    def test_df_output_preserved(self):
        text = "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1        20G   5G   14G  27% /"
        result = redact(text)
        assert result == text

    def test_ip_output_preserved(self):
        text = "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN"
        result = redact(text)
        assert result == text

    def test_journalctl_output_preserved(self):
        text = "Jan 01 00:00:01 hostname nginx[1234]: 2026/01/01 00:00:01 [error] 5678#0: something failed"
        result = redact(text)
        assert result == text

    def test_plain_text_preserved(self):
        text = "The service is running normally with no errors detected."
        result = redact(text)
        assert result == text

    def test_etc_passwd_line_preserved(self):
        # /etc/passwd lines do NOT contain password hashes — must be preserved
        text = "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin"
        result = redact(text)
        assert result == text

    def test_short_hex_not_redacted(self):
        # Short hex strings (< 32 chars) should NOT be redacted
        text = "exit code 0x00 pid 1234 fd 0xdeadbeef"
        result = redact(text)
        assert result == text

    def test_version_numbers_preserved(self):
        text = "nginx version: nginx/1.18.0 (Ubuntu)"
        result = redact(text)
        assert result == text


# ===========================================================================
# 7. In-context (mixed) redaction — secret surrounded by normal text
# ===========================================================================

class TestMixedRedaction:
    def test_password_in_config_line(self):
        text = "DATABASE_URL=postgres://user:SUPERSECRET@localhost/mydb port=5432"
        result = redact(text)
        assert "SUPERSECRET" not in result
        assert "localhost" in result
        assert "5432" in result

    def test_multiple_secrets_in_one_line(self):
        text = "API_KEY=sk-abc123 SECRET=mysecretval TOKEN=tok_xyz"
        result = redact(text)
        assert "sk-abc123" not in result
        assert "mysecretval" not in result
        assert "tok_xyz" not in result

    def test_key_in_env_output(self):
        text = "SOME_VAR=normalvalue\nAPI_KEY=sk-secretkey12345\nOTHER_VAR=anothervalue"
        result = redact(text)
        assert "sk-secretkey12345" not in result
        assert "SOME_VAR=normalvalue" in result
        assert "OTHER_VAR=anothervalue" in result


# ===========================================================================
# 8. Logging filter
# ===========================================================================

class TestLoggingFilter:
    def test_make_redacting_log_filter_returns_filter(self):
        f = make_redacting_log_filter()
        assert isinstance(f, logging.Filter)

    def test_log_filter_redacts_secret(self):
        f = make_redacting_log_filter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="PASSWORD=supersecret",
            args=(), exc_info=None,
        )
        f.filter(record)
        assert "supersecret" not in record.getMessage()
        assert "redacted" in record.getMessage()

    def test_log_filter_preserves_normal_log(self):
        f = make_redacting_log_filter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Service started successfully",
            args=(), exc_info=None,
        )
        original_msg = record.getMessage()
        f.filter(record)
        assert record.getMessage() == original_msg
