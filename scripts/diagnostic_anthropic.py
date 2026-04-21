import os
import json
import urllib.request
import urllib.error

def check_models():
    # Attempt to read .env manually to avoid dependency on python-dotenv
    api_key = None
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env file.")
        return

    print(f"Diagnosing available models for Key: {api_key[:10]}...")
    
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                models = data.get("data", [])
                print("\nSUCCESS! Models available to your account:")
                print("-" * 50)
                for m in models:
                    print(f"- ID: {m['id']} (Created: {m.get('created_at', 'N/A')})")
                print("-" * 50)
                
                # Check for any "haiku" variants specifically
                haiku_variants = [m['id'] for m in models if "haiku" in m['id'].lower()]
                if haiku_variants:
                    print(f"\nFOUND {len(haiku_variants)} HAIKU VARIANT(S):")
                    for v in haiku_variants:
                        print(f"  >>> {v}")
                else:
                    print("\nWARNING: No 'haiku' model IDs found in your allowed list!")
            else:
                print(f"FAILED: API returned {response.status}")
    except urllib.error.HTTPError as e:
        print(f"FAILED: API returned {e.code}")
        print(f"Error Response: {e.read().decode()}")
    except Exception as e:
        print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    check_models()
