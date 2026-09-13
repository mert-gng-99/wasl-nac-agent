"""Consent ledger.

CAMARA identity, location and geofencing APIs are only lawful with the consent
of the line owner. That is not a footnote for this platform, it is the reason
each of the seven products is shaped the way it is: a rider signs at induction,
a pilgrim signs when handed the local SIM, a bank customer consents at the
moment of the transfer.

So consent lives in the transport path, not in a policy document. Every
:class:`~core.camara.CamaraClient` call that touches a regulated scope asks
this ledger first, and an ungranted call raises before a request is built.

Grants are scoped three ways:

*   **scope** - which family of calls (``location:verify``, ``fraud:sim-swap``)
*   **subject** - which line
*   **window** - from when to when, so access ends when the trip, shift or
    transfer ends rather than living forever
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

ALL_SCOPES = (
    "identity:verify",
    "fraud:sim-swap",
    "fraud:device-swap",
    "location:verify",
    "location:retrieve",
    "location:geofence",
    "device:status",
    "network:insights",
    "network:qod",
    "network:slice",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ConsentGrant:
    subject: str
    scopes: List[str]
    granted_at: datetime
    expires_at: Optional[datetime]
    basis: str = "explicit"
    reference: str = ""
    revoked_at: Optional[datetime] = None

    def active(self, at: Optional[datetime] = None) -> bool:
        moment = at or _now()
        if self.revoked_at is not None and self.revoked_at <= moment:
            return False
        if self.granted_at > moment:
            return False
        if self.expires_at is not None and self.expires_at <= moment:
            return False
        return True

    def covers(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes

    def to_dict(self) -> Dict[str, object]:
        return {
            "subject": self.subject,
            "scopes": list(self.scopes),
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "basis": self.basis,
            "reference": self.reference,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "active": self.active(),
        }


class ConsentLedger:
    """Thread-safe in-memory consent store.

    In-memory is the right call for a prototype and the wrong one for
    production, where this belongs in the same database as the enrolment
    record. The interface is what matters: one ``allows`` question, asked on
    every regulated call.
    """

    def __init__(self) -> None:
        self._grants: Dict[str, List[ConsentGrant]] = {}
        self._denials: List[Dict[str, str]] = []
        self._lock = threading.RLock()

    # -- writing -------------------------------------------------------------

    def grant(
        self,
        subject: str,
        scopes: Iterable[str],
        *,
        duration: Optional[timedelta] = None,
        basis: str = "explicit",
        reference: str = "",
    ) -> ConsentGrant:
        scope_list = list(scopes)
        unknown = [s for s in scope_list if s not in ALL_SCOPES and s != "*"]
        if unknown:
            raise ValueError("unknown consent scopes: " + ", ".join(unknown))
        now = _now()
        grant = ConsentGrant(
            subject=_key(subject),
            scopes=scope_list,
            granted_at=now,
            expires_at=(now + duration) if duration else None,
            basis=basis,
            reference=reference,
        )
        with self._lock:
            self._grants.setdefault(grant.subject, []).append(grant)
        return grant

    def revoke(self, subject: str) -> int:
        """Withdraw every grant for a line. Enrolled people can always leave."""
        now = _now()
        count = 0
        with self._lock:
            for grant in self._grants.get(_key(subject), []):
                if grant.revoked_at is None:
                    grant.revoked_at = now
                    count += 1
        return count

    # -- reading -------------------------------------------------------------

    def allows(self, scope: str, subject: str) -> bool:
        with self._lock:
            grants = list(self._grants.get(_key(subject), []))
        for grant in grants:
            if grant.active() and grant.covers(scope):
                return True
        with self._lock:
            self._denials.append(
                {
                    "subject": _key(subject),
                    "scope": scope,
                    "at": _now().isoformat(),
                }
            )
            self._denials[:] = self._denials[-200:]
        return False

    def has_history(self, subject: str) -> bool:
        """Has this line ever been enrolled, even if consent was withdrawn since?

        Auto-enrolment exists so a reviewer can type any number and get a
        decision. It must never quietly undo a withdrawal, so callers check
        history rather than current state before granting on someone's behalf.
        """
        with self._lock:
            return bool(self._grants.get(_key(subject)))

    def scopes_for(self, subject: str) -> List[str]:
        with self._lock:
            grants = list(self._grants.get(_key(subject), []))
        out: List[str] = []
        for grant in grants:
            if grant.active():
                out.extend(grant.scopes)
        return sorted(set(out))

    def subjects(self) -> List[str]:
        with self._lock:
            return sorted(self._grants)

    def recent_denials(self, limit: int = 20) -> List[Dict[str, str]]:
        with self._lock:
            return list(reversed(self._denials[-limit:]))

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            grants = {
                subject: [g.to_dict() for g in items]
                for subject, items in self._grants.items()
            }
        active = sum(1 for items in grants.values() for g in items if g["active"])
        return {
            "subjects": len(grants),
            "active_grants": active,
            "grants": grants,
            "recent_denials": self.recent_denials(),
        }


def _key(subject: str) -> str:
    cleaned = (subject or "").strip().replace(" ", "").replace("-", "")
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned
