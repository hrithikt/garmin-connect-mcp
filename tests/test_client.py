"""Tests for init_garmin_client's keychain-backed authentication branches."""

import keyring
import keyring.errors
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


def test_saves_tokens_refreshed_during_token_login(mocker):
    save_tokens("stored-token-blob")
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_garmin_cls.return_value.client.dumps.return_value = "refreshed-token-blob"

    init_garmin_client(GarminConfig())

    assert load_tokens() == "refreshed-token-blob"


def test_keychain_write_failure_does_not_fail_token_login(mocker):
    save_tokens("stored-token-blob")
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_instance = mock_garmin_cls.return_value
    mock_instance.client.dumps.return_value = "refreshed-token-blob"
    mocker.patch.object(
        keyring.get_keyring(), "set_password", side_effect=keyring.errors.KeyringError("denied")
    )

    result = init_garmin_client(GarminConfig())

    assert result is mock_instance


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
