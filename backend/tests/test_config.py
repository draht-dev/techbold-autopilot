from pathlib import Path

from app.config import Settings


def test_ssh_key_per_ticket(tmp_path):
    (tmp_path / "case1_key.pem").write_text("fake")
    (tmp_path / "case3_key.pem").write_text("fake")
    settings = Settings(ssh_private_key_path=str(tmp_path / "case1_key.pem"))
    assert settings.ssh_key_path_for_ticket(7001) == str(tmp_path / "case1_key.pem")
    assert settings.ssh_key_path_for_ticket(7003) == str(tmp_path / "case3_key.pem")
    assert settings.ssh_key_path_for_ticket(7099) == str(tmp_path / "case1_key.pem")
