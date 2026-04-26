import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# The scopes must match exactly what Atlas will request later
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

def generate_token():
    """
    Spins up a local server to handle the Google OAuth 2.0 flow.
    Reads ./config/credentials.json and writes ./config/token.json.
    Run this LOCALLY (not on VPS) — it opens a browser window.
    """
    script_dir = Path(os.path.abspath(__file__)).parent
    project_dir = script_dir.parent
    config_dir = project_dir / "config"

    creds_path = config_dir / "credentials.json"
    token_path = config_dir / "token.json"

    if not creds_path.exists():
        print(f"❌ Error: Could not find '{creds_path}'!")
        print("Steps:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. APIs & Services → Credentials")
        print("  3. Create OAuth 2.0 Client ID → Desktop App")
        print("  4. Download JSON → save as config/credentials.json")
        return

    print("🚀 Starting Google OAuth Flow...")
    print("A browser window will open. Sign in and grant permissions.")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            success_message='✅ Authorization successful! You can close this tab.'
        )

        config_dir.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w") as token:
            token.write(creds.to_json())

        print(f"\n✅ Success! token.json saved to: {token_path}")
        print("\nNext: Copy both files to your VPS:")
        print(f"  scp config/credentials.json config/token.json root@<vps-ip>:/home/atlas/atlass/config/")
        print("\nThen restart the orchestrator:")
        print("  docker-compose up -d orchestrator")

    except Exception as e:
        print(f"❌ OAuth flow failed: {e}")

if __name__ == "__main__":
    generate_token()
