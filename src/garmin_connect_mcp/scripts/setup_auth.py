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
