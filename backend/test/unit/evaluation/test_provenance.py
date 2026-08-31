from yuxi.evaluation.provenance import TraceEvent, checksum_for, redact_value, validate_trace_events


def test_redact_value_removes_credentials_and_truncates_text():
    redacted = redact_value({"api_key": "secret", "nested": {"password": "hidden"}, "text": "x" * 2100})

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["text"].endswith("...[truncated]")


def test_trace_event_checksum_is_stable_after_redaction():
    first = TraceEvent(
        event_id="event-1",
        rollout_id="rollout-1",
        sequence=1,
        component="tool",
        operation="fixture.echo",
        input_summary={"token": "one"},
    ).to_dict()
    second = TraceEvent(
        event_id="event-1",
        rollout_id="rollout-1",
        sequence=1,
        component="tool",
        operation="fixture.echo",
        input_summary={"token": "two"},
    ).to_dict()

    assert first["input_summary"]["token"] == "[REDACTED]"
    assert first["checksum"] == second["checksum"]
    assert checksum_for({"value": 1}) == checksum_for({"value": 1})


def test_validate_trace_events_checks_parent_order_and_duplicate_sequence():
    valid = [
        {"event_id": "root", "rollout_id": "r1", "sequence": 1, "parent_id": None},
        {"event_id": "child", "rollout_id": "r1", "sequence": 2, "parent_id": "root"},
    ]
    assert validate_trace_events(valid, rollout_id="r1") == []

    invalid = [
        {"event_id": "root", "rollout_id": "r1", "sequence": 2, "parent_id": "missing"},
        {"event_id": "root", "rollout_id": "r1", "sequence": 2, "parent_id": None},
    ]
    errors = validate_trace_events(invalid, rollout_id="r1")
    assert "trace_event_id_duplicate" in errors
    assert "trace_sequence_invalid" in errors
    assert "trace_parent_missing" in errors
