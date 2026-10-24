from index.build import parse_human_date
from index.logparse import parse_line
from index.store import Index


def test_parse_human_date_handles_time_and_date_only():
    assert parse_human_date("25 Jun 2026", "06", "42") == "2026-06-25T06:42:00Z"
    assert parse_human_date("26 Jul 2026", None, None) == "2026-07-26T00:00:00Z"


def test_logparse_key_value_line():
    rec = parse_line('2026-09-15T08:51:45Z level=DEBUG service=search_service trace_id=tr-1 event=index_query_started match_key="a@b.org"')
    assert rec["service"] == "search_service" and rec["event"] == "index_query_started" and rec["attrs"]["match_key"] == "a@b.org"


def test_identity_resolves_current_and_historical_email_case_insensitively():
    idx = Index()
    assert idx.resolve_identity("TOMAS.NOVAK@example.org").user_id == "user_905"          # historical, upper case
    assert idx.resolve_identity("tomas.novak.work@example.org").user_id == "user_905"     # current
    assert idx.resolve_identity("tomas.novak1@example.org").user_id == "user_907"         # look-alike is a different person
    assert idx.resolve_identity("nobody@example.org") is None


def test_state_ignores_evidence_captured_after_audit_at():
    idx = Index()
    before = idx.state_at("search_9403", "2026-10-03T12:00:00Z")
    after = idx.state_at("search_9403", "2026-10-06T00:00:00Z")
    assert before.presence == "ABSENT" and before.evidence_id == "evidence_04"
    assert after.presence == "PRESENT" and after.evidence_id == "evidence_05"
