import os
import httpx
import logging
from typing import Any
from tools.base import Tool

logger = logging.getLogger("atlas.tools.paystack")

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
BASE_URL = "https://api.paystack.co"

def _get_headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

class PaystackBalanceTool(Tool):
    name = "get_balance"
    description = "Retrieves the Paystack account/wallet balance."
    is_destructive = False

    schema = {
        "name": "get_balance",
        "description": "Fetch Paystack balance.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }

    async def run(self, **kwargs) -> Any:
        if not PAYSTACK_SECRET_KEY: return "Paystack key missing."
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{BASE_URL}/balance", headers=_get_headers())
                resp.raise_for_status()
                data = resp.json()["data"]
                return "\n".join([f"Currency: {b['currency']} | Balance: {b['balance']}" for b in data])
        except Exception as e:
            return f"Error fetching balance: {e}"

class PaystackCustomerTool(Tool):
    name = "get_customer"
    description = "Retrieves a Paystack customer details by exact email."
    is_destructive = False

    schema = {
        "name": "get_customer",
        "description": "Fetch a customer by email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string"}
            },
            "required": ["email"]
        }
    }

    async def run(self, email: str, **kwargs) -> Any:
        if not PAYSTACK_SECRET_KEY: return "Paystack key missing."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{BASE_URL}/customer/{email}", headers=_get_headers())
                resp.raise_for_status()
                c = resp.json()["data"]
                return f"Customer: {c['first_name']} {c['last_name']}\nEmail: {c['email']}\nPhone: {c.get('phone', 'N/A')}\nID: {c['id']}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return "Customer not found."
            try:
                error_msg = e.response.json().get('message', str(e))
                return f"Paystack API Error: {error_msg}"
            except Exception:
                return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

class PaystackTransactionsTool(Tool):
    name = "get_transactions"
    description = "Fetch recent Paystack transactions."
    is_destructive = False

    schema = {
        "name": "get_transactions",
        "description": "Fetch recent transactions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of transactions to fetch"}
            }
        }
    }

    async def run(self, limit: int = 5, **kwargs) -> Any:
        if not PAYSTACK_SECRET_KEY: return "Paystack key missing."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{BASE_URL}/transaction?perPage={limit}", headers=_get_headers())
                resp.raise_for_status()
                txs = resp.json()["data"]
                
                if not txs: return "No transactions found."
                res = ["Recent Transactions:"]
                for t in txs:
                    amount = t['amount'] / 100
                    res.append(f"- {amount} {t['currency']} | Status: {t['status']} | Ref: {t['reference']} | Email: {t['customer']['email']}")
                return "\n".join(res)
        except Exception as e:
            return f"Error fetching transactions: {e}"

class PaystackTransferTool(Tool):
    name = "initiate_transfer"
    description = "High Risk. Initiates a transfer payout. Triggers confirmation gate."
    is_destructive = True

    schema = {
        "name": "initiate_transfer",
        "description": "Initiate a Paystack transfer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Amount in kobo (multiply Naira by 100)"},
                "recipient_code": {"type": "string", "description": "The Paystack recipient code (e.g. RCP_1a2b3c)"},
                "reason": {"type": "string"}
            },
            "required": ["amount", "recipient_code", "reason"]
        }
    }

    async def run(self, amount: int, recipient_code: str, reason: str, **kwargs) -> Any:
        if not PAYSTACK_SECRET_KEY: return "Paystack key missing."
        try:
            payload = {
                "source": "balance",
                "amount": amount,
                "recipient": recipient_code,
                "reason": reason
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{BASE_URL}/transfer", json=payload, headers=_get_headers())
                resp.raise_for_status()
                data = resp.json()["data"]
                return f"Transfer initiated successfully. Transfer code: {data['transfer_code']}. Status: {data['status']}"
        except httpx.HTTPStatusError as e:
            try:
                error_msg = e.response.json().get('message', str(e))
                logger.error(f"Transfer Paystack API Error: {error_msg}")
                return f"Transfer failed immediately at provider: {error_msg}"
            except Exception:
                logger.error(f"Transfer error: {e}")
                return f"Transfer failed: {e}"
        except Exception as e:
            logger.error(f"Transfer error: {e}")
            return f"Transfer failed: {e}"
