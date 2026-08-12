"""Make one real x402 payment against our own endpoint, to trigger Bazaar indexing.

Why this exists
---------------
CDP will not index a resource just because it returns a valid 402. Per their
seller docs, "every validated endpoint is eligible for indexing in the CDP
Bazaar after a successful settled payment" -- validation alone leaves the
listing at index: null. So the only way onto the Bazaar is to actually pay
once. This script is that one payment.

Safety
------
This moves real money on Base mainnet, so it refuses to do anything by
default. Without --confirm it prints the wallet, the balance and the exact
amount, then exits. Nothing is signed until you pass --confirm.

The key is read from X402_PAYER_KEY, never from a command-line argument --
argv ends up in shell history and in `ps` output for every user on the box.

Usage
-----
    export X402_PAYER_KEY=0x...          # a throwaway wallet, small balance
    python scripts/pay_once.py           # dry run: shows what would happen
    python scripts/pay_once.py --confirm # signs and settles

The payer needs USDC but does NOT need ETH for gas. The "exact" scheme uses
EIP-3009 transferWithAuthorization: you sign an off-chain authorization and
the facilitator submits it on-chain and pays the gas itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

RESOURCE = "https://sports-predictor-api.fly.dev/api/v1/probabilities"
USDC_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC = "https://mainnet.base.org"
NETWORK = "eip155:8453"


def _usdc_balance(address: str) -> float:
    """Read the payer's USDC balance straight from an RPC node.

    Checked before paying because the failure mode otherwise is a signature
    the facilitator rejects at settle time, which is a much less obvious
    error message than "you have no USDC".
    """
    import httpx

    data = "0x70a08231" + address[2:].lower().rjust(64, "0")
    r = httpx.post(
        BASE_RPC,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": USDC_MAINNET, "data": data}, "latest"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return int(r.json()["result"], 16) / 1e6


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="actually sign and settle. Without this it is a dry run.")
    ap.add_argument("--resource", default=RESOURCE)
    args = ap.parse_args()

    key = os.getenv("X402_PAYER_KEY", "").strip()
    if not key:
        print("X402_PAYER_KEY is not set.", file=sys.stderr)
        print("Use a throwaway wallet holding a dollar or two, not your main one.",
              file=sys.stderr)
        return 2

    from eth_account import Account
    from x402 import x402Client
    from x402.http.clients import x402HttpxClient
    from x402.mechanisms.evm.exact.client import ExactEvmScheme

    account = Account.from_key(key)
    balance = _usdc_balance(account.address)

    # Ask the endpoint what it wants before deciding anything, so the numbers
    # printed below are the live ones rather than constants in this file.
    import httpx
    import base64

    async with httpx.AsyncClient(timeout=60) as probe:
        challenge = await probe.get(args.resource)
    raw = challenge.headers.get("payment-required", "")
    if not raw:
        print(f"No payment-required header (HTTP {challenge.status_code}).", file=sys.stderr)
        return 1
    req = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    accept = req["accepts"][0]
    price = int(accept["amount"]) / 1e6

    print(f"  resource : {args.resource}")
    print(f"  network  : {accept['network']}"
          f"{'  (MAINNET -- real funds)' if accept['network'] == NETWORK else ''}")
    print(f"  price    : {price} USDC")
    print(f"  payTo    : {accept['payTo']}")
    print(f"  payer    : {account.address}")
    print(f"  balance  : {balance} USDC")

    if balance < price:
        print(f"\nNot enough USDC: need {price}, have {balance}.", file=sys.stderr)
        print(f"Send USDC on Base to {account.address} and re-run.", file=sys.stderr)
        return 1

    if not args.confirm:
        print("\nDry run. Nothing was signed. Re-run with --confirm to pay.")
        return 0

    client = x402Client()
    # ExactEvmScheme auto-wraps an eth_account LocalAccount, so the account
    # goes straight in. Registered against the network the challenge names,
    # not a constant, so this still works if the server moves chains.
    client.register(accept["network"], ExactEvmScheme(account))

    async with x402HttpxClient(client, timeout=90) as paid:
        resp = await paid.get(args.resource)

    print(f"\n  HTTP {resp.status_code}")
    settle = resp.headers.get("payment-response") or resp.headers.get("x-payment-response")
    if settle:
        try:
            info = json.loads(base64.b64decode(settle + "=" * (-len(settle) % 4)))
            tx = info.get("transaction") or info.get("txHash")
            print(f"  settled  : {info.get('success')}")
            if tx:
                print(f"  tx       : https://basescan.org/tx/{tx}")
        except Exception:
            print(f"  settle header (unparsed): {settle[:120]}")

    if resp.status_code == 200:
        body = resp.json()
        print(f"  fixtures : {body.get('count')}")
        print("\nPaid and served. Bazaar indexing follows a settled payment,")
        print("so re-check the listing in a few minutes.")
        return 0

    print(f"  body: {resp.text[:300]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
