"""Wallet identity for picks: every prediction is a signed statement.

Why sign each pick rather than issue a session
----------------------------------------------
A session token proves you logged in once. Signing the pick itself proves
*this specific prediction* came from *this wallet* at *this time*, and it does
so in a way nobody -- including whoever runs this server -- can forge after the
fact. For a leaderboard whose only value is that predictions cannot be revised,
that is the difference between "trust our database" and "check it yourself".

It also composes with what comes next: a signed pick is already the payload an
onchain leaderboard would need. Nothing has to be re-architected to publish it.

What the signature covers
-------------------------
The message contains the fixture, the outcome, the confidence and an issue
time. All of it. Signing a bare nonce and submitting the pick separately would
let a caller sign one thing and send another, which is the usual way this gets
built wrong.

Replay is bounded two ways: the message carries an issue time and is rejected
outside a short window, and a pick can only be stored once per wallet and
fixture anyway, so a captured signature has nothing to re-submit against.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# How stale a signature may be. Long enough to sign, read and submit without
# racing a clock; short enough that a captured message is not useful later.
MAX_AGE = timedelta(minutes=15)
# Small allowance for a client clock running ahead of ours.
MAX_SKEW = timedelta(minutes=2)

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

TEMPLATE = """Beat the Model — prediction

league: {league}
fixture: {home} v {away}
date: {date}
pick: {pick}
confidence: {confidence:.2f}
issued: {issued}

Signing this records the prediction above. It cannot be changed afterwards."""


def build_message(*, league: str, date: str, home: str, away: str,
                  pick: str, confidence: float, issued: str) -> str:
    """The exact text a wallet is asked to sign.

    Built server-side from the submitted fields and compared against what the
    signature recovers, so a caller cannot sign one prediction and file a
    different one.
    """
    return TEMPLATE.format(league=league, home=home, away=away, date=date,
                           pick=pick, confidence=float(confidence),
                           issued=issued)


def _parse_issued(issued: str) -> datetime:
    ts = datetime.fromisoformat(issued.replace("Z", "+00:00"))
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def recover_signer(message: str, signature: str) -> str:
    """Recover the address that produced this signature, lowercased."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    try:
        return Account.recover_message(
            encode_defunct(text=message), signature=signature).lower()
    except Exception as e:
        raise ValueError(f"could not verify that signature: {e}") from e


def verify(*, address: str, signature: str, league: str, date: str, home: str,
           away: str, pick: str, confidence: float, issued: str) -> str:
    """Check a signed pick end to end, returning the verified lowercase address.

    Raises ValueError on anything that does not add up, so the caller can turn
    every failure into one 400 rather than leaking which check failed.
    """
    if not _ADDRESS_RE.match(address or ""):
        raise ValueError("address must be a 0x EVM address")

    try:
        issued_at = _parse_issued(issued)
    except Exception as e:
        raise ValueError(f"issued must be an ISO-8601 UTC timestamp: {e}") from e

    now = datetime.now(timezone.utc)
    if issued_at > now + MAX_SKEW:
        raise ValueError("that signature is timestamped in the future")
    if now - issued_at > MAX_AGE:
        raise ValueError("that signature has expired, please sign again")

    message = build_message(league=league, date=date, home=home, away=away,
                            pick=pick, confidence=confidence, issued=issued)
    signer = recover_signer(message, signature)

    # Compare recovered against claimed rather than trusting either alone: the
    # claimed address is what the client says, the recovered one is what the
    # maths says, and only their agreement means anything.
    if signer != address.lower():
        raise ValueError("that signature does not match the address given")
    return signer


def display_name(address: str) -> str:
    """Short form for a public table: 0x1234…abcd."""
    a = address.lower()
    return f"{a[:6]}…{a[-4:]}"
