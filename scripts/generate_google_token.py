import os
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
    Reads credentials.json and spits out the authenticated token.json.
    """
    
    # Use explicit absolute path or relative to project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    
    creds_path = os.path.join(project_dir, "credentials.json")
    token_path = os.path.join(project_dir, "token.json")

    if not os.path.exists(creds_path):
        print(f"❌ Error: Could not find '{creds_path}'!")
        print("Please download your OAuth 2.0 Client credentials from the Google Cloud Console and save it to the root of the Atlas project as 'credentials.json'.")
        return

    print("🚀 Starting Google OAuth Flow...")
    
    try:
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
        # This will pop open your default web browser
        creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())
            
        print(f"✅ Success! Your authenticated token has been saved to: {token_path}")
        print("You can now safely map this file into your docker-compose volume for the orchestrator to read.")
        
    except Exception as e:
        print(f"❌ An error occurred during the OAuth flow: {e}")

if __name__ == "__main__":
    generate_token()
