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
