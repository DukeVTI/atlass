import os
import httpx
import asyncio
from dotenv import load_dotenv

async def check_models():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env file.")
        return

    print(f"Diagnosing available models for Key: {api_key[:10]}...")
    
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
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
                print(f"FAILED: API returned {resp.status_code}")
                print(f"Error Response: {resp.text}")
        except Exception as e:
            print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check_models())
