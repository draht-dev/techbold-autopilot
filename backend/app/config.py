"""Application configuration — SPEC §3.

Loads from .env via pydantic-settings v2. All secrets stay on the backend;
never expose them to the frontend or include them in activity/audit output.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object.  All env-var names are the UPPER_SNAKE version
    of the field name, which pydantic-settings resolves automatically.
    """

    # Look for .env in the backend dir (local: uvicorn runs from backend/) and
    # at the repo root (one .env serves both local and Docker). Real process
    # env vars still take precedence over either file.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        extra="ignore",
        case_sensitive=False,
    )

    # --- Phoenix ERP ---
    phoenix_api_base_url: str = ""
    phoenix_api_token: str = ""

    # --- SSH to customer VMs (SPEC §3) ---
    ssh_private_key_path: str = "keys/your-key.pem"
    ssh_username: str = "azureuser"
    ssh_default_port: int = 22
    ssh_connect_timeout: int = 10
    ssh_command_timeout: int = 30
    ssh_command_timeout_max: int = 120

    # --- LLM provider (SPEC §3) ---
    llm_provider: str = "anthropic"          # anthropic | openai | azure-openai
    llm_model: str = ""

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""

    # --- Agent behaviour (SPEC §3) ---
    auto_run_readonly: bool = True
    agent_max_steps: int = 40
    agent_max_commands: int = 60

    # --- Audit log (SPEC §6) ---
    audit_dir: str = "data/audit"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return (and cache) the application settings singleton."""
    return Settings()


def resolve_ssh_connection(
    system_username: str | None,
    system_port: int | None,
    settings: Settings,
) -> tuple[str, int]:
    """Return the effective (username, port) for an SSH connection to a
    customer VM, falling back to the configured defaults when the customer-
    system record is empty or zero (SPEC §3).
    """
    username = system_username or settings.ssh_username
    port = system_port or settings.ssh_default_port
    return username, port
