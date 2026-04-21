import os
import httpx
import asyncio
import json
import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8001")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://atlas:change_me_before_deploy@localhost/atlas")

async def test_layer4():
    print("🚀 Starting Layer 4 Core Validation Test...")
    print("-" * 50)

    # 1. Check Google Credentials Existence
    print("\n[1/4] Checking Google Credentials...")
    creds_exist = os.path.exists("credentials.json")
    token_exist = os.path.exists("token.json")
    print(f"  - credentials.json: {'✅ Found' if creds_exist else '❌ Missing'}")
    print(f"  - token.json:       {'✅ Found' if token_exist else '❌ Missing (Run generate_google_token.py)'}")

    # 2. Test Orchestrator Connectivity
    print("\n[2/4] Testing Orchestrator Response...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/chat", json={
                "user_id": 12345,
                "text": "Hello Atlas. This is a system health check."
            })
            resp.raise_for_status()
            print(f"  - Orchestrator Response: {resp.json().get('response')[:50]}...")
            print("  - Status: ✅ Success")
    except Exception as e:
        print(f"  - Status: ❌ Failed to connect to Orchestrator: {e}")

    # 3. Test Web Search Tool (Integrated)
    print("\n[3/4] Testing Web Search Tool...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/chat", json={
                "user_id": 12345,
                "text": "Search for the latest news about Anthropic Claude."
            })
            resp.raise_for_status()
            data = resp.json()
            print(f"  - Search Result: {data.get('response')[:100]}...")
            print("  - Status: ✅ Success")
    except Exception as e:
        print(f"  - Status: ❌ Web Search test failed: {e}")

    # 4. Verify Audit Log Entry
    print("\n[4/4] Verifying Audit Log Records...")
    try:
        # We check the database directly
        conn = await asyncpg.connect(POSTGRES_DSN)
        rows = await conn.fetch("SELECT tool_name, status, timestamp FROM audit_logs ORDER BY timestamp DESC LIMIT 5;")
        await conn.close()
        
        if rows:
            print(f"  - Found {len(rows)} recent audit log entries:")
            for row in rows:
                print(f"    >>> {row['timestamp'].strftime('%H:%M:%S')} | Tool: {row['tool_name']} | Status: {row['status']}")
            print("  - Status: ✅ Success")
        else:
            print("  - Status: ❌ No audit log entries found in database!")
    except Exception as e:
        print(f"  - Status: ❌ Database check failed: {e}")

    print("\n" + "-" * 50)
    print("Layer 4 Core Validation Complete.")

if __name__ == "__main__":
    asyncio.run(test_layer4())
