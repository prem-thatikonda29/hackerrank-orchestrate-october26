"""Read-side of the evidence index. All queries take the case's audit_at and never return later evidence as citable."""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX_PATH = REPO / "build" / "index.sqlite"


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


@dataclass
class Identity:
    user_id: str
    name: str
    current_email: str
    emails: dict[str, str] = field(default_factory=dict)   # email -> audit_id ("" for current snapshot)
    phones: dict[str, str] = field(default_factory=dict)   # normalized phone -> audit_id
    hashes: dict[str, str] = field(default_factory=dict)   # sha256 -> audit_id

    def keys(self) -> list[tuple[str, str, str]]:
        """(key_type, key_value, match_type) for every identifier this person has used."""
        out = [("user_ref", self.user_id, "user_id")]
        out += [("email", e, "current_email" if not a else "old_email") for e, a in self.emails.items()]
        out += [("phone", p, "current_phone" if not a else "old_phone") for p, a in self.phones.items()]
        out += [("hash", h, "current_hash" if not a else "old_hash") for h, a in self.hashes.items()]
        return out


@dataclass
class Observation:
    obs_id: int
    file_id: str
    captured_at: str
    observed_at: str
    system_id: str
    resource_id: str
    record_type: str
    copy: str
    presence: str
    pii: str | None
    flow: str | None
    restore: str | None
    retention_until: str | None
    source: str
    excerpt: str


@dataclass
class ResourceState:
    """State of one resource at audit_at, taken from the latest citable file that shows it."""
    resource_id: str
    system_id: str
    record_type: str
    presence: str
    pii: str | None
    flow: str | None
    restore: str | None
    retention_until: str | None
    evidence_id: str
    observed_at: str
    copies: dict[str, str]
    history: list[Observation]


class Index:
    def __init__(self, path: Path = INDEX_PATH) -> None:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        src.backup(self.db)
        src.close()
        self.db.row_factory = sqlite3.Row

    # ---- identities ----------------------------------------------------------------------------

    @cached_property
    def _email_owner(self) -> dict[str, str]:
        owners = {r["email"].lower(): r["user_id"] for r in self.db.execute("SELECT user_id, email FROM identities")}
        for r in self.db.execute("SELECT user_id, old_value, new_value FROM identity_history WHERE field_name='email'"):
            owners.setdefault(r["old_value"].lower(), r["user_id"])
            owners.setdefault(r["new_value"].lower(), r["user_id"])
        return owners

    def resolve_identity(self, email: str) -> Identity | None:
        user_id = self._email_owner.get(email.strip().lower())
        if not user_id:
            return None
        row = self.db.execute("SELECT * FROM identities WHERE user_id=?", (user_id,)).fetchone()
        ident = Identity(user_id=user_id, name=row["name"], current_email=row["email"].lower())
        ident.emails[row["email"].lower()] = ""
        ident.phones[norm_phone(row["phone"])] = ""
        ident.hashes[row["email_hash"].lower()] = ""
        for h in self.db.execute("SELECT * FROM identity_history WHERE user_id=? ORDER BY event_time", (user_id,)):
            for value in (h["old_value"], h["new_value"]):
                if h["field_name"] == "email":
                    ident.emails.setdefault(value.lower(), h["audit_id"])
                elif h["field_name"] == "phone":
                    ident.phones.setdefault(norm_phone(value), h["audit_id"])
                elif h["field_name"] == "email_hash":
                    ident.hashes.setdefault(value.lower(), h["audit_id"])
        return ident

    def owner_of_key(self, key_type: str, value: str) -> str | None:
        """Which known user an identifier belongs to (used to reject resources tied to someone else)."""
        if key_type == "user_ref":
            return value
        if key_type == "email":
            return self._email_owner.get(value)
        if key_type == "hash":
            r = self.db.execute("SELECT user_id FROM identities WHERE lower(email_hash)=?", (value,)).fetchone()
            if r:
                return r[0]
            r = self.db.execute("SELECT user_id FROM identity_history WHERE field_name='email_hash' AND (lower(old_value)=? OR lower(new_value)=?)", (value, value)).fetchone()
            return r[0] if r else None
        return None

    # ---- evidence ------------------------------------------------------------------------------

    def file_captured(self, file_id: str) -> str | None:
        r = self.db.execute("SELECT captured_at FROM files WHERE file_id=?", (file_id,)).fetchone()
        return r[0] if r else None

    def observations(self, resource_id: str) -> list[Observation]:
        rows = self.db.execute("SELECT * FROM obs WHERE resource_id=? ORDER BY captured_at, observed_at, obs_id", (resource_id,))
        return [Observation(**dict(r)) for r in rows]

    def keys_of(self, obs_id: int) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.db.execute("SELECT key_type, key_value FROM obs_keys WHERE obs_id=?", (obs_id,))]

    def search(self, identity: Identity, audit_at: str, allow_late_discovery: bool = True) -> dict[str, list[dict]]:
        """resource_id -> list of {obs_id, file_id, match_type, citable} for observations matching the identity.

        Late files (captured after audit_at) are only used for discovery, and only for lines dated before audit_at.
        """
        hits: dict[str, list[dict]] = {}
        for key_type, value, match_type in identity.keys():
            for r in self.db.execute(
                "SELECT o.obs_id, o.resource_id, o.file_id, o.captured_at, o.observed_at FROM obs_keys k JOIN obs o USING(obs_id) "
                "WHERE k.key_type=? AND k.key_value=?", (key_type, value)):
                citable = r["captured_at"] <= audit_at
                if not citable and not (allow_late_discovery and (r["observed_at"] or "9") <= audit_at):
                    continue
                hits.setdefault(r["resource_id"], []).append(
                    {"obs_id": r["obs_id"], "file_id": r["file_id"], "match_type": match_type, "citable": citable})
        return hits

    def other_owners(self, resource_id: str, user_id: str) -> set[str]:
        """Users other than user_id that observations of this resource are tied to."""
        owners = set()
        for r in self.db.execute("SELECT k.key_type, k.key_value FROM obs_keys k JOIN obs o USING(obs_id) WHERE o.resource_id=?", (resource_id,)):
            owner = self.owner_of_key(r[0], r[1])
            if owner and owner != user_id:
                owners.add(owner)
        return owners

    def state_at(self, resource_id: str, audit_at: str) -> ResourceState | None:
        obs = [o for o in self.observations(resource_id) if o.captured_at <= audit_at and (o.observed_at or o.captured_at) <= audit_at]
        if not obs:
            return None
        latest_file_time = max(o.captured_at for o in obs)
        latest = [o for o in obs if o.captured_at == latest_file_time]
        latest_time = max(o.observed_at for o in latest)
        in_file = [o for o in latest if o.file_id == latest[0].file_id]
        # Within the latest file, judge every copy (primary, replicas) by its newest line.
        by_copy: dict[str, Observation] = {}
        for o in sorted(in_file, key=lambda o: (o.observed_at, o.obs_id)):
            by_copy[o.copy or "-"] = o
        copies = list(by_copy.values())
        rtype = next((o.record_type for o in reversed(obs) if o.record_type), "")
        presence = "PRESENT" if any(o.presence == "PRESENT" for o in copies) else "ABSENT"
        restore = "ALLOWED" if any(o.restore == "ALLOWED" for o in copies) else ("BLOCKED" if any(o.restore == "BLOCKED" for o in copies) else None)
        # An export past its expiry is gone, and an archive past its retention end can no longer be restored (screenshot-derived dates).
        lapsed = any(o.source.startswith("link:") and o.retention_until and o.retention_until <= audit_at for o in copies)
        if lapsed and rtype == "USER_EXPORT":
            presence = "ABSENT"
        if lapsed and rtype == "BACKUP_ARCHIVE":
            restore = "BLOCKED"
        return ResourceState(
            resource_id=resource_id,
            system_id=copies[0].system_id,
            record_type=rtype,
            presence=presence,
            pii="EXPOSED" if any(o.pii == "EXPOSED" for o in copies) else ("CLEAN" if any(o.pii == "CLEAN" for o in copies) else None),
            flow="ACTIVE" if any(o.flow == "ACTIVE" for o in copies) else ("DISABLED" if any(o.flow == "DISABLED" for o in copies) else None),
            restore=restore,
            retention_until=next((o.retention_until for o in reversed(obs) if o.retention_until), None),
            evidence_id=copies[0].file_id,
            observed_at=latest_time,
            copies={c: f"{o.presence}/{o.pii or '-'}/{o.flow or o.restore or '-'}" for c, o in by_copy.items()},
            history=obs,
        )

    def covering_files(self, system_id: str, audit_at: str) -> list[tuple[str, str]]:
        """(file_id, captured_at) of snapshots that cover a system, newest first — used to prove absence by omission."""
        rows = self.db.execute(
            "SELECT DISTINCT c.file_id, f.captured_at FROM coverage c JOIN files f USING(file_id) "
            "WHERE c.system_id=? AND f.captured_at<=? ORDER BY f.captured_at DESC", (system_id, audit_at))
        return [(r[0], r[1]) for r in rows]

    # ---- activity ------------------------------------------------------------------------------

    def activity_for(self, resource_id: str, before: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM activity WHERE (' ' || mentioned || ' ') LIKE ? ORDER BY event_time",
                               (f"% {resource_id} %",))
        return [dict(r) for r in rows if before is None or r["event_time"] <= before]

    def activity_window(self, start: str, end: str, source_system: str | None = None) -> list[dict]:
        q = "SELECT * FROM activity WHERE event_time BETWEEN ? AND ?"
        args: list = [start, end]
        if source_system:
            q += " AND source_system=?"
            args.append(source_system)
        return [dict(r) for r in self.db.execute(q + " ORDER BY event_time", args)]


def window_before(moment: str, minutes: int) -> str:
    return (ts(moment) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
