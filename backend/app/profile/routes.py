"""`/api/profile`: view and edit the signed-in owner's profile, its tailoring metadata and its
versions (T-042, T-043)."""

import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import Db
from app.profile.edits import (
    BasicsEdit,
    BulletEdit,
    CourseworkEdit,
    Edited,
    EducationEdit,
    OpenQuestionEdit,
    ProjectEdit,
    RoleEdit,
    RoleRulesEdit,
    RoleTypesEdit,
    SkillPresetsEdit,
    SkillsEdit,
    SummariesEdit,
    SwapsEdit,
    basics_changes,
    bullet_changes,
    coursework_changes,
    education_changes,
    new_question_changes,
    project_changes,
    question_changes,
    role_changes,
    role_rules_changes,
    role_types_changes,
    skill_presets_changes,
    skills_changes,
    summaries_changes,
    swaps_changes,
)
from app.profile.page import (
    Conflict,
    NotFound,
    ProfilePage,
    Restore,
    VersionDetail,
    VersionList,
    load_page,
    load_version,
    load_versions,
    restore,
    save_edit,
)
from app.profile_ops import ProfileOpError
from app.schema.profile import Profile

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
async def profile(user: CurrentUser, db: Db, response: Response) -> ProfilePage:
    """The latest version of the profile, with its resume-rule findings. Everything is null
    before a profile is loaded."""
    response.headers.update(NO_STORE)
    return await load_page(db, user.id)


@router.put("/basics")
async def edit_basics(
    body: BasicsEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change the basics. The fields that changed are saved as a change set by you and a new
    version (FR-PRF-11); nothing changed saves nothing. 409 when the profile is no longer at
    `baseVersion`."""
    return await _save(db, user.id, response, body.base_version, lambda p: basics_changes(p, body))


@router.put("/work/{role_id}")
async def edit_role(
    role_id: str, body: RoleEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change a role, as `PUT /api/profile/basics` does the basics."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: role_changes(p, role_id, body)
    )


@router.put("/education/{education_id}")
async def edit_education(
    education_id: str, body: EducationEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change an education entry, as `PUT /api/profile/basics` does the basics."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: education_changes(p, education_id, body)
    )


@router.put("/projects/{project_id}")
async def edit_project(
    project_id: str, body: ProjectEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change a project, as `PUT /api/profile/basics` does the basics."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: project_changes(p, project_id, body)
    )


@router.put("/bullets/{bullet_id}")
async def edit_bullet(
    bullet_id: str, body: BulletEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change a bullet: its text, status (benching needs a reason, FR-PRF-4), strength,
    verification and tags, as `PUT /api/profile/basics` does the basics. Your own words aren't
    checked by the verifier, but the resume rules' findings show on the page."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: bullet_changes(p, bullet_id, body)
    )


@router.put("/work/{role_id}/rules")
async def edit_role_rules(
    role_id: str, body: RoleRulesEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change a role's tailoring rules (FR-PRF-5): its approved title variants, whether it's
    load-bearing or held during education, the role types it's kept or cut for, its
    sensitivity and its honesty boundaries."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: role_rules_changes(p, role_id, body)
    )


@router.put("/education/{education_id}/coursework")
async def edit_coursework(
    education_id: str, body: CourseworkEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change an education entry's full coursework list, its coursework for each role type and
    its rules (FR-PRF-7)."""
    return await _save(
        db,
        user.id,
        response,
        body.base_version,
        lambda p: coursework_changes(p, education_id, body),
    )


@router.put("/bullets/{bullet_id}/swaps")
async def edit_swaps(
    bullet_id: str, body: SwapsEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change a bullet's swaps, the approved phrasings tailored resumes can use (FR-WRT-5)."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: swaps_changes(p, bullet_id, body)
    )


@router.put("/role-types")
async def edit_role_types(
    body: RoleTypesEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change the role types. One still in use can't be removed."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: role_types_changes(p, body)
    )


@router.put("/skills")
async def edit_skills(
    body: SkillsEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change the skills and the gaps list (FR-PRF-6)."""
    return await _save(db, user.id, response, body.base_version, lambda p: skills_changes(p, body))


@router.put("/skill-presets")
async def edit_skill_presets(
    body: SkillPresetsEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change the skills presets, one per role type (FR-PRF-9)."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: skill_presets_changes(p, body)
    )


@router.put("/summaries")
async def edit_summaries(
    body: SummariesEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change the summary variants, one per role type (FR-PRF-8)."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: summaries_changes(p, body)
    )


@router.post("/open-questions")
async def add_open_question(
    body: OpenQuestionEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Add an open question, blocking the bullets it names (FR-PRF-10)."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: new_question_changes(p, body)
    )


@router.put("/open-questions/{question_id}")
async def edit_open_question(
    question_id: str, body: OpenQuestionEdit, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Change an open question: its wording, its status (open, answered or closed) and the
    bullets it blocks."""
    return await _save(
        db, user.id, response, body.base_version, lambda p: question_changes(p, question_id, body)
    )


@router.get("/versions")
async def versions(user: CurrentUser, db: Db, response: Response) -> VersionList:
    """Every version, newest first, with what each one's change set did."""
    response.headers.update(NO_STORE)
    return await load_versions(db, user.id)


@router.get("/versions/{number}")
async def version(number: int, user: CurrentUser, db: Db, response: Response) -> VersionDetail:
    """A version, with what it changed against its parent."""
    response.headers.update(NO_STORE)
    with _errors():
        return await load_version(db, user.id, number)


@router.post("/versions/{number}/restore")
async def restore_version(
    number: int, body: Restore, user: CurrentUser, db: Db, response: Response
) -> ProfilePage:
    """Save a copy of version `number` as the newest version (FR-PRF-3); the versions in between
    stay. 409 when the profile is no longer at `baseVersion`, or already matches that version."""
    response.headers.update(NO_STORE)
    with _errors():
        page = await restore(db, user.id, number, body.base_version)
    await db.commit()
    return page


async def _save(
    db: AsyncSession,
    user_id: uuid.UUID,
    response: Response,
    base_version: int,
    build: Callable[[Profile], Edited],
) -> ProfilePage:
    response.headers.update(NO_STORE)
    with _errors():
        page = await save_edit(db, user_id, base_version, build)
    await db.commit()
    return page


@contextmanager
def _errors() -> Generator[None]:
    """Answer a change that can't be made with its HTTP status."""
    try:
        yield
    except NotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except Conflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except ProfileOpError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
