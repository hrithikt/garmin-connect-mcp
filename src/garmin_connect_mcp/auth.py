"""Authentication and configuration for Garmin Connect API."""

from pathlib import Path

import keyring
import keyring.errors
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = Path.home() / ".garminconnect.env"
LOCAL_ENV_FILE = Path(".env")

KEYCHAIN_SERVICE = "garmin-connect-mcp"
KEYCHAIN_ACCOUNT = "oauth-tokens"


class GarminConfig(BaseSettings):
    """Garmin Connect API configuration from environment variables."""

    garmin_email: str = ""
    garmin_password: str = ""
    garmintokens: str = str(Path.home() / ".garminconnect")
    garmintokens_base64: str = str(Path.home() / ".garminconnect_base64")

    model_config = SettingsConfigDict(env_file_encoding="utf-8", case_sensitive=False)


def get_env_file_path() -> Path:
    """Get the path where interactive setup should write credentials."""
    local_env = Path.cwd() / LOCAL_ENV_FILE
    if local_env.exists():
        return local_env
    return DEFAULT_ENV_FILE


def load_config() -> GarminConfig:
    """Load configuration from environment variables and env files."""
    settings_kwargs = {"_env_file": (str(DEFAULT_ENV_FILE), str(LOCAL_ENV_FILE))}
    return GarminConfig(**settings_kwargs)


def validate_credentials(config: GarminConfig) -> bool:
    """Check if a stored OAuth token is available for authentication."""
    # `config` is unused now that credentials live in the keychain; kept so
    # middleware.py's call site doesn't need to change.
    return load_tokens() is not None


def get_token_store() -> str:
    """Get the token storage directory path."""
    config = load_config()
    token_dir = Path(config.garmintokens)
    token_dir.mkdir(parents=True, exist_ok=True)
    return str(token_dir)


def get_token_base64_path() -> str:
    """Get the base64 token file path."""
    config = load_config()
    return config.garmintokens_base64


def save_tokens(token_json: str) -> None:
    """Save the OAuth token blob to the OS keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token_json)


def load_tokens() -> str | None:
    """Load the OAuth token blob from the OS keychain, or None if absent/unreadable."""
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except keyring.errors.KeyringError:
        return None
