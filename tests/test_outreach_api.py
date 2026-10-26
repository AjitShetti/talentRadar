"""
tests/test_outreach_api.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The ``/api/v1/outreach`` routes.

* Almost every test mocks the service function the handler calls (``AsyncMock``,
  patched on the ``services.outreach`` module, as ``tests/test_company_map_api.py``
  does), so what is under test is the router: validation, delegation, the status
  codes and the one place a refused request becomes an HTTP error.
* ``test_other_users_thread_is_404_everywhere`` is the exception. It runs the real
  services on in-memory SQLite (``outreach_db``) behind two real bearer tokens, to
  prove that another user's thread, person and company are a 404 on every route and
  that nothing of the owner's changes.

Every person and company here is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import create_access_token
from api.main import app
from services.outreach import drafts, people, threads
from services.outreach.errors import Conflict, Invalid, NotFound
from storage.models import OutreachPerson, OutreachThread, OutreachTouch
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

PREFIX = "/api/v1/outreach"
TID = str(uuid.uuid4())
PID = str(uuid.uuid4())
CID = str(uuid.uuid4())

TOUCH = {"channel": "linkedin", "direction": "out"}
DRAFT = {"format": "linkedin_note", "person_id": PID}
SUMMARY = {"id": TID, "company_name": "Tessel AI", "stage": "planned"}
DETAIL = {**SUMMARY, "people": [], "touches": []}

#: (method, path, json body, the service function the handler delegates to)
HANDLERS: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "", None, "touches.home"),
    ("GET", "/settings", None, "touches.get_target"),
    ("PUT", "/settings", {"daily_target": 3}, "touches.set_target"),
    ("GET", "/pack", None, "pack.get_pack"),
    ("POST", "/pack/seen", {"item_ids": []}, "pack.mark_seen"),
    ("POST", f"/pack/companies/{CID}/write", None, "pack.write_company"),
    ("POST", f"/pack/items/{PID}/write", None, "pack.write_follow_up"),
    ("GET", "/suggestions", None, "suggest.suggestions"),
    ("POST", f"/suggestions/{CID}/dismiss", None, "suggest.dismiss_suggestion"),
    ("GET", "/companies/search?q=te", None, "threads.search_companies"),
    ("GET", f"/companies/{CID}", None, "threads.thread_for_company"),
    ("POST", "/threads", {"company_name": "Tessel AI"}, "threads.create_thread"),
    ("GET", f"/threads/{TID}", None, "people.get_thread"),
    ("PATCH", f"/threads/{TID}", {"note": "x"}, "threads.update_thread"),
    ("DELETE", f"/threads/{TID}", None, "threads.delete_thread"),
    ("POST", f"/threads/{TID}/people", {"name": "Asha Rao"}, "people.add_person"),
    ("PATCH", f"/threads/{TID}/people/{PID}", {"state": "dropped"}, "people.update_person"),
    ("DELETE", f"/threads/{TID}/people/{PID}", None, "people.remove_person"),
    ("POST", f"/threads/{TID}/touches", TOUCH, "touches.record_touch"),
    ("POST", f"/threads/{TID}/draft", DRAFT, "drafts.draft"),
]


def service(name: str, **kw: Any) -> Any:
    """Patch ``services.outreach.<name>`` with an ``AsyncMock``."""
    return patch(f"services.outreach.{name}", AsyncMock(**kw))


# --- authentication --------------------------------------------------------------


def mounted_routes() -> list[tuple[str, str]]:
    """Every ``(METHOD, path)`` the app serves under the prefix.

    Read from the OpenAPI document rather than ``app.routes``: FastAPI 0.139 nests
    included routers, so the route list no longer holds the handlers directly.
    """
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(PREFIX)
        for method in operations
    )


def test_the_route_table_is_the_documented_one() -> None:
    mounted = {(method, path.removeprefix(PREFIX)) for method, path in mounted_routes()}

    assert mounted == {
        ("GET", ""),
        ("GET", "/settings"),
        ("PUT", "/settings"),
        ("GET", "/pack"),
        ("POST", "/pack/seen"),
        ("POST", "/pack/companies/{company_id}/write"),
        ("POST", "/pack/items/{item_id}/write"),
        ("GET", "/suggestions"),
        ("POST", "/suggestions/{company_id}/dismiss"),
        ("GET", "/companies/search"),
        ("GET", "/companies/{company_id}"),
        ("POST", "/threads"),
        ("GET", "/threads/{thread_id}"),
        ("PATCH", "/threads/{thread_id}"),
        ("DELETE", "/threads/{thread_id}"),
        ("POST", "/threads/{thread_id}/people"),
        ("PATCH", "/threads/{thread_id}/people/{person_id}"),
        ("DELETE", "/threads/{thread_id}/people/{person_id}"),
        ("POST", "/threads/{thread_id}/touches"),
        ("POST", "/threads/{thread_id}/draft"),
    }


@pytest.mark.asyncio
async def test_routes_require_auth(api_client: AsyncClient) -> None:
    # Every route the app mounts under the prefix, not the ones this file remembers.
    routes = mounted_routes()
    assert len(routes) == len(HANDLERS)

    for method, template in routes:
        path = (
            template.replace("{thread_id}", TID)
            .replace("{person_id}", PID)
            .replace("{company_id}", CID)
            .replace("{item_id}", PID)
        )
        if path.endswith("/companies/search"):
            path += "?q=te"
        response = await api_client.request(method, path, json={})
        assert response.status_code in (401, 403), (method, path, response.status_code)


# --- creating a thread -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_201_then_200(auth_client: AsyncClient) -> None:
    with service("threads.create_thread", side_effect=[(SUMMARY, True), (SUMMARY, False)]) as m:
        first = await auth_client.post(f"{PREFIX}/threads", json={"company_id": CID})
        again = await auth_client.post(f"{PREFIX}/threads", json={"company_id": CID})

    assert first.status_code == 201
    assert first.json() == {"thread": SUMMARY, "created": True}
    assert again.status_code == 200
    assert again.json() == {"thread": SUMMARY, "created": False}
    kwargs = m.await_args.kwargs
    assert kwargs == {"company_id": CID, "company_name": None, "job_id": None, "source": "drawer"}


@pytest.mark.asyncio
async def test_create_passes_the_typed_name_job_and_source(auth_client: AsyncClient) -> None:
    body = {"company_name": "Tessel AI", "job_id": TID, "source": "typed"}
    with service("threads.create_thread", return_value=(SUMMARY, True)) as m:
        response = await auth_client.post(f"{PREFIX}/threads", json=body)

    assert response.status_code == 201
    assert m.await_args.kwargs == {
        "company_id": None,
        "company_name": "Tessel AI",
        "job_id": TID,
        "source": "typed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"source": "drawer"},
        {"company_name": ""},
        {"company_name": "   "},
        {"company_id": ""},
        {"company_name": "x" * 257},
        {"company_name": "Tessel AI", "source": "carrier-pigeon"},
    ],
)
async def test_create_needs_company_id_or_name(
    auth_client: AsyncClient, body: dict[str, Any]
) -> None:
    with service("threads.create_thread") as m:
        response = await auth_client.post(f"{PREFIX}/threads", json=body)

    assert response.status_code == 422
    m.assert_not_awaited()


# --- errors ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "status"), [(NotFound, 404), (Conflict, 409), (Invalid, 422)])
async def test_errors_map_to_status(
    auth_client: AsyncClient, error: type[Exception], status: int
) -> None:
    # Through every handler: none may let a refused request surface as a 500.
    for method, path, body, target in HANDLERS:
        with service(target, side_effect=error("Nothing to see here")):
            response = await auth_client.request(method, f"{PREFIX}{path}", json=body)

        assert response.status_code == status, (method, path)
        assert response.json() == {"detail": "Nothing to see here"}, (method, path)


@pytest.mark.asyncio
async def test_a_failure_that_is_not_a_refusal_stays_a_500(auth_client: AsyncClient) -> None:
    # Only an OutreachError is the caller's doing; anything else is ours and is
    # answered by the app's opaque 500, never mapped to a 4xx.
    with service("people.get_thread", side_effect=ValueError("boom")):
        response = await auth_client.get(f"{PREFIX}/threads/{TID}")

    assert response.status_code == 500
    assert "boom" not in response.text


# --- requests that must never reach a service ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"channel": "carrier-pigeon", "direction": "out"},
        {"channel": "linkedin", "direction": "sideways"},
        {"direction": "out"},
        {**TOUCH, "note": "x" * 501},
        {**TOUCH, "body": "x" * 2001},
        {**TOUCH, "next_step": "x" * 141},
        {**TOUCH, "happened_at": "yesterday"},
        {**TOUCH, "next_step_at": "next week"},
    ],
)
async def test_touch_rejects_unknown_channel(
    auth_client: AsyncClient, body: dict[str, Any]
) -> None:
    with service("touches.record_touch") as m:
        response = await auth_client.post(f"{PREFIX}/threads/{TID}/touches", json=body)

    assert response.status_code == 422
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_touch_is_delegated_with_every_field(auth_client: AsyncClient) -> None:
    body = {
        "person_id": PID,
        "channel": "email",
        "direction": "in",
        "note": "Said to try again in March",
        "body": "Hello, thanks for writing.",
        "happened_at": "2026-10-14T09:30:00+05:30",
        "next_step": "Send my portfolio",
        "next_step_at": "2026-10-20",
    }
    with service("touches.record_touch", return_value=DETAIL) as m:
        response = await auth_client.post(f"{PREFIX}/threads/{TID}/touches", json=body)

    assert response.status_code == 200
    assert response.json() == DETAIL
    call = m.await_args
    assert call.args[1] == TID
    kwargs = call.kwargs
    assert kwargs["person_id"] == PID
    assert (kwargs["channel"], kwargs["direction"]) == ("email", "in")
    assert kwargs["note"] == "Said to try again in March"
    assert kwargs["body"] == "Hello, thanks for writing."
    assert kwargs["happened_at"].isoformat() == "2026-10-14T09:30:00+05:30"
    assert kwargs["next_step"] == "Send my portfolio"
    assert kwargs["next_step_at"].isoformat() == "2026-10-20"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [-1, 11, 100, "three", None])
async def test_settings_bounds(auth_client: AsyncClient, target: Any) -> None:
    with service("touches.set_target") as m:
        response = await auth_client.put(f"{PREFIX}/settings", json={"daily_target": target})

    assert response.status_code == 422
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_are_read_and_saved(auth_client: AsyncClient) -> None:
    with service("touches.get_target", return_value=3):
        read = await auth_client.get(f"{PREFIX}/settings")
    with service("touches.set_target", return_value=10) as m:
        saved = await auth_client.put(f"{PREFIX}/settings", json={"daily_target": 10})
    with service("touches.set_target", return_value=0):
        off = await auth_client.put(f"{PREFIX}/settings", json={"daily_target": 0})

    assert read.json() == {"daily_target": 3}
    assert saved.json() == {"daily_target": 10}
    assert m.await_args.args[1] == 10
    assert off.status_code == 200
    assert off.json() == {"daily_target": 0}


@pytest.mark.asyncio
async def test_a_company_search_needs_two_characters(auth_client: AsyncClient) -> None:
    rows = [{"id": CID, "name": "Tessel AI", "summary": None, "area": None}]
    with service("threads.search_companies", return_value=rows) as m:
        short = [
            await auth_client.get(f"{PREFIX}/companies/search", params={"q": q})
            for q in ("", "t", " t ", "   ")
        ]
        missing = await auth_client.get(f"{PREFIX}/companies/search")
        long = await auth_client.get(f"{PREFIX}/companies/search", params={"q": "q" * 81})
        found = await auth_client.get(f"{PREFIX}/companies/search", params={"q": " te "})

    assert [r.status_code for r in short] == [422, 422, 422, 422]
    assert missing.status_code == 422
    assert long.status_code == 422
    assert found.status_code == 200
    assert found.json() == rows
    m.assert_awaited_once_with("te")


@pytest.mark.asyncio
async def test_suggestions_limit_is_one_to_twenty_five(auth_client: AsyncClient) -> None:
    payload = {"suggestions": [], "profile_complete": True}
    with service("suggest.suggestions", return_value=payload) as m:
        default = await auth_client.get(f"{PREFIX}/suggestions")
        top = await auth_client.get(f"{PREFIX}/suggestions", params={"limit": 25})
        zero = await auth_client.get(f"{PREFIX}/suggestions", params={"limit": 0})
        many = await auth_client.get(f"{PREFIX}/suggestions", params={"limit": 26})

    assert default.json() == payload
    assert top.status_code == 200
    assert (zero.status_code, many.status_code) == (422, 422)
    assert [c.kwargs["limit"] for c in m.await_args_list] == [10, 25]


@pytest.mark.asyncio
async def test_dismiss_and_delete_answer_204_with_no_body(auth_client: AsyncClient) -> None:
    with service("suggest.dismiss_suggestion") as dismiss:
        gone = await auth_client.post(f"{PREFIX}/suggestions/{CID}/dismiss")
    with service("threads.delete_thread") as delete:
        deleted = await auth_client.delete(f"{PREFIX}/threads/{TID}")

    assert (gone.status_code, gone.content) == (204, b"")
    assert (deleted.status_code, deleted.content) == (204, b"")
    assert dismiss.await_args.args[1] == CID
    assert delete.await_args.args[1] == TID


@pytest.mark.asyncio
async def test_the_company_drawer_reads_a_thread_or_null(auth_client: AsyncClient) -> None:
    with service("threads.thread_for_company", side_effect=[SUMMARY, None]):
        has = await auth_client.get(f"{PREFIX}/companies/{CID}")
        none = await auth_client.get(f"{PREFIX}/companies/{CID}")

    assert has.json() == {"thread": SUMMARY}
    assert none.json() == {"thread": None}


# --- patching a thread -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_sends_only_what_the_client_sent(auth_client: AsyncClient) -> None:
    body = {
        "note": None,  # sent as null: clear it
        "next_step": "Ask about the team",
        "next_step_at": "2026-10-20",
        "close": {"outcome": "not_now"},
    }
    with (
        service("threads.update_thread") as update,
        service("people.get_thread", return_value=DETAIL) as read,
    ):
        response = await auth_client.patch(f"{PREFIX}/threads/{TID}", json=body)

    assert response.status_code == 200
    assert response.json() == DETAIL
    patch_arg = update.await_args.args[2]
    assert set(patch_arg) == {"note", "next_step", "next_step_at", "close_outcome"}
    assert patch_arg["note"] is None
    assert patch_arg["next_step"] == "Ask about the team"
    assert patch_arg["next_step_at"].isoformat() == "2026-10-20"
    assert patch_arg["close_outcome"] == "not_now"
    read.assert_awaited_once()
    assert read.await_args.args[1] == TID


@pytest.mark.asyncio
async def test_patch_maps_job_application_and_reopen(auth_client: AsyncClient) -> None:
    with (
        service("threads.update_thread") as update,
        service("people.get_thread", return_value=DETAIL),
    ):
        await auth_client.patch(
            f"{PREFIX}/threads/{TID}",
            json={"job_id": CID, "application_id": None, "reopen": True},
        )
        await auth_client.patch(f"{PREFIX}/threads/{TID}", json={})
        await auth_client.patch(f"{PREFIX}/threads/{TID}", json={"reopen": False, "close": None})

    sent = [call.args[2] for call in update.await_args_list]
    assert sent == [{"job_id": CID, "application_id": None, "reopen": True}, {}, {}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"close": {"outcome": "ghosted"}},
        {"close": {}},
        {"close": "offer"},
        {"note": "x" * 501},
        {"next_step": "x" * 141},
        {"next_step_at": "soon"},
    ],
)
async def test_patch_rejects_bad_values(auth_client: AsyncClient, body: dict[str, Any]) -> None:
    with service("threads.update_thread") as update:
        response = await auth_client.patch(f"{PREFIX}/threads/{TID}", json=body)

    assert response.status_code == 422
    update.assert_not_awaited()


# --- people and drafts -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_person_passes_the_fields_through(auth_client: AsyncClient) -> None:
    body = {
        "name": "Asha Rao",
        "title": "Head of Platform",
        "email": "asha@example.com",
        "linkedin_url": "https://www.linkedin.com/in/asha-rao-example",
    }
    with service("people.add_person", return_value=DETAIL) as m:
        named = await auth_client.post(f"{PREFIX}/threads/{TID}/people", json=body)
        stored = await auth_client.post(f"{PREFIX}/threads/{TID}/people", json={"contact_id": CID})

    assert named.json() == DETAIL
    assert stored.status_code == 200
    first, second = m.await_args_list
    assert first.args[1] == TID
    assert first.kwargs == {"contact_id": None, **body}
    assert second.kwargs == {
        "contact_id": CID,
        "name": None,
        "title": None,
        "email": None,
        "linkedin_url": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"name": "x" * 129},
        {"title": "x" * 129},
        {"email": "x" * 257},
        {"linkedin_url": "x" * 513},
    ],
)
async def test_add_person_bounds_the_text(auth_client: AsyncClient, body: dict[str, Any]) -> None:
    with service("people.add_person") as m:
        response = await auth_client.post(f"{PREFIX}/threads/{TID}/people", json=body)

    assert response.status_code == 422
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_person_snoozes_or_drops(auth_client: AsyncClient) -> None:
    url = f"{PREFIX}/threads/{TID}/people/{PID}"
    with service("people.update_person", return_value=DETAIL) as m:
        snoozed = await auth_client.patch(url, json={"snooze_days": 14})
        dropped = await auth_client.patch(url, json={"state": "dropped"})
        refused = [
            await auth_client.patch(url, json=body)
            for body in ({"snooze_days": 0}, {"snooze_days": 15}, {"state": "replied"})
        ]

    assert (snoozed.status_code, dropped.status_code) == (200, 200)
    assert [r.status_code for r in refused] == [422, 422, 422]
    first, second = m.await_args_list
    assert first.args[1:] == (TID, PID)
    assert first.kwargs == {"snooze_days": 14, "state": None}
    assert second.kwargs == {"snooze_days": None, "state": "dropped"}
    assert len(m.await_args_list) == 2


@pytest.mark.asyncio
async def test_remove_person_returns_the_thread(auth_client: AsyncClient) -> None:
    with service("people.remove_person", return_value=DETAIL) as m:
        response = await auth_client.delete(f"{PREFIX}/threads/{TID}/people/{PID}")

    assert response.status_code == 200
    assert response.json() == DETAIL
    assert m.await_args.args[1:] == (TID, PID)


@pytest.mark.asyncio
async def test_draft_is_delegated_and_its_payload_returned(auth_client: AsyncClient) -> None:
    result = {
        "format": "email",
        "message": "Hi",
        "subject": "Hello",
        "generated": True,
        "reason": None,
        "remaining_today": 19,
        "max_chars": 1200,
    }
    body = {
        "person_id": PID,
        "format": "email",
        "relationship": "alumnus",
        "issue_url": "https://github.com/tessel-ai/engine/issues/41",
    }
    with service("drafts.draft", return_value=result) as m:
        response = await auth_client.post(f"{PREFIX}/threads/{TID}/draft", json=body)

    assert response.json() == result
    assert m.await_args.args[1] == TID
    assert m.await_args.kwargs == {
        "person_id": PID,
        "fmt": "email",
        "relationship": "alumnus",
        "issue_url": "https://github.com/tessel-ai/engine/issues/41",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"format": "telegram"},
        {"format": "email", "relationship": "x" * 41},
        {"format": "github_comment", "issue_url": "x" * 513},
    ],
)
async def test_draft_rejects_bad_requests(auth_client: AsyncClient, body: dict[str, Any]) -> None:
    with service("drafts.draft") as m:
        response = await auth_client.post(f"{PREFIX}/threads/{TID}/draft", json=body)

    assert response.status_code == 422
    m.assert_not_awaited()


# --- ownership, end to end -----------------------------------------------------------------


def client_for(user_id: str) -> AsyncClient:
    token = create_access_token(
        data={"sub": user_id, "email": f"{user_id[:8]}@example.com", "role": "user"}
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def count(factory: Factory, model: Any) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


@pytest.fixture
def no_outside_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The draft service reads the resume through its own Postgres session and calls
    the model. Neither may happen for a request that is refused, and neither may reach
    the network if a regression lets it through."""
    monkeypatch.setattr(drafts, "get_active_resume", AsyncMock(return_value=None))
    monkeypatch.setattr(drafts, "_chat", AsyncMock(side_effect=AssertionError("model called")))


@pytest.mark.asyncio
async def test_other_users_thread_is_404_everywhere(
    outreach_db: Factory, no_outside_calls: None
) -> None:
    async with outreach_db() as session:
        owner = await make_user(session)
        stranger = await make_user(session)
        company = await make_company(session, "Tessel AI")
        contact = await make_contact(session, company, "founder", "Asha Rao")
    a, b = str(owner.id), str(stranger.id)
    summary, _ = await threads.create_thread(
        a, company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    tid = str(summary["id"])
    pid = next(
        p["id"] for p in (await people.add_person(a, tid, contact_id=str(contact.id)))["people"]
    )
    await threads.update_thread(a, tid, {"note": "mine"})
    before = await people.get_thread(a, tid)
    counts = [await count(outreach_db, m) for m in (OutreachThread, OutreachPerson, OutreachTouch)]
    assert counts == [1, 1, 0]

    thread_url = f"{PREFIX}/threads/{tid}"
    attempts: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", thread_url, None),
        ("PATCH", thread_url, {"note": "taken over", "close": {"outcome": "offer"}}),
        ("DELETE", thread_url, None),
        ("POST", f"{thread_url}/people", {"name": "Eve Mallory"}),
        ("PATCH", f"{thread_url}/people/{pid}", {"state": "dropped"}),
        ("DELETE", f"{thread_url}/people/{pid}", None),
        ("POST", f"{thread_url}/touches", {**TOUCH, "person_id": pid}),
        ("POST", f"{thread_url}/touches", TOUCH),
        ("POST", f"{thread_url}/draft", {"format": "linkedin_note", "person_id": pid}),
    ]
    async with client_for(b) as other, client_for(a) as mine:
        for method, url, body in attempts:
            response = await other.request(method, url, json=body)
            assert response.status_code == 404, (method, url, response.text)
            # Not yours reads exactly like not there.
            unknown = await other.request(method, url.replace(tid, str(uuid.uuid4())), json=body)
            assert unknown.status_code == 404
            assert unknown.json() == response.json(), (method, url)

        # The company drawer shows B no thread at all.
        drawer = await other.get(f"{PREFIX}/companies/{company.id}")
        assert (drawer.status_code, drawer.json()) == (200, {"thread": None})
        assert (await other.get(PREFIX)).json()["threads"] == []

        # And it was not a routing accident: the owner reads it fine.
        owned = await mine.get(thread_url)
        assert owned.status_code == 200
        assert owned.json()["note"] == "mine"

    assert await people.get_thread(a, tid) == before
    assert [
        await count(outreach_db, m) for m in (OutreachThread, OutreachPerson, OutreachTouch)
    ] == counts


@pytest.mark.asyncio
async def test_a_touch_on_a_closed_thread_is_recorded(
    outreach_db: Factory, no_outside_calls: None
) -> None:
    """Closing a thread does not forbid writing it down that you did reach someone."""
    async with outreach_db() as session:
        owner = await make_user(session)
        company = await make_company(session, "Tessel AI")
    uid = str(owner.id)

    async with client_for(uid) as client:
        created = await client.post(f"{PREFIX}/threads", json={"company_id": str(company.id)})
        tid = created.json()["thread"]["id"]
        closed = await client.patch(
            f"{PREFIX}/threads/{tid}", json={"close": {"outcome": "not_now"}}
        )
        touched = await client.post(f"{PREFIX}/threads/{tid}/touches", json=TOUCH)

    assert created.status_code == 201
    assert closed.json()["stage"] == "closed"
    assert touched.status_code == 200
    assert [t["channel"] for t in touched.json()["touches"]] == ["linkedin"]
