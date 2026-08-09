# Garmin Keychain Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Garmin credentials and OAuth tokens off disk entirely and into macOS Keychain, fixing the `validate_credentials` gate that currently requires a plaintext `.env` file even when valid tokens already exist.

**Architecture:** Two new functions in `auth.py` (`save_tokens`/`load_tokens`) wrap the `keyring` package. `init_garmin_client` in `client.py` swaps its file-based token read/write for these two calls, using the `garminconnect` library's existing support for passing token JSON as an in-memory string (`Garmin.login(token_json)` — any string over 512 chars is treated as inline data, not a file path). Every existing function signature in `auth.py`/`client.py` stays identical, so `middleware.py` and `server.py` need zero changes.

**Tech Stack:** Python 3.12, `keyring>=25.7.0`, existing `garminconnect`/`fastmcp` stack, `pytest` + `pytest-mock` for tests.

## Global Constraints

- Dependency floor: `keyring>=25.7.0` (resolved via `uv add keyring` against current PyPI).
- Keychain identity is fixed: service `"garmin-connect-mcp"`, account `"oauth-tokens"`.
- No Garmin credentials or OAuth tokens may be written to disk, in any task, at any point.
- Preserve these existing signatures exactly — do not rename, reorder params, or change return types: `GarminConfig`, `load_config() -> GarminConfig`, `validate_credentials(config: GarminConfig) -> bool`, `init_garmin_client(config: GarminConfig, prompt_mfa: Callable[[], str] | None = None) -> Garmin | None`.
- `src/garmin_connect_mcp/middleware.py` and `src/garmin_connect_mcp/server.py` must receive **zero** line changes in this plan.
- Leave `get_env_file_path()`, `get_token_store()`, `get_token_base64_path()`, `DEFAULT_ENV_FILE`, `LOCAL_ENV_FILE`, and all `GarminConfig` fields byte-for-byte unchanged in `auth.py`, even though unused by the new code path (upstream merge-friendliness — see spec).
- Detroit-style tests (project `CLAUDE.md`): assert on observable behavior; mock only at the boundary. The boundary here is the OS keychain (faked via an in-memory `keyring` backend, never the real macOS Keychain) and the `garminconnect.Garmin` class (the external API boundary).
- Use `make` targets, not raw commands, per project `CLAUDE.md`. Run `make can-release` before considering the work done.
- Do not touch `README.md`'s Docker/Linux auth instructions (out of scope, per spec non-goals).

---

## File Structure

| File | Change |
|---|---|
| `pyproject.toml` | Add `keyring>=25.7.0` dependency |
| `tests/conftest.py` | Add shared in-memory fake keyring backend + autouse fixture (repo-wide safety net so no test ever touches the real macOS Keychain) |
| `src/garmin_connect_mcp/auth.py` | Add `KEYCHAIN_SERVICE`, `KEYCHAIN_ACCOUNT`, `save_tokens()`, `load_tokens()`; change `validate_credentials()` body only |
| `tests/test_auth.py` | New — tests for `save_tokens`/`load_tokens`/`validate_credentials` |
| `src/garmin_connect_mcp/client.py` | Rewire `init_garmin_client()` body to use Keychain instead of files; trim now-unused imports |
| `tests/test_client.py` | New — tests for the three `init_garmin_client` branches |
| `tests/test_middleware.py` | New — regression tests proving `middleware.py`'s unchanged code still behaves correctly against the new auth backend |
| `src/garmin_connect_mcp/scripts/setup_auth.py` | Remove plaintext `.env` writing; update success message |

---

### Task 1: Keychain-backed token storage in `auth.py`

**Files:**
- Modify: `pyproject.toml:7-14` (dependencies list)
- Modify: `tests/conftest.py` (add fixture, applies repo-wide)
- Modify: `src/garmin_connect_mcp/auth.py:1-57` (full file)
- Test: `tests/test_auth.py` (new)

**Interfaces:**
- Consumes: nothing new (first task)
- Produces: `save_tokens(token_json: str) -> None`, `load_tokens() -> str | None`, `KEYCHAIN_SERVICE: str = "garmin-connect-mcp"`, `KEYCHAIN_ACCOUNT: str = "oauth-tokens"`, and `validate_credentials(config: GarminConfig) -> bool` (signature unchanged, now returns `load_tokens() is not None`). Also produces the `fake_keyring` autouse fixture in `conftest.py`, consumed automatically by every test in the suite from this point on.

- [ ] **Step 1: Add the `keyring` dependency**

Run:
```bash
uv add keyring
```

Verify `pyproject.toml`'s `dependencies` list now contains a `keyring>=...` line (version may differ slightly from `25.7.0` depending on what's current — that's fine, keep whatever `uv add` resolves).

- [ ] **Step 2: Add the repo-wide fake keyring safety net to `conftest.py`**

Every test that touches `save_tokens`/`load_tokens` must never hit the real macOS Keychain. Add this to the top of `tests/conftest.py`, after the existing imports:

```python
"""Pytest configuration and shared fixtures."""

import keyring
import keyring.backend
import pytest

from garmin_connect_mcp.types import HeartRateData, SleepData, StepsData, StressData


class InMemoryKeyring(keyring.backend.KeyringBackend):
    """Fake keyring backend for tests — never touches the real OS keychain."""

    priority = 1

    def __init__(self):
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def fake_keyring():
    """Swap in an in-memory keyring backend for every test in the suite."""
    original_backend = keyring.get_keyring()
    keyring.set_keyring(InMemoryKeyring())
    yield
    keyring.set_keyring(original_backend)
```

Keep all the existing fixtures (`sample_sleep_data`, etc.) below this unchanged.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_auth.py`:

```python
"""Tests for keychain-backed token storage in auth.py."""

from garmin_connect_mcp.auth import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    GarminConfig,
    load_tokens,
    save_tokens,
    validate_credentials,
)


def test_load_tokens_returns_none_when_nothing_stored():
    assert load_tokens() is None


def test_save_then_load_round_trips():
    save_tokens('{"di_token": "abc"}')
    assert load_tokens() == '{"di_token": "abc"}'


def test_save_overwrites_previous_value():
    save_tokens('{"di_token": "first"}')
    save_tokens('{"di_token": "second"}')
    assert load_tokens() == '{"di_token": "second"}'


def test_save_uses_expected_keychain_identity(fake_keyring):
    import keyring

    save_tokens("some-token-blob")
    assert keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) == "some-token-blob"


def test_validate_credentials_false_when_no_token_stored():
    config = GarminConfig()
    assert validate_credentials(config) is False


def test_validate_credentials_true_when_token_stored():
    save_tokens("some-token-blob")
    config = GarminConfig()
    assert validate_credentials(config) is True


def test_load_tokens_returns_none_on_keyring_error(mocker):
    import keyring.errors

    mocker.patch("keyring.get_password", side_effect=keyring.errors.KeyringError("locked"))
    assert load_tokens() is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run:
```bash
make test/auth
```
Expected: `ImportError: cannot import name 'KEYCHAIN_ACCOUNT' from 'garmin_connect_mcp.auth'` (or similar — `save_tokens`/`load_tokens`/`KEYCHAIN_SERVICE`/`KEYCHAIN_ACCOUNT` don't exist yet).

- [ ] **Step 5: Implement the changes in `auth.py`**

Replace the full contents of `src/garmin_connect_mcp/auth.py` with:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
make test/auth
```
Expected: all 7 tests in `tests/test_auth.py` PASS.

- [ ] **Step 7: Lint and format**

Run:
```bash
make format
make lint
```
Fix anything ruff/pyright flag (there shouldn't be anything, but `keyring.errors` needs the explicit `import keyring.errors` line above — already included).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock tests/conftest.py tests/test_auth.py src/garmin_connect_mcp/auth.py
git commit -m "$(cat <<'EOF'
feat(auth): store Garmin OAuth tokens in macOS Keychain

Adds save_tokens/load_tokens backed by the keyring package, and fixes
validate_credentials to check for a stored token instead of requiring
a plaintext .env file. All existing auth.py signatures are unchanged.
EOF
)"
```

---

### Task 2: Rewire `init_garmin_client` to use Keychain

**Files:**
- Modify: `src/garmin_connect_mcp/client.py:1-136`
- Test: `tests/test_client.py` (new)

**Interfaces:**
- Consumes: `save_tokens(token_json: str) -> None`, `load_tokens() -> str | None` from `garmin_connect_mcp.auth` (Task 1)
- Produces: `init_garmin_client(config: GarminConfig, prompt_mfa: Callable[[], str] | None = None) -> Garmin | None` — signature unchanged, consumed as-is by `middleware.py`, `server.py` (untouched) and `scripts/setup_auth.py` (Task 4)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_client.py`:

```python
"""Tests for init_garmin_client's keychain-backed authentication branches."""

from garminconnect import GarminConnectAuthenticationError

from garmin_connect_mcp.auth import GarminConfig, load_tokens, save_tokens
from garmin_connect_mcp.client import init_garmin_client


def test_uses_stored_token_without_credential_login(mocker):
    save_tokens("stored-token-blob")
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_instance = mock_garmin_cls.return_value

    config = GarminConfig(garmin_email="user@test.com", garmin_password="pw")
    result = init_garmin_client(config)

    assert result is mock_instance
    mock_garmin_cls.assert_called_once_with()
    mock_instance.login.assert_called_once_with("stored-token-blob")


def test_falls_back_to_credentials_and_saves_new_token(mocker):
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_instance = mock_garmin_cls.return_value
    mock_instance.client.dumps.return_value = '{"di_token": "new"}'

    config = GarminConfig(garmin_email="user@test.com", garmin_password="pw")
    result = init_garmin_client(config)

    assert result is mock_instance
    mock_garmin_cls.assert_called_once_with(email="user@test.com", password="pw", prompt_mfa=None)
    mock_instance.login.assert_called_once_with()
    assert load_tokens() == '{"di_token": "new"}'


def test_returns_none_when_no_token_and_no_credentials(mocker):
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_instance = mock_garmin_cls.return_value
    mock_instance.login.side_effect = GarminConnectAuthenticationError(
        "Username and password are required"
    )

    config = GarminConfig()
    result = init_garmin_client(config)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
make test/client
```
Expected: FAIL — `test_uses_stored_token_without_credential_login` fails because current code still calls `get_token_store()` (a real directory path) instead of `load_tokens()`, so the mocked `Garmin` assertions don't match actual behavior.

- [ ] **Step 3: Implement the changes in `client.py`**

Replace lines 1-15 (module docstring through imports) with:

```python
"""Garmin Connect API client wrapper with error handling."""

import sys
from collections.abc import Callable
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .auth import GarminConfig, load_tokens, save_tokens
```

(This drops the now-unused `from pathlib import Path` line and swaps the `.auth` import.)

Replace the body of `init_garmin_client` (currently lines 76-135, from `try:` through the final `return None`) with:

```python
    try:
        token_json = load_tokens()

        # Try token-based login first
        try:
            if token_json:
                garmin = Garmin()
                garmin.login(token_json)
                print("Logged in using token data from keychain.", file=sys.stderr)
                return garmin
            else:
                raise FileNotFoundError("No tokens found")

        except (
            FileNotFoundError,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
        ) as e:
            # Token login failed, try credential login
            print(f"Token login failed: {e}. Attempting credential-based login...", file=sys.stderr)

            # Create Garmin client with credentials. MFA prompts are only enabled when
            # an interactive caller explicitly supplies a callback.
            garmin = Garmin(
                email=config.garmin_email,
                password=config.garmin_password,
                prompt_mfa=prompt_mfa,
            )

            # Attempt credential-based login.
            garmin.login()

            # Save tokens for future use
            save_tokens(garmin.client.dumps())
            print("OAuth tokens saved to keychain.", file=sys.stderr)

            return garmin

    except GarminConnectAuthenticationError as err:
        print(f"Authentication error: {err}", file=sys.stderr)
        return None

    except GarminConnectTooManyRequestsError as err:
        print(f"Rate limit error: {err}", file=sys.stderr)
        return None

    except Exception as err:
        print(f"Unexpected error during login: {err}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        return None
```

The rest of the file (the `GarminAPIError` class hierarchy above `init_garmin_client`, and `GarminClientWrapper` below it) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
make test/client
```
Expected: all 3 tests in `tests/test_client.py` PASS.

- [ ] **Step 5: Run the full suite so far**

Run:
```bash
make test
```
Expected: all tests pass, including the untouched `test_formatters.py`/`test_pagination.py`/`test_response_builder.py`/`test_response_size.py` and Task 1's `test_auth.py`.

- [ ] **Step 6: Lint and format**

```bash
make format
make lint
```
Expected: clean (this step is what catches the now-unused `Path` import if it was left in by mistake).

- [ ] **Step 7: Commit**

```bash
git add src/garmin_connect_mcp/client.py tests/test_client.py
git commit -m "$(cat <<'EOF'
feat(client): load and persist Garmin OAuth tokens via Keychain

init_garmin_client now reads/writes tokens through auth.load_tokens/
save_tokens instead of files. Signature and fallback-to-credentials
structure are unchanged; middleware.py and server.py need no changes.
EOF
)"
```

---

### Task 3: Regression-test `middleware.py` against the new auth backend

`middleware.py` itself is not modified — this task proves, with a real test, that it still behaves correctly now that `validate_credentials`/`init_garmin_client` are Keychain-backed.

**Files:**
- Test: `tests/test_middleware.py` (new)

**Interfaces:**
- Consumes: `ConfigMiddleware` from `garmin_connect_mcp.middleware` (unchanged), `save_tokens` from `garmin_connect_mcp.auth` (Task 1), mocked `Garmin` from `garmin_connect_mcp.client` (Task 2)
- Produces: nothing new (pure regression coverage)

- [ ] **Step 1: Write the tests**

Create `tests/test_middleware.py`:

```python
"""Tests for ConfigMiddleware's keychain-backed authentication gate."""

import pytest
from fastmcp.exceptions import ToolError

from garmin_connect_mcp.auth import save_tokens
from garmin_connect_mcp.middleware import ConfigMiddleware


class FakeFastMCPContext:
    """Minimal stand-in for fastmcp's Context — only implements set_state."""

    def __init__(self):
        self.state: dict[str, object] = {}

    async def set_state(self, key, value, serializable=True):
        self.state[key] = value


class FakeMiddlewareContext:
    """Minimal stand-in for fastmcp's MiddlewareContext."""

    def __init__(self):
        self.fastmcp_context = FakeFastMCPContext()


async def test_raises_tool_error_when_no_token_stored():
    middleware = ConfigMiddleware()
    context = FakeMiddlewareContext()

    async def call_next(ctx):
        raise AssertionError("call_next should not run without valid auth")

    with pytest.raises(ToolError, match="garmin-connect-mcp auth"):
        await middleware.on_call_tool(context, call_next)


async def test_injects_client_and_calls_next_when_token_valid(mocker):
    save_tokens("stored-token-blob")
    mocker.patch("garmin_connect_mcp.client.Garmin")

    middleware = ConfigMiddleware()
    context = FakeMiddlewareContext()
    call_next = mocker.AsyncMock(return_value="tool-result")

    result = await middleware.on_call_tool(context, call_next)

    assert result == "tool-result"
    call_next.assert_called_once_with(context)
    assert "client" in context.fastmcp_context.state
```

- [ ] **Step 2: Run tests to verify they fail or pass appropriately**

Run:
```bash
make test/middleware
```
Expected: both tests PASS immediately, since `middleware.py` requires no changes — this step confirms that, rather than driving new implementation. If either fails, investigate `middleware.py`'s actual current behavior before changing anything (this file is out of scope for edits per the Global Constraints).

- [ ] **Step 3: Lint and format**

```bash
make format
make lint
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_middleware.py
git commit -m "$(cat <<'EOF'
test(middleware): lock in ConfigMiddleware behavior against Keychain auth

No source changes — proves the existing middleware.py gate still works
correctly now that validate_credentials/init_garmin_client are backed
by the keychain instead of a plaintext env file.
EOF
)"
```

---

### Task 4: Remove plaintext credential writing from the `auth` CLI command

**Files:**
- Modify: `src/garmin_connect_mcp/scripts/setup_auth.py` (full file, 78 lines)

**Interfaces:**
- Consumes: `KEYCHAIN_SERVICE`, `KEYCHAIN_ACCOUNT` from `garmin_connect_mcp.auth` (Task 1); `init_garmin_client` from `garmin_connect_mcp.client` (Task 2, signature unchanged)
- Produces: nothing consumed by later tasks (CLI entry point)

This script is interactive (`input()`-driven) and has no existing test coverage (`tests/` has no `test_setup_auth.py` today, and none is added here — restructuring it for dependency-injected testability would be a bigger change than the approved spec calls for). Verification for this task is an import-level sanity check plus lint/type-check, and a real end-to-end run happens in Task 5.

- [ ] **Step 1: Replace the full contents of `setup_auth.py`**

```python
"""Interactive authentication setup script for Garmin Connect MCP."""

import sys

from ..auth import KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE, GarminConfig
from ..client import init_garmin_client


def main():
    """Run the interactive authentication setup."""
    print("=" * 60)
    print("Garmin Connect MCP - Authentication Setup")
    print("=" * 60)
    print()
    print("This script will help you set up authentication with Garmin Connect.")
    print()

    # Get Garmin credentials
    print("Enter your Garmin Connect credentials:")
    print("-" * 60)
    email = input("Email: ").strip()
    password = input("Password: ").strip()

    if not email or not password:
        print("\nError: Email and password are required.")
        return

    print()
    print("-" * 60)
    print("Authenticating with Garmin Connect...")
    print("-" * 60)
    print()
    print("If your Garmin account has MFA enabled, enter the code when prompted.")
    print()

    def prompt_for_mfa() -> str:
        """Prompt for a Garmin MFA code in the interactive setup command."""
        return input("MFA one-time code: ").strip()

    config = GarminConfig(garmin_email=email, garmin_password=password)
    client = init_garmin_client(config, prompt_mfa=prompt_for_mfa)

    if client is None:
        print()
        print("Authentication failed.")
        print("Please check your credentials and try again.")
        sys.exit(1)

    print("=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print()
    print("Authentication successful.")
    print(
        f"Tokens saved to macOS Keychain "
        f"(service: {KEYCHAIN_SERVICE!r}, account: {KEYCHAIN_ACCOUNT!r})."
    )
    print()
    print("You can now use the Garmin Connect MCP server:")
    print("  uvx garmin-connect-mcp")
    print()
    print("Your saved tokens will be reused automatically.")
    print("Re-run this script if you need to change credentials or re-authenticate.")
    print()


if __name__ == "__main__":
    main()
```

This removes the `from dotenv import set_key` import, the `get_env_file_path`/`get_token_store` imports and calls, and the `env_path` mkdir/touch + `set_key(...)` lines that wrote plaintext credentials to `~/.garminconnect.env`.

- [ ] **Step 2: Verify the module still imports cleanly**

Run:
```bash
uv run python -c "import garmin_connect_mcp.scripts.setup_auth"
```
Expected: no output, exit code 0 (confirms no leftover broken imports).

- [ ] **Step 3: Run the full test suite**

```bash
make test
```
Expected: all tests still pass (this file has no direct tests, but confirms nothing else broke).

- [ ] **Step 4: Lint and format**

```bash
make format
make lint
```
Expected: clean — this is what catches the removed `dotenv`/`get_env_file_path`/`get_token_store` imports if any were left dangling.

- [ ] **Step 5: Commit**

```bash
git add src/garmin_connect_mcp/scripts/setup_auth.py
git commit -m "$(cat <<'EOF'
fix(auth): stop writing plaintext Garmin credentials to disk

The interactive `auth` command no longer writes GARMIN_EMAIL/
GARMIN_PASSWORD to ~/.garminconnect.env. Credentials stay in memory
for the single login call; only the resulting OAuth token is
persisted, via init_garmin_client's Keychain-backed save_tokens.
EOF
)"
```

---

### Task 5: Full verification and manual migration

**Files:** none (verification + manual steps only)

**Interfaces:** none

- [ ] **Step 1: Run the full release-gate check**

```bash
make can-release
```
Expected: lint (ruff + pyright) and the full test suite both pass.

- [ ] **Step 2 (manual, requires live Garmin credentials — not run by an automated worker): re-run interactive auth**

```bash
make auth
```
Enter your real Garmin email/password/MFA code when prompted, same as always. Confirm the final message references macOS Keychain, not a file path.

- [ ] **Step 3 (manual): verify a real tool call works**

From Claude Desktop or Claude Code, ask a Garmin-related question (e.g. "get my Garmin profile") and confirm it succeeds — same check used earlier in this project when the file-based token store was first verified.

- [ ] **Step 4 (manual): delete the old plaintext files**

Once Step 3 confirms the Keychain-backed flow works end-to-end:
```bash
rm ~/.garminconnect.env
rm -rf ~/.garminconnect
rm ~/.garminconnect_base64
```

- [ ] **Step 5: Push the branch** (only if you want these commits on GitHub — ask before pushing)

```bash
git push origin main
```
