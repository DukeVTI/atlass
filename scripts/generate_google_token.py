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
    print("NOTE: Since you are likely on a VPS, the browser will NOT open automatically.")
    
    try:
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
        
        # Option 1: Headless flow (user copies URL to local browser)
        # We use a fixed port to make SSH tunneling easy if needed
        # But run_local_server with open_browser=False is standard
        creds = flow.run_local_server(
            port=8080, 
            host='localhost', 
            open_browser=False,
            authorization_prompt_message='Please visit this URL to authorize Atlas: {url}',
            success_message='✅ Authorization successful! You can close this tab.'
        )
        
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())
            
        print(f"\n✅ Success! Your authenticated token has been saved to: {token_path}")
        print("IMPORTANT: Ensure you map this file in your docker-compose.yml so Atlas can use it.")
        
    except Exception as e:
        print(f"❌ An error occurred during the OAuth flow: {e}")
        print("\nTIP: If you are on a VPS, run this on your local machine FIRST:")
        print(f"  ssh -L 8080:localhost:8080 azureuser@shadowfight")
        print("Then run this script and open the link in your LOCAL browser.")

if __name__ == "__main__":
    generate_token()
