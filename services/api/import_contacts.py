import asyncio
import json
import os
import sys
import asyncpg
from dotenv import load_dotenv

async def main():
    if len(sys.argv) < 2:
        print("Usage: python import_contacts.py <path_to_json>")
        sys.exit(1)

    json_path = sys.argv[1]
    
    # Load env variables
    load_dotenv()

    # In Docker, we use the env vars directly
    password = os.getenv("POSTGRES_PASSWORD", "atlas")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    
    dsn = f"postgresql://atlas:{password}@{host}:{port}/atlas"

    print(f"Loading JSON from {json_path}...")
    try:
        if not os.path.exists(json_path):
            print(f"File not found: {json_path}")
            sys.exit(1)
            
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to load JSON: {e}")
        sys.exit(1)

    contacts = data.get("contacts", [])
    print(f"Found {len(contacts)} contacts. Connecting to DB...")

    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"Failed to connect to DB at {dsn}: {e}")
        sys.exit(1)

    print("Importing contacts...")
    
    records = []
    for c in contacts:
        cid = c.get("id")
        name = c.get("name")
        whatsapp = c.get("whatsapp")
        phone_raw = c.get("phone", [])
        phone_json = json.dumps(phone_raw)
        vip = c.get("vip", False)
        
        if cid and name and whatsapp:
            records.append((cid, name, whatsapp, phone_json, vip))

    print(f"Filtered to {len(records)} valid WhatsApp contacts.")

    if not records:
        print("No valid contacts to insert.")
        await conn.close()
        sys.exit(0)

    try:
        await conn.executemany("""
            INSERT INTO contacts (contact_id, name, whatsapp, phone, vip)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT (contact_id) DO UPDATE SET
                name = EXCLUDED.name,
                whatsapp = EXCLUDED.whatsapp,
                phone = EXCLUDED.phone,
                vip = EXCLUDED.vip;
        """, records)
        print("Import successful!")
    except Exception as e:
        print(f"Error during import: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
