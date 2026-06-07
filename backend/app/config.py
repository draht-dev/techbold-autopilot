"""Application configuration loaded from the environment / .env file.

Secrets (Phoenix token, SSH key path, OpenRouter key) live here and never leave
the backend. See `.env.example` for the full list.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Pydantic reads real environment variables first (docker-compose passes the
    # .env through), then falls back to a local .env file for `uvicorn --reload`.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Phoenix ERP mock ----
    phoenix_api_base_url: str = "http://host.docker.internal:8000"
    phoenix_api_token: str = ""

    # ---- SSH access to the customer VMs ----
    ssh_private_key_path: str = "/keys/your-key.pem"
    ssh_username: str = "azureuser"
    ssh_connect_timeout: int = 15
    ssh_command_timeout: int = 45

    # ---- S3 fallback for uploaded SSH keys (all optional) ----
    # When a bucket is set, keys uploaded via POST /api/dev/keys are mirrored to
    # S3, and a key missing from the local keys dir is pulled back from S3 on
    # demand. Leave the bucket empty (the default) to run purely off local disk —
    # the S3 path then short-circuits and never touches boto3.
    s3_keys_bucket: str = ""
    s3_keys_prefix: str = "ssh-keys/"
    aws_region: str = ""

    # ---- LLM via OpenRouter (OpenAI-compatible) ----
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Strong model for reasoning (recon synthesis, hypotheses, fix planning).
    agent_model: str = "openai/gpt-5.5"
    # Fast model for verification, validation interpretation and activity drafting.
    fast_model: str = "openai/gpt-5.5-mini"

    # ---- Autonomous agent ----
    # The agent is one continuous tool-calling conversation. These bound its
    # context (compaction triggers at threshold * max) and its autonomy.
    agent_context_max_tokens: int = 200_000
    agent_context_compact_threshold: float = 0.8
    agent_context_keep_recent_messages: int = 20
    # Hard cap on tool-call rounds so a misbehaving model cannot loop forever.
    agent_max_iterations: int = 60
    # OpenRouter reasoning effort ("low" | "medium" | "high"); empty disables it.
    agent_reasoning_effort: str = "medium"

    # ---- ElevenLabs voice agent (optional) ----
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_base_url: str = "https://api.elevenlabs.io"

    # ---- Misc ----
    request_timeout: int = 20
    audit_dir: str = "audit_logs"
    # When True, read-only/safe commands run without an approval prompt; mutations
    # always require approval. Toggle per-run from the UI ("auto-approve reads").
    auto_approve_reads_default: bool = True

    # ---- Run registry bounds (in-memory; no DB) ----
    # Terminal scrollback retained per run for late-joiner replay, in CHARACTERS
    # (decoded str length, not raw UTF-8 bytes). Semantic events (phases, hypotheses,
    # approvals) are always kept in full; only the high-volume PTY/character stream is
    # bounded so a long session can't grow forever.
    term_replay_max_chars: int = 200_000
    # Max runs kept in memory. Finished runs beyond this are garbage-collected
    # (active runs are never evicted).
    max_retained_runs: int = 50

    @property
    def llm_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def voice_configured(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_agent_id)

    @property
    def s3_keys_configured(self) -> bool:
        return bool(self.s3_keys_bucket)

    @property
    def keys_dir(self) -> Path:
        """Directory that holds the SSH keys (parent of SSH_PRIVATE_KEY_PATH)."""
        return Path(self.ssh_private_key_path).parent

    def ssh_key_path_for_ticket(self, ticket_id: int) -> str:
        """Pick the team key for this ticket (case1..case5 per VM).

        Phoenix tickets 7001–7005 each have their own VM and matching
        ``case{N}_key.pem`` in the keys directory. ``SSH_PRIVATE_KEY_PATH`` points
        at any key in that directory; we swap in the right sibling file.

        If the sibling key is not on local disk but an S3 bucket is configured,
        we pull it down from S3 first (keys uploaded on another replica land
        there). The S3 path is skipped entirely when no bucket is set.
        """
        keys_dir = self.keys_dir
        case_num = ticket_id - 7000
        if 1 <= case_num <= 9:
            name = f"case{case_num}_key.pem"
            candidate = keys_dir / name
            if not candidate.is_file() and self.s3_keys_configured:
                from app.keys import KeyStore

                KeyStore(self).fetch_from_s3(name)
            if candidate.is_file():
                return str(candidate)
        return self.ssh_private_key_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
