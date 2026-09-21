"""Tests for the interactive 'garmin-connect-mcp auth' setup command."""

from garmin_connect_mcp.scripts.setup_auth import main


def test_reads_password_without_echo_and_uses_it_to_authenticate(mocker):
    mocker.patch("builtins.input", return_value="user@test.com")
    mocker.patch("getpass.getpass", return_value="s3cret")
    mock_garmin_cls = mocker.patch("garmin_connect_mcp.client.Garmin")
    mock_garmin_cls.return_value.client.dumps.return_value = '{"di_token": "new"}'

    main()

    mock_garmin_cls.assert_called_once_with(
        email="user@test.com", password="s3cret", prompt_mfa=mocker.ANY
    )
