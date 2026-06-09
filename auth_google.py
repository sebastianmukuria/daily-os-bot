"""
Run this once locally to authenticate with Google Calendar.
It opens a browser, you log in, and saves token.json for the bot to use.

Usage:
  1. Go to https://console.cloud.google.com/
  2. Create a project -> Enable "Google Calendar API"
  3. OAuth consent screen: External, add your email as a test user
  4. Credentials -> Create -> OAuth 2.0 Client ID -> Desktop app -> Download JSON
  5. Save the downloaded file as credentials.json in this directory
  6. Run: python3 auth_google.py

For local running: token.json is all you need.
For Railway/Render (ephemeral filesystem): copy the printed JSON into an env var
named GOOGLE_TOKEN_JSON in your hosting dashboard.
"""

import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",  # read mail + apply JobTracker label
]
CREDS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def main():
    if not os.path.exists(CREDS_PATH):
        print(f"Error: credentials.json not found at {CREDS_PATH}")
        print("Download it from Google Cloud Console -> Credentials -> OAuth 2.0 Client IDs")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    token_json = creds.to_json()
    with open(TOKEN_PATH, "w") as f:
        f.write(token_json)

    print(f"\nAuth successful. Token saved to {TOKEN_PATH}")
    print("Calendar is ready when running locally.\n")
    print("=" * 70)
    print("FOR CLOUD DEPLOY (Railway/Render): set this as the GOOGLE_TOKEN_JSON env var")
    print("=" * 70)
    print(token_json)
    print("=" * 70)


if __name__ == "__main__":
    main()
