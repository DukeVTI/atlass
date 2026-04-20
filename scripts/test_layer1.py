#!/usr/bin/env python3
"""
Test script for Atlas Layer 1.
Validates the infrastructure is up, services are reachable, and health endpoints map correctly.
"""

import urllib.request
import json
import sys
import time

def check_endpoint(url: str, name: str) -> bool:
    try:
        print(f"Checking {name} at {url}...")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                print(f"✅ {name} is OK: {data}")
                return True
    except urllib.error.HTTPError as e:
        print(f"❌ {name} returned HTTP {e.code}")
        try:
            error_data = json.loads(e.read().decode())
            print(f"   Details: {json.dumps(error_data, indent=2)}")
        except Exception:
            print(f"   Details: could not parse error response")
        return False
    except Exception as e:
        print(f"❌ {name} failed: {e}")
        return False

def main():
    print("====================================")
    print("ATLAS LAYER 1: INFRASTRUCTURE TEST")
    print("====================================")
    print("Make sure you have run 'docker-compose up -d' before running this.")
    print("Waiting 5 seconds for services to settle...")
    time.sleep(5)
    
    success = True
    
    # Check FastAPI Health
    if not check_endpoint("http://localhost:8000/health", "API Liveness Probe"):
        success = False
        
    # Check FastAPI Detailed Health (Postgres, Redis, ChromaDB)
    if not check_endpoint("http://localhost:8000/health/detailed", "API Detailed Health"):
        success = False
        
    # Check WhatsApp sidecar (Layer 1 stub)
    # The whatsapp container port isn't exposed in docker-compose.yml to the host,
    # so we might not be able to test it directly from the host.
    # We will rely on docker ps or similar if needed.

    if success:
        print("\n🎉 LAYER 1 VERIFICTION PASSED!")
        print("You are ready to proceed to Layer 2 (Telegram Bot).")
        sys.exit(0)
    else:
        print("\n⚠️ LAYER 1 VERIFICTION FAILED.")
        print("Check your docker-compose logs and ensure all containers are running.")
        sys.exit(1)

if __name__ == "__main__":
    main()
