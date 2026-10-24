"""Build the evidence index: every sighting of every resource across all evidence files, normalized.

Output: build/index.sqlite with tables files, coverage, obs, obs_keys, activity, identities, identity_history, rules.
Run: python code/index/build.py
"""

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from collections import defaultdict
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index.logparse import parse_file  # noqa: E402
from index.vocab import (  # noqa: E402
    COLLECTION_TYPES,
    LEDGER_RESULTS,
    system_for,
    type_for,
)

REPO = Path(__file__).resolve().parents[2]
from paths import DATASET, INDEX_PATH as OUT, VISION_CACHE  # noqa: E402

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
RESOURCE_RE = re.compile(r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|legal_hold|marketing|cache|email_delivery|object)_\d+\b")

NAME_FIELDS = {"full_name", "name", "display_name", "requester_name"}
PHONE_FIELDS = {"phone", "requester_phone", "owner_phone"}
EMAIL_FIELDS = {"email", "requester_email", "owner_email", "customer_email", "uploaded_by"}
HASH_FIELDS = {"email_hash"}
REF_FIELDS = {"customer_reference", "subject_reference", "user_id", "subject_selector"}
DIRECT_ID_FIELDS = NAME_FIELDS | PHONE_FIELDS | EMAIL_FIELDS | HASH_FIELDS

SCHEMA = """
CREATE TABLE files (file_id TEXT PRIMARY KEY, path TEXT, kind TEXT, origin TEXT, captured_at TEXT);
CREATE TABLE coverage (file_id TEXT, system_id TEXT, copy TEXT, captured_at TEXT);
CREATE TABLE obs (
  obs_id INTEGER PRIMARY KEY, file_id TEXT, captured_at TEXT, observed_at TEXT,
  system_id TEXT, resource_id TEXT, record_type TEXT, copy TEXT,
  presence TEXT, pii TEXT, flow TEXT, restore TEXT, retention_until TEXT,
  source TEXT, excerpt TEXT
);
CREATE TABLE obs_keys (obs_id INTEGER, key_type TEXT, key_value TEXT);
CREATE TABLE activity (activity_id TEXT PRIMARY KEY, event_time TEXT, event_type TEXT, source_system TEXT,
  target_system TEXT, resource_id TEXT, reported_result TEXT, details TEXT, mentioned TEXT);
CREATE TABLE identities (user_id TEXT PRIMARY KEY, name TEXT, email TEXT, phone TEXT, email_hash TEXT, updated_at TEXT);
CREATE TABLE identity_history (audit_id TEXT PRIMARY KEY, user_id TEXT, event_time TEXT, field_name TEXT, old_value TEXT, new_value TEXT);
CREATE TABLE rules (rule_id TEXT, data_category TEXT, condition TEXT, required_action TEXT);
CREATE INDEX obs_res ON obs(resource_id);
CREATE INDEX keys_val ON obs_keys(key_value);
CREATE INDEX keys_obs ON obs_keys(obs_id);
"""


LINK_DELAY_S = 420   # the catalog links a resource 7 minutes after the primary evidence file was captured
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def norm_phone_text(text: str) -> str:
    return re.sub(r"\D", "", text)


def ts_(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_human_date(day_month_year: str, hh: str | None, mm: str | None) -> str:
    d, mon, y = day_month_year.split()
    return f"{int(y):04d}-{MONTHS[mon]:02d}-{int(d):02d}T{int(hh or 0):02d}:{int(mm or 0):02d}:00Z"


def keys_from(record: dict, extra_text: str = "") -> set[tuple[str, str]]:
    """Identifier keys found in a record: field-typed values plus emails/user refs/hashes in free text."""
    keys: set[tuple[str, str]] = set()

    def walk(obj, field=None):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, field)
        elif isinstance(obj, str) and obj:
            if field in PHONE_FIELDS:
                keys.add(("phone", norm_phone(obj)))
            elif field in NAME_FIELDS:
                keys.add(("name", obj.lower()))
            for e in EMAIL_RE.findall(obj):
                keys.add(("email", e.lower()))
            for u in USER_RE.findall(obj):
                keys.add(("user_ref", u))
            for h in HASH_RE.findall(obj.lower()):
                keys.add(("hash", h))

    walk(record)
    if extra_text:
        walk(extra_text)
    return keys


def pii_state(record: dict) -> str | None:
    """EXPOSED if any direct identifier is populated, CLEAN if identifier fields exist but are all empty."""
    present, seen = False, False
    for field in DIRECT_ID_FIELDS:
        if field in record:
            seen = True
            if record[field] not in (None, "", "[REDACTED]"):
                present = True
    text = record.get("message_text") or record.get("message_excerpt")
    if text is not None:
        seen = True
        if EMAIL_RE.search(text):
            present = True
    doc = record.get("document")
    if isinstance(doc, dict):
        sub = pii_state(doc)
        if sub == "EXPOSED":
            return "EXPOSED"
        seen = seen or sub is not None
    if present:
        return "EXPOSED"
    return "CLEAN" if seen else None


def truthy(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "allow", "allowed")


class Builder:
    def __init__(self) -> None:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        if OUT.exists():
            OUT.unlink()
        self.db = sqlite3.connect(OUT)
        self.db.executescript(SCHEMA)
        self.next_id = 1
        self.order_owners: list[tuple[str, str, str]] = []
        self.order_mentions: list = []

    def add_obs(self, file_id, captured_at, resource_id, *, observed_at=None, record_type=None, copy="",
                presence="PRESENT", pii=None, flow=None, restore=None, retention_until=None,
                source="", record=None, keys=None, excerpt="") -> None:
        if not resource_id or not RESOURCE_RE.fullmatch(resource_id):
            return
        record = record or {}
        if record_type is None:
            record_type = type_for(resource_id)
        if pii is None and presence == "PRESENT":
            pii = pii_state(record)
        obs_id = self.next_id
        self.next_id += 1
        self.db.execute(
            "INSERT INTO obs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (obs_id, file_id, captured_at, observed_at or captured_at, system_for(resource_id), resource_id,
             record_type, copy, presence, pii, flow, restore, retention_until, source,
             (excerpt or json.dumps(record, ensure_ascii=False))[:600]),
        )
        all_keys = set(keys or set()) | keys_from(record)
        all_keys.discard(("user_ref", ""))
        self.db.executemany("INSERT INTO obs_keys VALUES (?,?,?)", [(obs_id, t, v) for t, v in all_keys])

    def add_structured(self, file_id, captured_at, record: dict, *, collection=None, copy="", observed_at=None, source="") -> None:
        """A typed business record (SQL row, JSON record) as found in a data store snapshot."""
        rid = record.get("resource_id")
        rtype = COLLECTION_TYPES.get(collection) if collection else None
        flow = restore = None
        if "enabled" in record:
            flow = "ACTIVE" if truthy(record["enabled"]) else "DISABLED"
        if "restore_allowed" in record:
            restore = "ALLOWED" if truthy(record["restore_allowed"]) else "BLOCKED"
        retention = record.get("retention_until") or record.get("retained_until")
        self.add_obs(file_id, captured_at, rid, observed_at=observed_at, record_type=rtype, copy=copy,
                     flow=flow, restore=restore, retention_until=retention, source=source, record=record)

    def add_ledger(self, file_id, captured_at, row: dict, source: str) -> None:
        """A lookup-result row: {system, resource_id, record_type, result, subject_reference|lookup_value}."""
        result = (row.get("result") or "").upper()
        mapped = LEDGER_RESULTS.get(result, {})
        subject = row.get("subject_reference") or row.get("lookup_value") or ""
        keys = keys_from({"subject": subject})
        rtype = row.get("record_type")
        flow = restore = None
        if rtype == "DATA_FLOW" and result in ("ACTIVE", "DISABLED"):
            flow = result
        if rtype == "BACKUP_ARCHIVE" and result in ("ACTIVE", "BLOCKED"):
            restore = "ALLOWED" if result == "ACTIVE" else "BLOCKED"
        self.add_obs(file_id, captured_at, row.get("resource_id"), observed_at=row.get("recorded_at"),
                     record_type=rtype, copy="ledger", presence=mapped.get("presence", "PRESENT"),
                     pii=mapped.get("pii"), flow=flow, restore=restore, source=source, keys=keys,
                     excerpt=json.dumps(row, ensure_ascii=False))

    # ---- loaders -------------------------------------------------------------------------------

    def load_sql(self, file_id, captured_at, path: Path) -> None:
        mem = sqlite3.connect(":memory:")
        mem.executescript(path.read_text(encoding="utf-8"))
        tables = [r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "snapshot_scope" in tables:
            for system, cap in mem.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
                self.db.execute("INSERT INTO coverage VALUES (?,?,?,?)", (file_id, system, "inventory", cap))
        for table in tables:
            if table == "snapshot_scope":
                continue
            cols = [c[1] for c in mem.execute(f'PRAGMA table_info("{table}")')]
            copy = "inventory"
            for suffix in ("primary", "read_replica", "failover_replica", "reporting_replica", "replica_ap_south", "replica_eu_west"):
                if table.endswith(suffix):
                    copy = suffix
            systems = set()
            for row in mem.execute(f'SELECT * FROM "{table}"'):
                rec = dict(zip(cols, row))
                if "record_json" in rec:
                    body = json.loads(rec["record_json"])
                    systems.add(rec["origin_system"])
                    self.add_structured(file_id, captured_at, body, collection=rec["collection_name"], copy=copy,
                                        observed_at=rec.get("updated_at"), source=f"sql:{table}")
                else:
                    if "source_resource_id" in rec:
                        rec["document"] = {"display_name": rec.pop("display_name", None), "email": rec.pop("email", None)}
                    systems.add(system_for(rec.get("resource_id", "")))
                    self.add_structured(file_id, captured_at, rec, collection=table, copy=copy,
                                        observed_at=rec.get("updated_at") or rec.get("indexed_at"), source=f"sql:{table}")
            if copy != "inventory":
                for system in systems:
                    self.db.execute("INSERT INTO coverage VALUES (?,?,?,?)", (file_id, system, copy, captured_at))

    def load_json(self, file_id, captured_at, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "snapshots" in data:
            for snap in data["snapshots"]:
                cap = snap.get("captured_at")
                coll = snap.get("collection")
                if coll == "operational_record_history":
                    for row in snap["records"]:
                        self.add_ledger(file_id, captured_at, row, f"bundle:{snap['data_store']}")
                    continue
                self.db.execute("INSERT INTO coverage VALUES (?,?,?,?)", (file_id, snap["data_store"], "bundle", cap))
                for rec in snap["records"]:
                    self.add_structured(file_id, captured_at, rec, collection=coll, copy="bundle", observed_at=cap,
                                        source=f"bundle:{snap['data_store']}")
        elif "service_exports" in data:
            self.load_service_export(file_id, captured_at, data)
        elif "records" in data and data["records"] and "record_type" in data["records"][0]:
            for row in data["records"]:
                self.add_ledger(file_id, captured_at, row, f"ledger:{data.get('origin', '')}")
        elif "records" in data:
            coll = data.get("collection")
            self.db.execute("INSERT INTO coverage VALUES (?,?,?,?)",
                            (file_id, {"customer_analytics_reports": "analytics_platform", "customer_export_jobs": "export_service"}.get(coll, ""), "store", data.get("captured_at")))
            for rec in data["records"]:
                self.add_structured(file_id, captured_at, rec, collection=coll, copy="store",
                                    observed_at=data.get("captured_at"), source=f"store:{coll}")

    def load_service_export(self, file_id, captured_at, data: dict) -> None:
        for system, groups in data["service_exports"].items():
            self.db.execute("INSERT INTO coverage VALUES (?,?,?,?)", (file_id, system, "service_export", captured_at))
            for group, items in groups.items():
                for item in items:
                    seen = item.get("observed_at")
                    src = f"service_export:{system}.{group}"
                    if group == "profile_lookups":
                        if item.get("status_code") == 404 or item.get("profile") is None:
                            self.add_obs(file_id, captured_at, item["resource_id"], observed_at=seen, copy="service_export",
                                         presence="ABSENT", source=src, record=item)
                        else:
                            self.add_obs(file_id, captured_at, item["resource_id"], observed_at=seen, copy="service_export",
                                         source=src, record=item["profile"], excerpt=json.dumps(item, ensure_ascii=False))
                    elif group == "index_queries":
                        docs = (item.get("hits") or {}).get("documents") or []
                        if not docs:
                            self.add_obs(file_id, captured_at, item.get("resource_id"), observed_at=seen, copy="service_export",
                                         presence="ABSENT", source=src, record=item,
                                         keys=keys_from({"q": item.get("query", "")}))
                        for doc in docs:
                            self.add_obs(file_id, captured_at, doc.get("resource_id"), observed_at=seen, copy="service_export",
                                         source=src, record=doc, keys=keys_from({"q": item.get("query", "")}))
                    else:
                        coll = {"profiles": "customer_analytics_reports", "restore_entries": "backup_catalog",
                                "exports": "customer_export_jobs", "connectors": "connector_configurations",
                                "audit_records": "security_audit_events", "tickets": "support_conversations"}.get(group)
                        self.add_structured(file_id, captured_at, item, collection=coll, copy="service_export",
                                            observed_at=seen, source=src)

    def load_txt(self, file_id, captured_at, path: Path) -> None:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.lstrip("- ").split("|")]
            if len(parts) == 6 and RESOURCE_RE.fullmatch(parts[2]):
                when, system, rid, rtype, result, subject = parts
                self.add_ledger(file_id, captured_at, {"recorded_at": when, "system": system, "resource_id": rid,
                                                       "record_type": rtype, "result": result, "subject_reference": subject}, "handover")

    def load_pdf(self, file_id, captured_at, path: Path) -> None:
        text = "\n".join(page.get_text() for page in pymupdf.open(path))
        self.load_document_text(file_id, captured_at, text, "pdf")

    def load_document_text(self, file_id, captured_at, text: str, source: str) -> None:
        """Free-text business documents: every resource mentioned is observed PRESENT with the document's identifiers."""
        keys = keys_from({"text": text})
        orders = set(re.findall(r"ORD-\d+", text))
        for rid in sorted(set(RESOURCE_RE.findall(text))):
            self.add_obs(file_id, captured_at, rid, copy=source, source=source, keys=keys, pii="EXPOSED",
                         record_type="SUPPORT_ATTACHMENT" if rid.startswith("support_") else None,
                         excerpt=text[:600])
        # Legal register rows name an order + user; legal_hold records carry the same order_reference.
        for line in text.splitlines():
            m = re.match(r"(ORD-\d+)\s*\|\s*(user_\d+)\s*\|", line.strip())
            if m:
                self.order_owners.append((m.group(1), m.group(2), file_id))
        if orders and not RESOURCE_RE.search(text):
            self.order_mentions.append((file_id, captured_at, orders, keys))

    def link_orders(self) -> None:
        """Give legal_hold observations the user_ref of the register row naming their order."""
        for order, user_ref, _fid in self.order_owners:
            ids = [r[0] for r in self.db.execute("SELECT obs_id FROM obs WHERE excerpt LIKE ?", (f"%{order}%",))]
            self.db.executemany("INSERT INTO obs_keys VALUES (?,?,?)", [(i, "user_ref", user_ref) for i in ids])

    def load_log(self, file_id, captured_at, path: Path) -> None:
        recs = parse_file(str(path))
        trace_keys: dict[str, set] = defaultdict(set)
        for r in recs:
            a = r["attrs"]
            for k in ("lookup_key", "match_key", "subject_key", "subject_ref", "scan_key", "owner_key", "correlation_key", "matched_value"):
                if a.get(k) and r["trace_id"]:
                    trace_keys[r["trace_id"]] |= keys_from({"k": a[k]})
        src = f"log:{path.name}"
        for r in recs:
            a, ev, rid = r["attrs"], r["event"], r["attrs"].get("resource_id")
            if not rid:
                continue
            keys = set(trace_keys.get(r["trace_id"], set())) if r["trace_id"] else set()
            for k in ("lookup_key", "correlation_key", "matched_value", "subject_reference", "subject_ref"):
                if a.get(k):
                    keys |= keys_from({"k": a[k]})
            line = f"{r['ts']} {r['service']} {ev} " + " ".join(f"{k}={v}" for k, v in a.items() if k not in ("instance", "pod", "region"))
            kw = dict(observed_at=r["ts"], copy="log", source=src, keys=keys, excerpt=line)
            fields = {k: a.get(k) for k in DIRECT_ID_FIELDS | {"customer_reference"} if k in a}
            if ev == "record_lookup":
                self.add_ledger(file_id, captured_at, {"recorded_at": r["ts"], **a}, src)
            elif ev == "profile_lookup_completed":
                absent = a.get("rows") == "0" or a.get("http_status") == "404"
                self.add_obs(file_id, captured_at, rid, presence="ABSENT" if absent else "PRESENT",
                             pii=None if absent else ("EXPOSED" if any(fields.get(f) for f in ("email", "phone")) else None), **kw)
            elif ev == "index_query_completed":
                hit = a.get("result") == "hit" or (a.get("total_hits") not in (None, "0"))
                self.add_obs(file_id, captured_at, rid, presence="PRESENT" if hit else "ABSENT",
                             pii="EXPOSED" if hit else None, **kw)
            elif ev in ("analytics_profile_read_completed", "analytics_profile_read", "analytics_identity_scan"):
                exposed = any(a.get(f) for f in ("email", "phone", "email_hash", "customer_reference")) or a.get("direct_identifiers") not in (None, "0")
                self.add_obs(file_id, captured_at, rid, pii="EXPOSED" if exposed else "CLEAN", **kw)
            elif ev in ("audit_record_read_completed",):
                self.add_obs(file_id, captured_at, rid, presence="PRESENT" if a.get("rows") != "0" else "ABSENT",
                             retention_until=a.get("retention_until"), **kw)
            elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
                self.add_obs(file_id, captured_at, rid, flow="ACTIVE" if truthy(a.get("enabled")) else "DISABLED", **kw)
            elif ev == "contact_lookup_completed":
                # marketing opt-in contact profile held under an analytics resource id
                self.add_obs(file_id, captured_at, rid, presence="PRESENT" if a.get("rows") != "0" else "ABSENT",
                             pii="EXPOSED", record_type="MARKETING_PROFILE", **kw)
            elif ev == "contact_query_completed":
                # legacy contact index entry held under a support resource id
                self.add_obs(file_id, captured_at, rid, presence="PRESENT" if a.get("rows") != "0" else "ABSENT",
                             pii="EXPOSED", record_type="SECONDARY_IDENTIFIER", **kw)
            elif ev == "deletion_marker_check":
                self.add_obs(file_id, captured_at, rid, presence="ABSENT" if truthy(a.get("marker_found")) else "PRESENT",
                             pii="EXPOSED", record_type="SECONDARY_IDENTIFIER", **kw)
            elif ev in ("export_download_check_completed", "export_metadata_read_completed"):
                self.add_obs(file_id, captured_at, rid, presence="PRESENT" if truthy(a.get("available")) else "ABSENT", **kw)
            elif ev == "message_pii_scan":
                self.add_obs(file_id, captured_at, rid, pii="EXPOSED", record_type="SUPPORT_MESSAGE", **kw)
            elif ev == "attachment_pii_scan":
                self.add_obs(file_id, captured_at, rid, pii="EXPOSED", record_type="SUPPORT_ATTACHMENT", **kw)
            elif ev == "message_read":
                txt = a.get("message_text") or ""
                self.add_obs(file_id, captured_at, rid, pii="CLEAN" if "REDACTED" in txt or not EMAIL_RE.search(txt) else "EXPOSED", **kw)
            elif ev == "support_ticket_read_completed":
                exposed = any(a.get(f) for f in ("requester_name", "requester_email", "requester_phone"))
                self.add_obs(file_id, captured_at, rid, pii="EXPOSED" if exposed else "CLEAN", **kw)
            elif ev == "restore_policy_read_completed":
                self.add_obs(file_id, captured_at, rid, restore="ALLOWED" if truthy(a.get("restore_allowed")) else "BLOCKED",
                             presence="PRESENT" if truthy(a.get("archive_present")) is not False else "ABSENT", **kw)
            elif ev == "restore_candidate_evaluated":
                allowed = truthy(a.get("eligible")) and not truthy(a.get("deletion_block"))
                self.add_obs(file_id, captured_at, rid, restore="ALLOWED" if allowed else "BLOCKED", **kw)
            elif ev == "restore_authorization_completed":
                self.add_obs(file_id, captured_at, rid, restore="BLOCKED" if a.get("decision") == "deny" else "ALLOWED", **kw)


    # ---- screenshots and PDFs tied to a subject by a catalog link event -------------------------

    def load_links(self, docs: dict[str, tuple[str, str, str]]) -> set[str]:
        """Observe the resource each LINK_RESOURCE_IDENTITY event names, reading its state from the image/PDF it was linked from.

        The catalog writes the link event LINK_DELAY after the primary file is captured, so the pair is matched on time and
        confirmed by the resource id or alias appearing in the file. Other resources shown in the same screen are decoys
        and are not observed. Returns the file ids consumed (primary and supporting).
        """
        consumed: set[str] = set()
        events = self.db.execute("SELECT * FROM activity WHERE event_type='LINK_RESOURCE_IDENTITY'").fetchall()
        for ev in events:
            act_id, when, rid, details = ev[0], ev[1], ev[5], ev[7]
            emails = [e.lower() for e in EMAIL_RE.findall(details)]
            users = USER_RE.findall(details)
            alias = (re.search(r"\(([^)]+)\)", details) or [None, rid])[1]
            tokens = {rid, alias}
            mentions = {fid: d for fid, d in docs.items() if any(t in d[1] for t in tokens)}
            primary = [fid for fid, d in mentions.items() if abs((ts_(when) - ts_(d[0])).total_seconds() - LINK_DELAY_S) <= 1]
            if not primary:
                continue
            fid = primary[0]
            cap, text, _ = docs[fid]
            consumed |= set(mentions)
            self.add_link_obs(fid, cap, rid, text, emails, users, act_id, [d[1] for d in mentions.values()])
        return consumed

    def add_link_obs(self, fid, cap, rid, text, emails, users, act_id, texts) -> None:
        rtype = type_for(rid)
        subject = self.db.execute("SELECT name, phone FROM identities WHERE user_id=?", (users[0] if users else "",)).fetchone()
        low = text.lower()
        needles = set(emails) | ({subject[0].lower(), norm_phone(subject[1])} if subject else set())
        exposed = any(n in low or n in norm_phone_text(text) for n in needles if n)
        flow = restore = None
        if rtype == "DATA_FLOW":
            status = re.search(r"Status:\s*(\w+)", text)
            flow = "DISABLED" if re.search(r"disabled|paused|stopped|inactive", status.group(1) if status else "", re.I) else "ACTIVE"
        if rtype == "BACKUP_ARCHIVE":
            restore = "BLOCKED" if re.search(r"restore\s+(blocked|disabled|denied)|deletion block", text, re.I) else "ALLOWED"
        retention = None
        for t in texts:
            m = re.search(r"(?:Expires|Retention end|Retain until|retention until)\D{0,3}\s*(\d{1,2} [A-Z][a-z]{2} \d{4})(?:\s+(\d{2})\D{1,3}(\d{2}))?", t)
            if m:
                retention = parse_human_date(m.group(1), m.group(2), m.group(3))
        self.add_obs(fid, cap, rid, record_type=rtype, copy="link", presence="PRESENT", pii="EXPOSED" if exposed else "CLEAN",
                     flow=flow, restore=restore, retention_until=retention, source=f"link:{act_id}",
                     keys={("email", e) for e in emails} | {("user_ref", u) for u in users}, excerpt=text[:600])

    # ---- tables from CSVs ----------------------------------------------------------------------

    def load_csvs(self) -> None:
        def rows(name):
            with open(DATASET / name, encoding="utf-8") as fh:
                return list(csv.DictReader(fh))

        self.db.executemany("INSERT INTO identities VALUES (?,?,?,?,?,?)",
                            [(r["user_id"], r["name"], r["email"].lower(), r["phone"], r["email_hash"], r["updated_at"]) for r in rows("user_identities.csv")])
        self.db.executemany("INSERT INTO identity_history VALUES (?,?,?,?,?,?)",
                            [(r["audit_id"], r["user_id"], r["event_time"], r["field_name"], r["old_value"], r["new_value"]) for r in rows("user_audit_log.csv")])
        self.db.executemany("INSERT INTO rules VALUES (?,?,?,?)",
                            [(r["rule_id"], r["data_category"], r["condition"], r["required_action"]) for r in rows("deletion_rules.csv")])
        acts = []
        for r in rows("system_activity.csv"):
            mentioned = sorted(set(RESOURCE_RE.findall(r["details"])) | {r["resource_id"]})
            acts.append((r["activity_id"], r["event_time"], r["event_type"], r["source_system"], r["target_system"],
                         r["resource_id"], r["reported_result"], r["details"], " ".join(mentioned)))
        self.db.executemany("INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?)", acts)

    def run(self) -> None:
        self.load_csvs()
        with open(DATASET / "evidence_files.csv", encoding="utf-8") as fh:
            manifest = list(csv.DictReader(fh))
        vision_dir = VISION_CACHE
        docs: dict[str, tuple[str, str, str]] = {}   # file_id -> (captured_at, text, source) for images and PDFs
        for m in manifest:
            path = DATASET / m["file_path"]
            fid, cap = m["file_id"], m["captured_at"]
            kind = path.suffix.lstrip(".")
            self.db.execute("INSERT INTO files VALUES (?,?,?,?,?)", (fid, m["file_path"], kind, m["origin"], cap))
            if kind == "sql":
                self.load_sql(fid, cap, path)
            elif kind == "json":
                self.load_json(fid, cap, path)
            elif kind == "txt":
                self.load_txt(fid, cap, path)
            elif kind == "log":
                self.load_log(fid, cap, path)
            elif kind == "pdf":
                docs[fid] = (cap, "\n".join(page.get_text() for page in pymupdf.open(path)), "pdf")
            elif kind == "png":
                cached = vision_dir / f"{path.stem}.json"
                if cached.exists():
                    docs[fid] = (cap, json.loads(cached.read_text())["text"], "image")
        consumed = self.load_links(docs)
        for fid, (cap, text, source) in docs.items():
            if fid not in consumed and source == "pdf":
                self.load_document_text(fid, cap, text, source)
        self.link_orders()
        self.db.commit()
        n_obs = self.db.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
        n_res = self.db.execute("SELECT COUNT(DISTINCT resource_id) FROM obs").fetchone()[0]
        print(f"index built: {n_obs} observations of {n_res} resources -> {OUT}")


if __name__ == "__main__":
    Builder().run()
