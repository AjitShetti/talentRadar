"""
tests/test_copilot.py
~~~~~~~~~~~~~~~~~~~~~
Unit tests for the Career Copilot briefing and chat turn.

The invariant that matters here is that the briefing is *deterministic and
honest*: every card is derived from rows the user actually has, the LLM never
decides what appears, and a card the user dismissed stays gone. The second
invariant is that a failure anywhere in the stack — no LLM key, no Chroma, a
broken sub-query — degrades to a usable page instead of a 500.

Everything runs without a database or a network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services import copilot
from services.copilot import (
    SAVED_STALE_AFTER_DAYS,
    STALE_AFTER_DAYS,
    _card,
    _days_since,
    _headline,
    _interview_card,
    _plain,
    build_briefing,
    chat,
)
from services.outreach import briefing as outreach_briefing

# ─────────────────────────────────────────────────────────────────────────────
# Card primitives
# ─────────────────────────────────────────────────────────────────────────────

def test_card_shape_is_uniform() -> None:
    card = _card(card_id="x", kind="k", title="t", detail="d")
    assert set(card) == {"id", "kind", "title", "detail", "tone", "actions", "meta", "dismissible"}
    assert card["actions"] == [] and card["meta"] == {}
    assert card["dismissible"] is True


def test_days_since_handles_naive_timestamps() -> None:
    """Postgres can hand back naive datetimes; the card must not crash on one."""
    naive = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=3)
    assert _days_since(naive) == 3
    assert _days_since(None) is None


def test_days_since_never_goes_negative() -> None:
    assert _days_since(datetime.now(tz=UTC) + timedelta(days=2)) == 0


def test_staleness_thresholds_are_ordered() -> None:
    """A saved job decays faster than one already in the employer's hands."""
    assert SAVED_STALE_AFTER_DAYS < STALE_AFTER_DAYS


# ─────────────────────────────────────────────────────────────────────────────
# Headline
# ─────────────────────────────────────────────────────────────────────────────

def test_headline_leads_with_urgency() -> None:
    cards = [_card(card_id="a", kind="stale_application", title="t", detail="d", tone="warning")]
    assert "chasing" in _headline(cards, {"total_applications": 4})


def test_headline_is_calm_when_nothing_is_stale() -> None:
    cards = [_card(card_id="p", kind="priority", title="t", detail="d", tone="primary")]
    assert "on top of things" in _headline(cards, {"total_applications": 2})


def _followup_card(count: int = 1) -> dict[str, Any]:
    return _card(card_id="outreach_followup", kind="outreach_followup", title="Follow up",
                 detail="d", tone="primary", meta={"count": count, "thread_id": "t1"},
                 dismissible=False)


def test_headline_is_never_calm_while_a_follow_up_is_due() -> None:
    """A lone due follow-up is still something to do: the queue is not empty."""
    headline = _headline([_followup_card()], {"total_applications": 0})

    assert "on top of things" not in headline
    assert "1 follow-up" in headline


def test_headline_counts_every_follow_up_due() -> None:
    headline = _headline([_followup_card(3)], {"total_applications": 0})

    assert "3 follow-ups" in headline


def test_a_follow_up_does_not_hide_an_interview_today() -> None:
    interview = _card(card_id="scheduled_interview:1", kind="scheduled_interview", title="t",
                      detail="d", tone="warning", meta={"days_away": 0})

    headline = _headline([interview, _followup_card()], {"total_applications": 2})

    assert "interview" in headline


# ─────────────────────────────────────────────────────────────────────────────
# Briefing assembly
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stub_sources(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace every data source the briefing pulls from with a controllable stub."""
    state: dict = {
        "next_action": {
            "recommendation": "Apply to your saved jobs",
            "detail": "You have 6 saved job(s).",
            "action": "apply_saved",
            "href": "/applications",
            "context": {"total_applications": 8, "saved": 6, "applied": 1, "interviews": 0},
        },
        "application_cards": [],
        "interview_card": None,
        "dismissed": set(),
        "outreach": {"followup": None, "suggestion": None},
    }

    async def fake_next_action(*, user_id: str) -> dict:
        return state["next_action"]

    async def fake_app_cards(user_id: str) -> list:
        return state["application_cards"]

    async def fake_interview(user_id: str, applied: int) -> dict | None:
        return state["interview_card"]

    async def fake_dismissals(*, user_id: str) -> set:
        return state["dismissed"]

    async def fake_outreach(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        outreach = state["outreach"]
        if isinstance(outreach, Exception):
            raise outreach
        cards: dict[str, Any] = outreach
        return cards

    monkeypatch.setattr(copilot, "recommend_next_action", fake_next_action)
    monkeypatch.setattr(copilot, "_application_cards", fake_app_cards)
    monkeypatch.setattr(copilot, "_interview_card", fake_interview)
    monkeypatch.setattr(copilot, "active_dismissals", fake_dismissals)
    monkeypatch.setattr(outreach_briefing, "outreach_cards", fake_outreach)
    return state


async def test_priority_card_leads_and_cannot_be_dismissed(stub_sources: dict) -> None:
    briefing = await build_briefing(user_id="u1")
    lead = briefing["cards"][0]
    assert lead["kind"] == "priority"
    assert lead["title"] == "Apply to your saved jobs"
    assert lead["dismissible"] is False
    assert lead["actions"][0]["href"] == "/applications"


async def test_stats_mirror_the_users_real_funnel(stub_sources: dict) -> None:
    briefing = await build_briefing(user_id="u1")
    assert briefing["stats"]["total_applications"] == 8
    assert briefing["stats"]["saved"] == 6


async def test_dismissed_cards_are_filtered_and_counted(stub_sources: dict) -> None:
    stub_sources["application_cards"] = [
        _card(card_id="saved_backlog", kind="saved_backlog", title="6 saved", detail="d"),
        _card(card_id="stale_application:1", kind="stale_application", title="cold", detail="d"),
    ]
    stub_sources["dismissed"] = {"saved_backlog"}

    briefing = await build_briefing(user_id="u1")
    ids = [c["id"] for c in briefing["cards"]]
    assert "saved_backlog" not in ids
    assert "stale_application:1" in ids
    assert briefing["hidden_count"] == 1


async def test_dismissal_is_per_card_not_per_kind(stub_sources: dict) -> None:
    """Snoozing one cold application must not silence the others."""
    stub_sources["application_cards"] = [
        _card(card_id="stale_application:1", kind="stale_application", title="a", detail="d"),
        _card(card_id="stale_application:2", kind="stale_application", title="b", detail="d"),
    ]
    stub_sources["dismissed"] = {"stale_application:1"}

    briefing = await build_briefing(user_id="u1")
    assert [c["id"] for c in briefing["cards"] if c["kind"] == "stale_application"] == [
        "stale_application:2"
    ]


async def test_briefing_never_reports_what_the_overview_panels_own(stub_sources: dict) -> None:
    """
    The briefing and the overview panels share a page, so the briefing must not
    re-summarise interview scores or skill gaps — those are full panels there.
    """
    stub_sources["application_cards"] = [
        _card(card_id="saved_backlog", kind="saved_backlog", title="6 saved", detail="d"),
    ]
    briefing = await build_briefing(user_id="u1")
    assert {c["kind"] for c in briefing["cards"]} == {"priority", "saved_backlog"}


async def test_interview_card_stays_quiet_until_something_is_in_flight() -> None:
    """No applications means no practice nudge — and no query to make one."""
    assert await _interview_card("u1", 0) is None


async def test_briefing_places_outreach_cards(
    stub_sources: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dated commitment leads, the follow-up comes right after it, the suggestion is last."""
    async def fake_scheduled(user_id: str) -> list[dict[str, Any]]:
        return [_card(card_id="scheduled_interview:1", kind="scheduled_interview",
                      title="Interview tomorrow", detail="d", tone="warning")]

    monkeypatch.setattr(copilot, "_scheduled_interview_cards", fake_scheduled)
    stub_sources["application_cards"] = [
        _card(card_id="saved_backlog", kind="saved_backlog", title="6 saved", detail="d"),
    ]
    stub_sources["interview_card"] = _card(
        card_id="interview_cold_start", kind="interview_momentum", title="practise", detail="d"
    )
    stub_sources["outreach"] = {
        "followup": _card(card_id="outreach_followup", kind="outreach_followup",
                          title="Follow up", detail="d", tone="primary", dismissible=False),
        "suggestion": _card(card_id="outreach_suggestion:c1", kind="outreach_suggestion",
                            title="Reach out", detail="d"),
    }

    briefing = await build_briefing(user_id="u1")

    assert [c["kind"] for c in briefing["cards"]] == [
        "scheduled_interview",
        "outreach_followup",
        "priority",
        "saved_backlog",
        "interview_momentum",
        "outreach_suggestion",
    ]


async def test_a_dismissed_outreach_suggestion_is_filtered_like_any_card(
    stub_sources: dict[str, Any],
) -> None:
    stub_sources["outreach"] = {
        "followup": None,
        "suggestion": _card(card_id="outreach_suggestion:c1", kind="outreach_suggestion",
                            title="Reach out", detail="d"),
    }
    stub_sources["dismissed"] = {"outreach_suggestion:c1"}

    briefing = await build_briefing(user_id="u1")

    assert [c["kind"] for c in briefing["cards"]] == ["priority"]
    assert briefing["hidden_count"] == 1


async def test_a_failing_outreach_producer_cannot_blank_the_briefing(
    stub_sources: dict[str, Any],
) -> None:
    stub_sources["outreach"] = RuntimeError("the outreach tables are unreachable")

    briefing = await build_briefing(user_id="u1")

    assert [c["kind"] for c in briefing["cards"]] == ["priority"]


async def test_briefing_survives_a_missing_recommendation(stub_sources: dict) -> None:
    """A user with no profile at all still gets a page, not an exception."""
    stub_sources["next_action"] = {"recommendation": "", "context": {}}
    briefing = await build_briefing(user_id="u1")
    assert briefing["cards"] == []
    assert briefing["headline"]
    assert briefing["stats"]["total_applications"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────────────────────────────────────

class _StubResult:
    """Stands in for agents.state.RetrievalResult."""

    def __init__(self) -> None:
        self.job_id = "job-1"
        self.title = "Senior Backend Engineer"
        self.company = "Razorpay"
        self.location = "Bengaluru"
        self.is_remote = False
        self.skills = ["python", "postgres"]
        self.source_url = "https://example.com/job"
        self.score = 0.91


class _StubResponse:
    def __init__(self, intent: str, results: list | None = None, **kw: object) -> None:
        from agents.state import IntentType

        self.intent = IntentType(intent)
        self.results = results or []
        self.summary = kw.get("summary")
        self.metadata = kw.get("metadata", {})


def _stub_orchestrator(monkeypatch: pytest.MonkeyPatch, response: _StubResponse) -> None:
    import agents.orchestrator as orchestrator_module

    class _Stub:
        async def process_query(self, *, query: str, user_id: str | None = None) -> _StubResponse:
            return response

    monkeypatch.setattr(orchestrator_module, "Orchestrator", _Stub)


async def test_empty_message_short_circuits() -> None:
    result = await chat(user_id="u1", message="   ")
    assert result["jobs"] == []
    assert result["reply"]


async def test_search_intent_returns_job_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orchestrator(monkeypatch, _StubResponse("search_jobs", [_StubResult()]))

    async def no_capture(user_id: str, context: dict) -> None:
        return None

    monkeypatch.setattr(copilot, "_remember_preferences", no_capture)

    result = await chat(user_id="u1", message="senior backend roles in bengaluru")
    assert result["intent"] == "search_jobs"
    assert result["jobs"][0]["company"] == "Razorpay"
    assert result["jobs"][0]["id"] == "job-1"
    assert "Razorpay" in result["reply"]


async def test_empty_search_explains_the_india_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orchestrator(monkeypatch, _StubResponse("search_jobs", []))

    async def no_capture(user_id: str, context: dict) -> None:
        return None

    monkeypatch.setattr(copilot, "_remember_preferences", no_capture)

    result = await chat(user_id="u1", message="roles in berlin")
    assert result["jobs"] == []
    assert "India" in result["reply"]


async def test_orchestrator_failure_degrades_to_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Groq key / no Chroma must not surface as a 500 to the user."""
    import agents.orchestrator as orchestrator_module

    class _Boom:
        async def process_query(self, *, query: str, user_id: str | None = None) -> None:
            raise RuntimeError("groq unreachable")

    monkeypatch.setattr(orchestrator_module, "Orchestrator", _Boom)

    result = await chat(user_id="u1", message="what should I do today?")
    assert result["jobs"] == []
    assert result["error"] == "groq unreachable"
    assert "try again" in result["reply"].lower()


async def test_studio_result_is_narrated(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orchestrator(
        monkeypatch,
        _StubResponse("application_tracker", metadata={"data": {"applied": 3}}),
    )

    async def fake_narrate(message: str, intent: str, data: object, summary: str | None) -> str:
        return "You have 3 applications in flight."

    monkeypatch.setattr(copilot, "_narrate", fake_narrate)

    result = await chat(user_id="u1", message="how is my funnel doing?")
    assert result["intent"] == "application_tracker"
    assert result["reply"] == "You have 3 applications in flight."
    assert result["data"] == {"applied": 3}


async def test_general_intent_falls_back_to_grounded_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """GENERAL routes to the graph's error node — chat must still answer."""
    _stub_orchestrator(monkeypatch, _StubResponse("general"))

    async def fake_general(user_id: str, message: str, history: list | None) -> str:
        return "Start with your six saved roles."

    monkeypatch.setattr(copilot, "_general_reply", fake_general)

    result = await chat(user_id="u1", message="hey")
    assert result["reply"] == "Start with your six saved roles."


async def test_general_reply_survives_a_dead_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no LLM the copilot still answers from the deterministic briefing."""
    async def fake_briefing(*, user_id: str) -> dict:
        return {
            "headline": "2 things need chasing today.",
            "cards": [_card(card_id="a", kind="stale_application", title="Chase Swiggy", detail="d")],
            "stats": {},
        }

    async def fake_memories(**kwargs: object) -> list:
        return []

    async def boom(**kwargs: object) -> str:
        raise RuntimeError("no api key")

    monkeypatch.setattr(copilot, "build_briefing", fake_briefing)
    monkeypatch.setattr(copilot, "get_memories", fake_memories)
    monkeypatch.setattr("services.llm.generate_copilot_reply", boom)

    reply = await copilot._general_reply("u1", "what now?", None)
    assert "Chase Swiggy" in reply


async def _capture_model_context(
    monkeypatch: pytest.MonkeyPatch, cards: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run ``_general_reply`` over ``cards`` and return what the model was handed."""
    seen: dict[str, Any] = {}

    async def fake_briefing(*, user_id: str) -> dict[str, Any]:
        return {"headline": "h", "cards": cards, "stats": {"saved": 6}}

    async def fake_memories(**kwargs: object) -> list[Any]:
        return []

    async def fake_reply(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(copilot, "build_briefing", fake_briefing)
    monkeypatch.setattr(copilot, "get_memories", fake_memories)
    monkeypatch.setattr("services.llm.generate_copilot_reply", fake_reply)

    assert await copilot._general_reply("u1", "what now?", None) == "ok"
    return seen


async def test_outreach_cards_never_reach_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The follow-up card carries a typed next step and a person's name; the suggestion
    names a company. Neither may be sent to the model, in any field of the call."""
    marker = "ZQ7-ASHA-RAO-CALL-VIKRAM"
    cards = [
        _card(card_id="outreach_followup", kind="outreach_followup",
              title=f"Next step with Ledgerline: {marker}", detail=f"detail {marker}",
              tone="primary", meta={"count": 2, "thread_id": marker}, dismissible=False),
        _card(card_id="outreach_suggestion:c1", kind="outreach_suggestion",
              title=f"Reach out to {marker}", detail=f"reasons {marker}",
              meta={"company_id": marker, "reasons": [marker]}),
        _card(card_id="priority:apply_saved", kind="priority", title="Apply to your saved jobs",
              detail="You have 6 saved job(s).", tone="primary"),
    ]

    seen = await _capture_model_context(monkeypatch, cards)

    assert marker not in json.dumps(seen, default=str)
    assert seen["context"]["open_items"] == [
        {"title": "Apply to your saved jobs", "detail": "You have 6 saved job(s)."}
    ]


async def test_outreach_contributes_a_count_to_the_model_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        _card(card_id="outreach_followup", kind="outreach_followup", title="secret-title",
              detail="secret-detail", tone="primary",
              meta={"count": 2, "thread_id": "secret-thread"}, dismissible=False),
    ]

    seen = await _capture_model_context(monkeypatch, cards)

    assert seen["context"]["outreach_follow_ups_due"] == 2
    assert "secret" not in json.dumps(seen, default=str)
    assert seen["context"]["open_items"] == []


async def test_a_kind_nobody_has_cleared_stays_out_of_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlist, not a denylist: a card kind added later (an event, a practice nudge)
    is withheld until someone decides it may be sent."""
    marker = "ZQ7-FUTURE-KIND"
    cards = [
        _card(card_id="e1", kind="events_upcoming", title=marker, detail=marker),
        _card(card_id="p1", kind="practice_streak", title=marker, detail=marker),
    ]

    seen = await _capture_model_context(monkeypatch, cards)

    assert marker not in json.dumps(seen, default=str)


async def test_every_card_kind_the_briefing_had_before_outreach_still_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kinds = [
        "priority",
        "scheduled_interview",
        "stale_application",
        "saved_backlog",
        "closed_role",
        "interview_momentum",
    ]
    cards = [_card(card_id=kind, kind=kind, title=f"title {kind}", detail="d") for kind in kinds[:4]]

    seen = await _capture_model_context(monkeypatch, cards)
    assert [item["title"] for item in seen["context"]["open_items"]] == [
        f"title {kind}" for kind in kinds[:4]
    ]
    assert set(kinds) == copilot.MODEL_VISIBLE_CARD_KINDS


async def test_outreach_cards_do_not_use_up_the_models_four_open_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        _card(card_id="outreach_followup", kind="outreach_followup", title="x", detail="x",
              meta={"count": 1}),
        *[_card(card_id=f"stale_application:{n}", kind="stale_application", title=f"cold {n}",
                detail="d") for n in range(4)],
    ]

    seen = await _capture_model_context(monkeypatch, cards)

    assert [item["title"] for item in seen["context"]["open_items"]] == [
        f"cold {n}" for n in range(4)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Preference capture
# ─────────────────────────────────────────────────────────────────────────────

async def test_preferences_are_captured_from_a_search(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[dict] = []

    async def fake_get(**kwargs: object) -> list:
        return []

    async def fake_remember(**kwargs: object) -> dict:
        stored.append(kwargs)
        return kwargs

    monkeypatch.setattr(copilot, "get_memories", fake_get)
    monkeypatch.setattr(copilot, "remember", fake_remember)

    await copilot._remember_preferences(
        "u1", {"location": "Bengaluru", "skills": ["python"], "seniority": "senior"}
    )
    assert len(stored) == 1
    # Must read back as a sentence, not a template with a doubled noun.
    assert stored[0]["content"] == "Interested in senior python roles in Bengaluru"
    assert stored[0]["memory_type"] == copilot.PREFERENCE_TYPE


async def test_preferences_are_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking the same question twice must not stack identical memories."""
    stored: list[dict] = []

    async def fake_get(**kwargs: object) -> list:
        return [{"content": "Interested in senior python roles in Bengaluru"}]

    async def fake_remember(**kwargs: object) -> dict:
        stored.append(kwargs)
        return kwargs

    monkeypatch.setattr(copilot, "get_memories", fake_get)
    monkeypatch.setattr(copilot, "remember", fake_remember)

    await copilot._remember_preferences(
        "u1", {"location": "Bengaluru", "skills": ["python"], "seniority": "senior"}
    )
    assert stored == []


async def test_contentless_context_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[dict] = []

    async def fake_remember(**kwargs: object) -> dict:
        stored.append(kwargs)
        return kwargs

    monkeypatch.setattr(copilot, "remember", fake_remember)
    await copilot._remember_preferences("u1", {"location": None, "skills": []})
    assert stored == []


# ─────────────────────────────────────────────────────────────────────────────
# Reply formatting
# ─────────────────────────────────────────────────────────────────────────────

def test_markdown_never_reaches_a_chat_bubble() -> None:
    """The RAG agent writes for a document; the bubble renders literal text."""
    summary = "- **Total results:** 10\n- **Top match:** `Senior Engineer`\n\n## Notes"
    cleaned = _plain(summary)
    for marker in ("**", "`", "##", "- "):
        assert marker not in cleaned
    assert "Total results: 10" in cleaned


def test_plain_tolerates_empty_input() -> None:
    assert _plain("") == ""


def test_plain_drops_blank_runs_between_paragraphs() -> None:
    assert _plain("one\n\n\ntwo") == "one\ntwo"


async def test_location_only_preference_reads_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """No seniority or skills must not leave a doubled space in the sentence."""
    stored: list[dict] = []

    async def fake_get(**kwargs: object) -> list:
        return []

    async def fake_remember(**kwargs: object) -> dict:
        stored.append(kwargs)
        return kwargs

    monkeypatch.setattr(copilot, "get_memories", fake_get)
    monkeypatch.setattr(copilot, "remember", fake_remember)

    await copilot._remember_preferences("u1", {"location": "Pune", "skills": []})
    assert stored[0]["content"] == "Interested in roles in Pune"


# ── a saved role the employer has stopped listing ────────────────────────────


def test_closed_saved_role_gets_a_card_naming_the_date():
    from datetime import UTC, datetime

    card = copilot._closed_role_card(
        application_id="app-1",
        role="Backend Engineer",
        company="Stripe",
        closed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert card["id"] == "closed_role:app-1"
    assert card["kind"] == "closed_role"
    assert "Backend Engineer" in card["title"] and "Stripe" in card["title"]
    assert "2 Oct 2026" in card["detail"]
    assert card["meta"]["application_id"] == "app-1"


def test_closed_role_card_points_at_the_tracker_and_at_similar_roles():
    from datetime import UTC, datetime

    card = copilot._closed_role_card(
        application_id="app-1", role="Backend Engineer", company="Stripe",
        closed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert [a["href"] for a in card["actions"]] == ["/applications", "/search"]
