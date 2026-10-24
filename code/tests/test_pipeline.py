import csv
import json
from pathlib import Path

import pytest

from agent.agent import Rec, Verdict, merge_verdict
from index.store import Index
from runner.baseline import run_case
from runner.intent import baseline_intent
from runner.investigate import investigate, load_cases

GOLD = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
        for r in csv.DictReader(open(Path(__file__).resolve().parents[1] / "evaluation" / "custom_cases" / "fixtures" / "expected_output.csv"))}
CASES = {c.case_id: c for c in load_cases("deletion_requests.csv")}
INDEX = Index()


@pytest.mark.parametrize("case_id", sorted(GOLD))
def test_baseline_matches_hand_written_gold(case_id):
    if case_id == "custom_04":
        pytest.xfail("the English-only regex cannot read a French request that refuses deletion; the agent handles it")
    assert run_case(INDEX, CASES[case_id]) == GOLD[case_id]


def test_english_intent_regex():
    assert baseline_intent("Please send me a copy of my data.") == "non_deletion"
    assert baseline_intent("Please delete my account.") == "deletion"


def test_lookalike_email_decoy_is_excluded():
    resources = {c.resource_id for c in investigate(INDEX, CASES["custom_06"]).candidates}
    assert "search_9405" in resources and "search_9407" not in resources


def test_merge_guards_keep_omitted_candidates_ignore_inventions_and_repair_citations():
    d = investigate(INDEX, CASES["custom_06"])
    verdict = Verdict(authorizes_deletion=True, resources=[
        Rec(resource_id="search_0000", category="SEARCH_INDEX_ENTRY", observed_state="PRESENT", evidence_id="evidence_07", rationale="invented"),
        Rec(resource_id="account_9105", category="USER_PROFILE", observed_state="ABSENT", evidence_id="evidence_999", rationale="unknown file"),
    ])
    recs, overrides = merge_verdict(INDEX, d, verdict)
    by_id = {r.resource_id: r for r in recs}
    assert "search_0000" not in by_id                                              # never invented
    assert by_id["search_9405"].observed_state == "PRESENT"                        # omitted by the agent, kept from the dossier
    assert by_id["account_9105"].evidence_id != "evidence_999"                     # unknown citation replaced
    kinds = {o["kind"] for o in overrides}
    assert {"agent_omitted", "agent_invented_ignored"} <= kinds
