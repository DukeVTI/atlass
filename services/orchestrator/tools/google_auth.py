import os
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

logger = logging.getLogger("atlas.tools.google_auth")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

def get_google_credentials() -> Credentials | None:
    """
    Retrieves OAuth 2.0 credentials for Google APIs.
    Expects `token.json` to be mounted via Docker volume. 
    If not found, it warns the user to generate it locally.
    """
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.error(f"Failed to load token.json: {e}")

    # If there are no valid credentials available, let the user know.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.error(f"Failed to refresh Google Token: {e}")
                return None
        else:
            logger.warning("No valid Google token.json found. You must run the OAuth flow locally and mount the generated token.json to the Orchestrator.")
            return None
            
    return creds
