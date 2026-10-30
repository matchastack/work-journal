"""`/api/profile` for the signed-in owner, against the test database (T-042, T-043)."""

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.models import User
from app.db.profiles import (
    SavedChangeSet,
    Version,
    get_change_set,
    latest_version,
    list_change_sets,
    save_version,
)
from app.main import create_app
from app.schema.profile import Profile

BROWSER = "https://testserver"
FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
REWORDED = "Cut the nightly export from 50 to 12 minutes by batching database writes."
NORTHWIND = "Engineer, Northwind Traders"
_random = random.SystemRandom()


def seed[T](database_url: str, build: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `build` in a transaction of its own, which commits, so the app sees its rows."""

    async def run() -> T:
        engine = create_async_engine(database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session, session.begin():
                return await build(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def delete_users_made(database_url: str) -> Iterator[None]:
    """The tests commit, so the users each one makes are deleted afterwards, with their rows."""

    async def users(session: AsyncSession) -> set[uuid.UUID]:
        return set(await session.scalars(select(User.id)))

    before = seed(database_url, users)
    yield

    async def delete_new(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.not_in(before)))

    seed(database_url, delete_new)


class Owner:
    """A signed-in user of the test's own, with the fixture profile loaded as version 1 unless
    `profile` is None."""

    def __init__(self, database_url: str, profile: Profile | None = PROFILE) -> None:
        self.database_url = database_url
        self.login = f"owner-{uuid.uuid4().hex[:8]}"

        async def sign_in(session: AsyncSession) -> tuple[uuid.UUID, str, str]:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login=self.login)
            session.add(user)
            await session.flush()
            if profile is not None:
                await save_version(session, user.id, profile, author="owner")
            token, row = start_session(session, user.id)
            return user.id, token, row.csrf_token

        self.id, self.session_token, self.csrf_token = seed(database_url, sign_in)

    @property
    def csrf(self) -> dict[str, str]:
        return {CSRF_HEADER: self.csrf_token}

    def latest(self) -> Version:
        async def query(session: AsyncSession) -> Version:
            version = await latest_version(session, self.id)
            assert version is not None
            return version

        return seed(self.database_url, query)

    def change_sets(self) -> list[SavedChangeSet]:
        async def query(session: AsyncSession) -> list[SavedChangeSet]:
            return await list_change_sets(session, self.id, None)

        return seed(self.database_url, query)

    def change_set(self, change_set_id: uuid.UUID) -> SavedChangeSet:
        async def query(session: AsyncSession) -> SavedChangeSet:
            return await get_change_set(session, self.id, change_set_id)

        return seed(self.database_url, query)


def client_for(database_url: str, *owners: Owner) -> TestClient:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset(owner.login for owner in owners),
    )
    return TestClient(
        create_app(settings), base_url=BROWSER, cookies={SESSION_COOKIE: owners[0].session_token}
    )


@pytest.fixture
def owner(database_url: str) -> Owner:
    return Owner(database_url)


@pytest.fixture
def client(database_url: str, owner: Owner) -> Iterator[TestClient]:
    with client_for(database_url, owner) as client:
        yield client


def bullet(**changes: Any) -> dict[str, Any]:
    """The nw_export bullet as its form sends it, with `changes`."""
    return {
        "baseVersion": 1,
        "text": EXPORT,
        "status": "active",
        "statusReason": None,
        "strength": "high",
        "verification": "yes",
        "verificationNote": None,
        "tags": [],
        **changes,
    }


def put(client: TestClient, owner: Owner, path: str, body: dict[str, Any]) -> Any:
    return client.put(f"/api/profile/{path}", json=body, headers=owner.csrf)


def restore(client: TestClient, owner: Owner, number: int, base_version: int) -> Any:
    return client.post(
        f"/api/profile/versions/{number}/restore",
        json={"baseVersion": base_version},
        headers=owner.csrf,
    )


def bullet_in(page: dict[str, Any], role: int, index: int) -> dict[str, Any]:
    return page["profile"]["work"][role]["highlights"][index]


# --- Reading the profile -------------------------------------------------------------------------


def test_the_profile_needs_a_signed_in_owner_and_the_csrf_header(
    owner: Owner, client: TestClient
) -> None:
    assert client.put("/api/profile/bullets/nw_export", json=bullet()).status_code == 403
    client.cookies.clear()
    assert client.get("/api/profile").status_code == 401
    assert client.get("/api/profile/versions").status_code == 401
    assert put(client, owner, "bullets/nw_export", bullet()).status_code == 401


def test_the_page_shows_the_latest_version_and_the_resume_rules(client: TestClient) -> None:
    response = client.get("/api/profile")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    page = response.json()
    assert page["version"] | {"createdAt": None} == {
        "number": 1,
        "parent": None,
        "author": "owner",
        "createdAt": None,
        "restoredFrom": None,
        "summary": None,
    }
    assert Profile.model_validate(page["profile"]) == PROFILE
    assert page["lint"] == {
        "findings": [],
        "notes": [
            "education[springfield_state].rules: rule 1 isn't checked: \"Show the minor after "
            'the major."'
        ],
    }


def test_nothing_shows_before_a_profile_is_loaded(database_url: str) -> None:
    owner = Owner(database_url, profile=None)
    with client_for(database_url, owner) as client:
        assert client.get("/api/profile").json() == {
            "version": None,
            "profile": None,
            "lint": None,
        }
        refused = put(client, owner, "bullets/nw_export", bullet())
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "there's no profile to edit yet: load it with wj db load-profile"
        )
        assert client.get("/api/profile/versions").json() == {"versions": []}


# --- Editing -------------------------------------------------------------------------------------


def test_an_edit_is_a_change_set_by_the_owner_and_a_new_version(
    owner: Owner, client: TestClient
) -> None:
    """FR-PRF-11, FR-PRF-2."""
    response = put(
        client,
        owner,
        "bullets/nw_export",
        bullet(text=REWORDED, status="benched", statusReason="Older work.", tags=["data"]),
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    page = response.json()
    assert (page["version"]["number"], page["version"]["parent"], page["version"]["author"]) == (
        2,
        1,
        "owner",
    )
    assert page["version"]["summary"] == f"Edited a bullet under {NORTHWIND}"
    edited = bullet_in(page, 0, 1)
    assert (edited["text"], edited["status"], edited["statusReason"], edited["tags"]) == (
        REWORDED,
        "benched",
        "Older work.",
        ["data"],
    )
    latest = owner.latest()
    assert (latest.number, latest.author) == (2, "owner")
    assert latest.change_set_id is not None
    saved = owner.change_set(latest.change_set_id)
    assert (saved.change_set.author, saved.change_set.status) == ("owner", "applied")
    assert saved.change_set.base_version == 1
    assert saved.decided_at is not None
    assert [op.op.op for op in saved.change_set.ops] == [
        "edit_bullet",
        "set_bullet_status",
        "update_field",
    ]
    assert {op.status for op in saved.change_set.ops} == {"accepted"}
    assert client.get("/api/inbox").json() == {"changeSets": [], "profileVersion": 2}


def test_a_save_that_changes_nothing_makes_no_version(owner: Owner, client: TestClient) -> None:
    response = put(client, owner, "bullets/nw_export", bullet())
    assert response.status_code == 200
    assert response.json()["version"]["number"] == 1
    assert owner.latest().number == 1
    assert owner.change_sets() == []


def test_a_save_from_an_old_page_is_refused(owner: Owner, client: TestClient) -> None:
    """An edit made on a page loaded before another change mustn't undo it."""
    assert put(client, owner, "bullets/nw_export", bullet(text=REWORDED)).status_code == 200
    stale = put(client, owner, "bullets/nw_orders", bullet(text="Built services."))
    assert stale.status_code == 409
    assert stale.json()["detail"] == (
        "your profile changed since this page loaded: it's now at version 2, so check it and "
        "try again"
    )
    assert owner.latest().number == 2
    assert len(owner.change_sets()) == 1


def test_an_edit_must_find_its_target_and_follow_the_rules(
    owner: Owner, client: TestClient
) -> None:
    missing = put(client, owner, "bullets/nw_gone", bullet())
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "there's no bullet nw_gone in your profile",
    )
    role = {
        "baseVersion": 1,
        "institution": "Northwind Traders",
        "studyType": "Course",
        "area": "Shipping",
        "honours": None,
        "location": None,
        "url": None,
        "startDate": "2025-08",
        "endDate": None,
    }
    assert put(client, owner, "education/northwind", role).status_code == 404
    unreasoned = put(client, owner, "bullets/nw_export", bullet(status="benched"))
    assert unreasoned.status_code == 422
    assert put(client, owner, "bullets/nw_export", bullet(tags=["Not a tag"])).status_code == 422
    without_tags = bullet()
    del without_tags["tags"]
    assert put(client, owner, "bullets/nw_export", without_tags).status_code == 422
    assert owner.latest().number == 1


def test_resume_rule_findings_show_but_dont_block_a_save(owner: Owner, client: TestClient) -> None:
    response = put(client, owner, "bullets/nw_export", bullet(text="Cut the export by TODO."))
    assert response.status_code == 200
    page = response.json()
    assert page["version"]["number"] == 2
    assert page["lint"]["findings"] == [
        {
            "rule": "R2",
            "severity": "error",
            "location": "work[northwind].highlights[nw_export]",
            "message": 'placeholder text "TODO"',
            "where": f"A bullet under {NORTHWIND}",
        }
    ]


def test_basics_roles_education_and_projects_can_be_edited(
    owner: Owner, client: TestClient
) -> None:
    basics = {
        "baseVersion": 1,
        "name": "Casey Morgan",
        "label": "Backend Engineer",
        "email": "casey@example.com",
        "phone": "",
        "url": "https://example.com",
        "summary": None,
        "location": {"city": "Springfield", "region": "", "countryCode": "US"},
        "profiles": [
            {"network": "GitHub", "username": "casey-example", "url": None},
            {"network": "Mastodon", "username": "@casey", "url": ""},
        ],
    }
    page = put(client, owner, "basics", basics).json()
    assert page["version"]["summary"] == "Edited your basics"
    role = {
        "baseVersion": 2,
        "name": "Northwind Traders",
        "position": "Software Engineer",
        "location": "Springfield",
        "url": None,
        "startDate": "2025-08",
        "endDate": "2026-09",
        "summary": "Order systems.",
    }
    page = put(client, owner, "work/northwind", role).json()
    assert page["version"]["summary"] == "Edited the role Software Engineer, Northwind Traders"
    education = {
        "baseVersion": 3,
        "institution": "Springfield State University",
        "studyType": "Bachelor of Science",
        "area": "Computer Science",
        "honours": None,
        "location": "Springfield",
        "url": None,
        "startDate": "2020-08",
        "endDate": "2024-05",
    }
    assert put(client, owner, "education/springfield_state", education).status_code == 200
    project = {
        "baseVersion": 4,
        "name": "Recipe Box",
        "description": "Share recipes with friends.",
        "keywords": ["TypeScript", "React"],
        "url": None,
        "startDate": "2023-06",
        "endDate": "2023-09",
        "status": "active",
        "statusReason": None,
        "tags": ["web"],
    }
    page = put(client, owner, "projects/recipe_box", project).json()
    assert page["version"]["number"] == 5
    profile = Profile.model_validate(page["profile"])
    assert (profile.basics.label, profile.basics.phone) == ("Backend Engineer", None)
    assert profile.basics.location is not None
    assert profile.basics.location.region is None
    assert [link.network for link in profile.basics.profiles] == ["GitHub", "Mastodon"]
    assert profile.basics.profiles[0].url is None
    northwind = profile.work[0]
    assert (northwind.position, northwind.end_date, northwind.summary) == (
        "Software Engineer",
        "2026-09",
        "Order systems.",
    )
    assert northwind.title_variants == PROFILE.work[0].title_variants, "T-043's fields stay"
    assert (profile.education[0].honours, profile.education[0].location) == (None, "Springfield")
    assert profile.projects[0].keywords == ("TypeScript", "React")
    assert profile.projects[0].url is None


# --- History and restoring -----------------------------------------------------------------------


def test_the_history_lists_every_version_newest_first(owner: Owner, client: TestClient) -> None:
    put(client, owner, "bullets/nw_export", bullet(text=REWORDED))
    put(client, owner, "bullets/nw_export", bullet(baseVersion=2, text=REWORDED, tags=["data"]))
    response = client.get("/api/profile/versions")
    assert response.headers["Cache-Control"] == "no-store"
    versions = response.json()["versions"]
    assert [(v["number"], v["parent"], v["author"], v["summary"]) for v in versions] == [
        (3, 2, "owner", f"Edited a bullet under {NORTHWIND}"),
        (2, 1, "owner", f"Edited a bullet under {NORTHWIND}"),
        (1, None, "owner", None),
    ]


def test_a_version_lists_what_it_changed_against_its_parent(
    owner: Owner, client: TestClient
) -> None:
    put(client, owner, "bullets/nw_export", bullet(text=REWORDED, strength="medium"))
    detail = client.get("/api/profile/versions/2")
    assert detail.headers["Cache-Control"] == "no-store"
    reworded, strength = detail.json()["changes"]
    assert detail.json()["version"]["number"] == 2
    assert reworded == {
        "kind": "changed",
        "path": "work[northwind].highlights[nw_export].text",
        "title": f"Text of a bullet under {NORTHWIND}",
        "context": None,
        "before": EXPORT,
        "after": REWORDED,
        "diff": [
            {"kind": "same", "text": "Cut the nightly export "},
            {"kind": "removed", "text": "job "},
            {"kind": "same", "text": "from 50 to 12 minutes by batching database writes."},
        ],
    }
    assert (strength["title"], strength["context"], strength["before"], strength["after"]) == (
        f"Strength of a bullet under {NORTHWIND}",
        REWORDED,
        "high",
        "medium",
    )
    assert client.get("/api/profile/versions/1").json()["changes"] == []
    assert client.get("/api/profile/versions/9").status_code == 404


def test_restoring_saves_the_old_profile_as_a_new_version(owner: Owner, client: TestClient) -> None:
    """FR-PRF-3: the versions in between stay."""
    put(client, owner, "bullets/nw_export", bullet(text=REWORDED))
    response = restore(client, owner, 1, base_version=2)
    assert response.status_code == 200
    page = response.json()
    assert (page["version"]["number"], page["version"]["restoredFrom"]) == (3, 1)
    assert page["version"]["summary"] is None
    assert Profile.model_validate(page["profile"]) == PROFILE
    versions = client.get("/api/profile/versions").json()["versions"]
    assert [v["number"] for v in versions] == [3, 2, 1]
    (change,) = client.get("/api/profile/versions/3").json()["changes"]
    assert (change["before"], change["after"]) == (REWORDED, EXPORT)


def test_restoring_refuses_the_current_profile_and_an_old_page(
    owner: Owner, client: TestClient
) -> None:
    put(client, owner, "bullets/nw_export", bullet(text=REWORDED))
    current = restore(client, owner, 2, base_version=2)
    assert (current.status_code, current.json()["detail"]) == (
        409,
        "version 2 is already your current profile",
    )
    stale = restore(client, owner, 1, base_version=1)
    assert stale.status_code == 409
    assert "it's now at version 2" in stale.json()["detail"]
    assert restore(client, owner, 9, base_version=2).status_code == 404
    assert restore(client, owner, 1, base_version=2).status_code == 200
    same = restore(client, owner, 1, base_version=3)
    assert (same.status_code, same.json()["detail"]) == (
        409,
        "version 1 is the same as your current profile",
    )
    assert owner.latest().number == 3


def test_each_owner_sees_and_edits_only_their_own_profile(database_url: str, owner: Owner) -> None:
    other = Owner(database_url)

    async def rename(session: AsyncSession) -> None:
        basics = PROFILE.basics.model_copy(update={"name": "Robin Lee"})
        await save_version(
            session, other.id, PROFILE.model_copy(update={"basics": basics}), author="owner"
        )

    seed(database_url, rename)
    with client_for(database_url, owner, other) as client:
        assert client.get("/api/profile").json()["version"]["number"] == 1
        assert client.get("/api/profile/versions/2").status_code == 404
        assert put(client, owner, "bullets/nw_export", bullet(text=REWORDED)).status_code == 200
    assert owner.latest().number == 2
    latest = other.latest()
    assert (latest.number, latest.profile.basics.name) == (2, "Robin Lee")
    assert latest.profile.work[0].highlights[1].text == EXPORT


# --- Tailoring metadata (T-043) -----------------------------------------------------------------


def rules(**changes: Any) -> dict[str, Any]:
    """Northwind's tailoring rules as their form sends them, with `changes`."""
    return {
        "baseVersion": 1,
        "titleVariants": ["Software Engineer", "Backend Engineer"],
        "loadBearing": True,
        "duringEducation": False,
        "keepFor": ["backend", "data"],
        "cutFor": [],
        "sensitivity": "sensitive",
        "honestyBoundaries": ["Did not own the deployment pipeline."],
        **changes,
    }


def skills_body(**changes: Any) -> dict[str, Any]:
    skills = [
        skill.model_dump(mode="json", exclude={"notes"}) | {"previousName": skill.name}
        for skill in PROFILE.skills
    ]
    return {"baseVersion": 1, "skills": skills, "skillGaps": list(PROFILE.skill_gaps), **changes}


def test_metadata_edits_need_a_signed_in_owner_and_the_csrf_header(
    owner: Owner, client: TestClient
) -> None:
    assert client.put("/api/profile/work/northwind/rules", json=rules()).status_code == 403
    assert client.put("/api/profile/skills", json=skills_body()).status_code == 403
    client.cookies.clear()
    assert put(client, owner, "work/northwind/rules", rules()).status_code == 401
    created = client.post("/api/profile/open-questions", json={}, headers=owner.csrf)
    assert created.status_code == 401


def test_a_roles_rules_save_as_a_version_by_the_owner(owner: Owner, client: TestClient) -> None:
    """FR-PRF-5, FR-PRF-11."""
    response = put(
        client, owner, "work/northwind/rules", rules(titleVariants=["Platform Engineer"])
    )
    assert response.status_code == 200
    page = response.json()
    assert page["version"]["number"] == 2
    assert page["version"]["summary"] == f"Edited the tailoring rules of the role {NORTHWIND}"
    assert page["profile"]["work"][0]["titleVariants"] == ["Platform Engineer"]
    latest = owner.latest()
    assert latest.change_set_id is not None
    saved = owner.change_set(latest.change_set_id)
    assert (saved.change_set.author, saved.change_set.status) == ("owner", "applied")
    unchanged = put(
        client,
        owner,
        "work/northwind/rules",
        rules(baseVersion=2, titleVariants=["Platform Engineer"]),
    )
    assert unchanged.json()["version"]["number"] == 2


def test_a_metadata_edit_that_doesnt_fit_says_why(owner: Owner, client: TestClient) -> None:
    unknown = put(client, owner, "work/northwind/rules", rules(keepFor=["mobile"]))
    assert (unknown.status_code, unknown.json()["detail"]) == (
        422,
        "mobile isn't one of your role types: add it under Role types first",
    )
    both = put(client, owner, "work/northwind/rules", rules(cutFor=["backend"]))
    assert both.status_code == 422
    in_use = put(
        client,
        owner,
        "role-types",
        {"baseVersion": 1, "roleTypes": [{"id": "backend", "name": "Backend"}]},
    )
    assert in_use.status_code == 422
    assert in_use.json()["detail"].startswith("the role type data is still used by")
    assert put(client, owner, "work/contoso-gone/rules", rules()).status_code == 404
    assert owner.latest().number == 1


def test_skills_gaps_presets_and_summaries_change_the_profile(
    owner: Owner, client: TestClient
) -> None:
    """FR-PRF-6, FR-PRF-8, FR-PRF-9."""
    gaps = put(client, owner, "skills", skills_body(skillGaps=["Rust", "Swift", "Haskell"]))
    assert gaps.json()["profile"]["skillGaps"] == ["Rust", "Swift", "Haskell"]
    assert gaps.json()["version"]["summary"] == "Edited your skills"
    claimed = put(client, owner, "skills", skills_body(baseVersion=2, skillGaps=["Python"]))
    assert claimed.status_code == 422
    preset = {"roleType": "data", "lines": [{"label": "Languages", "skills": ["Python", "SQL"]}]}
    presets = put(client, owner, "skill-presets", {"baseVersion": 2, "skillPresets": [preset]})
    assert presets.json()["profile"]["skillPresets"] == [preset]
    summary = {"roleType": "backend", "text": "Backend engineer building Python services."}
    summaries = put(client, owner, "summaries", {"baseVersion": 3, "summaries": [summary]})
    assert summaries.json()["profile"]["summaries"] == [summary]
    assert summaries.json()["version"]["number"] == 4


def test_swaps_and_coursework_change_their_bullet_and_entry(
    owner: Owner, client: TestClient
) -> None:
    """FR-WRT-5, FR-PRF-7."""
    swap = {
        "text": "high-volume order services",
        "replaces": None,
        "contexts": [],
        "origin": "approved",
    }
    swaps = put(client, owner, "bullets/nw_orders/swaps", {"baseVersion": 1, "swaps": [swap]})
    assert swaps.status_code == 200
    assert swaps.json()["profile"]["work"][0]["highlights"][0]["swaps"] == [swap]
    coursework = {
        "baseVersion": 2,
        "courses": ["Algorithms", "Databases"],
        "courseworkSubsets": [{"roleType": "data", "courses": ["Databases"]}],
        "rules": ["Never print the GPA."],
    }
    saved = put(client, owner, "education/springfield_state/coursework", coursework)
    assert saved.status_code == 200
    education = saved.json()["profile"]["education"][0]
    assert (education["courses"], education["rules"]) == (
        ["Algorithms", "Databases"],
        ["Never print the GPA."],
    )
    wrong = coursework | {
        "baseVersion": 3,
        "courseworkSubsets": [{"roleType": "data", "courses": ["Compilers"]}],
    }
    assert put(client, owner, "education/springfield_state/coursework", wrong).status_code == 422


def test_open_questions_are_created_answered_and_closed(owner: Owner, client: TestClient) -> None:
    """FR-PRF-10."""
    question = {
        "baseVersion": 1,
        "question": "How long did the export take before?",
        "status": "open",
        "bulletIds": ["nw_export"],
    }
    created = client.post("/api/profile/open-questions", json=question, headers=owner.csrf)
    assert created.status_code == 200
    page = created.json()
    assert page["profile"]["openQuestions"][-1] == {
        "id": "q_1",
        "question": "How long did the export take before?",
        "status": "open",
        "answerFactIds": [],
    }
    assert page["profile"]["work"][0]["highlights"][1]["needs"] == ["q_1"]
    answered = put(
        client, owner, "open-questions/q_1", question | {"baseVersion": 2, "status": "answered"}
    )
    assert answered.json()["version"]["summary"] == "Answered an open question"
    closed = put(
        client,
        owner,
        "open-questions/q_1",
        question | {"baseVersion": 3, "status": "closed", "bulletIds": []},
    )
    page = closed.json()
    assert page["profile"]["openQuestions"][-1]["status"] == "closed"
    assert page["profile"]["work"][0]["highlights"][1]["needs"] == []
    missing = put(client, owner, "open-questions/q_gone", question | {"baseVersion": 4})
    assert missing.status_code == 404
