from datetime import date

import pytest
from pydantic import ValidationError

from app.schema.jobs import Application, JobPosting

POSTING = {
    "title": "Backend Engineer",
    "company": "Fabrikam",
    "seniority": "Mid-level",
    "mustHave": ["Python", "PostgreSQL"],
    "niceToHave": ["Kafka"],
    "responsibilities": ["Build and run REST APIs"],
    "keyTerms": ["event-driven", "REST APIs"],
}


def test_job_posting_keeps_exact_spellings() -> None:
    posting = JobPosting.model_validate(POSTING)
    assert posting.must_have == ("Python", "PostgreSQL")
    assert posting.key_terms == ("event-driven", "REST APIs")


def test_application_round_trips() -> None:
    application = Application.model_validate(
        {
            "id": "app_fabrikam_2026_09",
            "appliedOn": "2026-09-27",
            "company": "Fabrikam",
            "position": "Backend Engineer",
            "posting": POSTING,
            "postingText": "We are hiring a backend engineer...",
            "variantId": "backend",
            "titleVariant": "Software Engineer",
            "bulletIds": ["nw_orders", "nw_export"],
            "swapsUsed": [{"bulletId": "nw_orders", "text": "event-driven Python services"}],
            "pdfSha256": "0" * 64,
        }
    )
    assert application.applied_on == date(2026, 9, 27)
    assert Application.model_validate_json(application.model_dump_json()) == application


def test_application_rejects_a_malformed_pdf_hash() -> None:
    with pytest.raises(ValidationError):
        Application.model_validate(
            {
                "id": "a1",
                "appliedOn": "2026-09-27",
                "company": "Fabrikam",
                "position": "Engineer",
                "posting": {"title": "Engineer"},
                "postingText": "...",
                "titleVariant": "Engineer",
                "pdfSha256": "not-a-hash",
            }
        )
