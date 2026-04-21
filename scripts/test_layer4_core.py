import os
import json
import urllib.request
import urllib.error
import subprocess

# Manual .env parsing to avoid 'python-dotenv' dependency
def load_env_manual():
    env = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("'").strip('"')
    return env

ENV = load_env_manual()

# Configuration
ORCHESTRATOR_URL = ENV.get("ORCHESTRATOR_URL", "http://localhost:8001")
API_BASE_URL = ENV.get("API_BASE_URL", "http://localhost:8000")

def test_layer4():
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
    url = f"{ORCHESTRATOR_URL}/chat"
    payload = json.dumps({
        "user_id": 12345,
        "text": "Hello Atlas. This is a system health check."
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            print(f"  - Orchestrator Response: {data.get('response')[:50]}...")
            print("  - Status: ✅ Success")
    except Exception as e:
        print(f"  - Status: ❌ Failed to connect to Orchestrator: {e}")

    # 3. Test Web Search Tool (Integrated)
    print("\n[3/4] Testing Web Search Tool...")
    payload_search = json.dumps({
        "user_id": 12345,
        "text": "Search for the latest news about Anthropic Claude."
    }).encode("utf-8")
    
    req_search = urllib.request.Request(url, data=payload_search, headers={"Content-Type": "application/json"})
    try:
        # Long timeout for search
        with urllib.request.urlopen(req_search, timeout=30) as response:
            data = json.loads(response.read().decode())
            print(f"  - Search Result: {data.get('response')[:100]}...")
            print("  - Status: ✅ Success")
    except Exception as e:
        print(f"  - Status: ❌ Web Search test failed: {e}")

    # 4. Verify Audit Log Entry via Docker exec (to avoid DB driver dependencies)
    print("\n[4/4] Verifying Audit Log Records (via Docker)...")
    try:
        query = "SELECT tool_name, status, timestamp FROM audit_logs ORDER BY timestamp DESC LIMIT 3;"
        cmd = [
            "docker", "compose", "exec", "postgres", 
            "psql", "-U", "atlas", "-d", "atlas", "-c", query
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and "tool_name" in result.stdout:
            print(result.stdout)
            print("  - Status: ✅ Success")
        else:
            print(f"  - Status: ❌ No audit log entries found or DB unreachable. Error: {result.stderr}")
    except Exception as e:
        print(f"  - Status: ❌ Database check failed: {e}")

    print("\n" + "-" * 50)
    print("Layer 4 Core Validation Complete.")

if __name__ == "__main__":
    test_layer4()
