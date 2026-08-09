# Garmin Keychain Auth — Design

## Problem

Garmin credentials and OAuth tokens currently live on disk as plaintext:

- `~/.garminconnect.env` — `GARMIN_EMAIL` / `GARMIN_PASSWORD` in plaintext, written by
  `scripts/setup_auth.py` via `dotenv.set_key(...)`.
- `~/.garminconnect/garmin_tokens.json` and `~/.garminconnect_base64` — OAuth token blobs,
  written by `client.py::init_garmin_client`.

File permissions (`600`) restrict these to the local user account, but the secrets themselves
are unencrypted. Separately, `middleware.py::ConfigMiddleware` gates every tool call on
`validate_credentials()`, which checks for a non-empty `GARMIN_EMAIL`/`GARMIN_PASSWORD` — even
though the actual login path (`init_garmin_client`) tries the saved OAuth tokens first and only
needs credentials as a fallback. This means deleting the plaintext `.env` file (the obvious fix
for the first problem) breaks tool calls entirely, even with perfectly valid tokens sitting in
`~/.garminconnect/`.

## Goals

- No Garmin credentials or OAuth tokens ever touch disk as plaintext (or at all).
- Fix the `validate_credentials` gate at its root, so it reflects whether authentication is
  actually possible (a valid stored token), not whether a legacy env file exists.
- Minimize the diff against upstream (`eddmann/garmin-connect-mcp`), since this is a personal
  fork the user wants to keep pulling updates into. Prefer leaving existing function signatures
  and unused-but-harmless upstream code untouched over deleting/restructuring it.

## Non-goals

- Cross-platform support beyond macOS. This fork is used only via `uvx` on the user's Mac
  (Claude Desktop + Claude Code). Docker/Linux compatibility from the upstream README is not
  preserved by this change.
- Mobile/remote hosting (tracked separately, currently on hold).
- Updating `README.md`'s Docker/Linux auth instructions to reflect this fork's Keychain-only
  flow. The README continues to describe upstream behavior; out of scope for this change.

## Approaches considered

1. **OS Keychain via the `keyring` package (chosen).** Store the OAuth token JSON as a single
   Keychain secret. The `garminconnect` library already supports loading tokens from an in-memory
   string — `Garmin.login(tokenstore)` treats any string longer than 512 characters as inline
   token data (`self.client.loads(tokenstore)`) rather than a file path — so no monkeypatching or
   temp files are needed. Real OS-level encryption, minimal code, fits personal-Mac-only scope.
   Trade-off: macOS may show a one-time Keychain access prompt, and possibly again if `uv`'s
   cache path changes after a `uv` update.
2. **Encrypt the token file with a passphrase-derived key.** Rejected: the encryption key has to
   live somewhere. A key file just moves the plaintext problem down one level; a typed passphrase
   doesn't work for a background MCP server that authenticates without the user present.
3. **Third-party secrets manager (e.g. 1Password CLI).** Rejected: adds a dependency on an
   external tool and its own unlock/session friction, not justified for one small JSON blob.

## Architecture

New dependency: `keyring`.

Keychain identity: service `garmin-connect-mcp`, account `oauth-tokens`.

### `auth.py`

- `GarminConfig`, `get_env_file_path()`, `get_token_store()`, `get_token_base64_path()`,
  `DEFAULT_ENV_FILE`, `LOCAL_ENV_FILE` — **left byte-for-byte unchanged**, even though the new
  code path no longer calls the token-path helpers. Deleting them buys nothing and only creates
  conflict risk if upstream later touches those exact lines; leaving them alone means upstream
  changes to those functions apply with zero conflicts.
- `validate_credentials(config)` — **signature unchanged**. Body becomes
  `return load_tokens() is not None` (the `config` parameter is now unused, kept only so the
  call site in `middleware.py` doesn't need to change).
- New functions, appended (pure addition, no conflict risk):
  - `save_tokens(token_json: str) -> None` — `keyring.set_password(SERVICE, ACCOUNT, token_json)`
  - `load_tokens() -> str | None` — wraps `keyring.get_password(SERVICE, ACCOUNT)`; any
    `keyring.errors.KeyringError` is caught and treated as "no token" (returns `None`) rather
    than crashing the server.

### `client.py::init_garmin_client`

**Signature unchanged**: `init_garmin_client(config: GarminConfig, prompt_mfa=None) -> Garmin | None`.

Body: replace the file-existence check and `garmin.login(tokenstore_path)` with
`load_tokens()` + `garmin.login(token_json)` when a token is present. On failure/absence, fall
through to the existing credential-login branch unchanged — `Garmin(email=config.garmin_email,
password=config.garmin_password, prompt_mfa=prompt_mfa).login()`. On success, replace the
file `dump()`/base64-file-write calls with `save_tokens(garmin.client.dumps())`. All other
exception handling (MFA errors, `GarminConnectTooManyRequestsError`, generic `Exception`) is
untouched.

At runtime (called from `middleware.py`/`server.py`, no credentials ever set), an empty
`config.garmin_email`/`garmin_password` makes the library itself raise a clean
`GarminConnectAuthenticationError("Username and password are required")`, already caught by the
existing `except GarminConnectAuthenticationError` block, yielding `None`. No new error-handling
code is needed for this case.

### `middleware.py` and `server.py`

**Zero changes.** They already call `load_config()` → `validate_credentials()` →
`init_garmin_client()`; only what those functions do internally changes.

### `scripts/setup_auth.py`

Small surgical diff: remove the `set_key(str(env_path), "GARMIN_EMAIL", email)` /
`set_key(str(env_path), "GARMIN_PASSWORD", password)` calls and the `env_path` mkdir/touch lines.
Email and password remain local variables, passed directly into
`init_garmin_client(config, prompt_mfa=prompt_for_mfa)` as before. Update the success message to
reference Keychain instead of a file path.

## Data flow

**Auth flow (run once, or again if tokens go stale):**
1. `uvx garmin-connect-mcp auth` prompts for email/password/MFA interactively; nothing is written
   to disk at this point.
2. `init_garmin_client` finds no Keychain token yet, falls through to credential login against
   Garmin's servers.
3. On success, `save_tokens(garmin.client.dumps())` writes the token JSON into Keychain,
   overwriting any prior entry.

**Runtime flow (every tool call from Claude Desktop/Code):**
1. `ConfigMiddleware.on_call_tool` calls `validate_credentials()` → `load_tokens() is not None`.
2. `init_garmin_client(config)` (no MFA callback — MFA is only ever triggered from the
   interactive `auth` command) loads the Keychain token and calls `garmin.login(token_json)`.
   No prompts; no network credential call unless the library's own internal refresh logic
   decides the access token needs refreshing.
3. Client is wrapped and injected into context state; tool executes normally.

**Re-auth-needed flow:** if Keychain has no token, or token-login raises an auth/connection
error, the credential branch runs with empty `config.garmin_email`/`password`, so the library
raises a clean `GarminConnectAuthenticationError`, already caught, returning `None`. Middleware
turns that into the existing `ToolError` pointing at `garmin-connect-mcp auth`.

This means: after running `auth` once, tool calls work automatically going forward — same UX as
the current file-based tokens, just relocated to Keychain. Re-running `auth` is only needed again
if the underlying Garmin refresh token itself becomes invalid (password change, long inactivity,
Garmin-side revocation) — not a regression from current behavior.

## Testing (Detroit-style, per project CLAUDE.md)

The real boundary is the OS keychain, so tests swap in a small in-memory fake `keyring` backend
(dict-backed, registered via `keyring.set_keyring()` for the test, restored after) rather than
mocking our own functions.

- `tests/test_auth.py`: asserts observable round-trips — `load_tokens()` returns `None` before
  any save; `save_tokens(x)` then `load_tokens()` returns `x`; a second `save_tokens(y)`
  overwrites so `load_tokens()` returns `y`.
- `tests/test_client.py`: stubs the `garminconnect.Garmin` class to exercise the three real
  branches of `init_garmin_client` — valid Keychain token (no credential login attempted),
  no token + credentials supplied (credential login runs and `save_tokens` is called, verified
  via a subsequent `load_tokens()`), and no token + no credentials (returns `None` cleanly).
- `tests/test_middleware.py`: asserts `ToolError` is raised when no token is present, and that a
  successful path injects the client into context state before calling `call_next`.

All run under `make test` / `make can-release`.

## Migration notes

This design does not delete the existing plaintext files — the user removes them manually after
verifying the new Keychain-backed flow works (consistent with earlier file deletions in this
project, which the user has always performed themselves). After this change ships:

1. Run `uvx garmin-connect-mcp auth` once to populate Keychain (re-entering credentials/MFA).
2. Verify a tool call succeeds.
3. Manually delete `~/.garminconnect.env`, `~/.garminconnect/`, and `~/.garminconnect_base64`.
