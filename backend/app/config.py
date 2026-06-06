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

    # ---- Misc ----
    request_timeout: int = 20
    audit_dir: str = "audit_logs"
    # When True, read-only/safe commands run without an approval prompt; mutations
    # always require approval. Toggle per-run from the UI ("auto-approve reads").
    auto_approve_reads_default: bool = True

    @property
    def llm_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    def ssh_key_path_for_ticket(self, ticket_id: int) -> str:
        """Pick the team key for this ticket (case1..case5 per VM).

        Phoenix tickets 7001–7005 each have their own VM and matching
        ``case{N}_key.pem`` in the keys directory. ``SSH_PRIVATE_KEY_PATH`` points
        at any key in that directory; we swap in the right sibling file.
        """
        keys_dir = Path(self.ssh_private_key_path).parent
        case_num = ticket_id - 7000
        if 1 <= case_num <= 9:
            candidate = keys_dir / f"case{case_num}_key.pem"
            if candidate.is_file():
                return str(candidate)
        return self.ssh_private_key_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
