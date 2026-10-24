"""Parse the six log formats in dataset/evidence/logs into (timestamp, service, trace_id, event, attrs) records."""

import json
import re

KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+([\w.]+):\s*(.*)$")
SYSLOG_RE = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")


def _kv(text: str) -> dict:
    out = {}
    for key, value in KV_RE.findall(text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        out[key] = None if value in ("null", "None", "") else value
    return out


def parse_line(line: str) -> dict | None:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = m.groups()
    attrs: dict
    if rest.startswith("{"):
        try:
            attrs = {k: (None if v in (None, "null") else str(v)) for k, v in json.loads(rest).items()}
        except json.JSONDecodeError:
            return None
        service = attrs.pop("service", None) or attrs.pop("logger", None)
        attrs.pop("logger", None)
    elif (b := BRACKET_RE.match(rest)):
        _level, service, trace, event, tail = b.groups()
        attrs = _kv(tail)
        attrs["trace_id"], attrs["event"] = trace, event
    elif rest.startswith("| "):
        parts = [p.strip() for p in rest[2:].split(" | ", 4)]
        if len(parts) < 5:
            return None
        _level, service, trace, event, tail = parts
        attrs = _kv(tail)
        attrs["trace_id"], attrs["event"] = trace, event
    elif (s := SYSLOG_RE.match(rest)):
        _node, service, trace, tail = s.groups()
        attrs = _kv(tail)
        attrs["trace_id"] = trace
    else:
        attrs = _kv(rest)
        if "message" in attrs and attrs["message"]:
            inner = attrs.pop("message")
            attrs.update(_kv(inner))
        service = attrs.pop("service", None) or attrs.pop("function", None)
        if "requestId" in attrs:
            attrs["trace_id"] = attrs.pop("requestId")
    attrs.pop("level", None)
    attrs.pop("severity", None)
    return {"ts": ts, "service": service, "trace_id": attrs.pop("trace_id", None), "event": attrs.pop("event", None), "attrs": attrs}


def parse_file(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [rec for line in fh if (rec := parse_line(line))]
