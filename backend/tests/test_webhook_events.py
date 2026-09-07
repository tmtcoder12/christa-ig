from __future__ import annotations

from christa_ig.webhook_events import expand_meta_events, retry_delay


def test_expands_every_dm_and_comment_event():
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "business-1",
                "time": 123,
                "messaging": [
                    {"message": {"mid": "m1", "text": "hello"}},
                    {"message": {"mid": "m2", "text": "again"}},
                ],
                "changes": [
                    {"field": "comments", "value": {"id": "c1", "text": "promo"}},
                ],
            }
        ],
    }
    jobs = expand_meta_events(payload)
    assert [job["external_event_id"] for job in jobs] == ["dm:m1", "dm:m2", "comment:c1"]
    assert all(job["account_external_id"] == "business-1" for job in jobs)
    assert len(jobs[0]["payload"]["entry"][0]["messaging"]) == 1


def test_generated_event_ids_are_deterministic():
    payload = {"entry": [{"id": "a", "messaging": [{"message": {"text": "hello"}}]}]}
    first = expand_meta_events(payload)
    second = expand_meta_events(payload)
    assert first[0]["external_event_id"] == second[0]["external_event_id"]


def test_unsupported_payload_is_ignored():
    assert expand_meta_events(None) == []
    assert expand_meta_events({"entry": "invalid"}) == []


def test_retry_delay_is_bounded():
    assert retry_delay(1) == 2
    assert retry_delay(3) == 30
    assert retry_delay(99) == 300
