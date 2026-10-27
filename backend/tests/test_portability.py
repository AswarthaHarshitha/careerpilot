"""Moving a career profile to another CareerPilot account: export, import, merge without duplicates."""

import io
import json

from tests.conftest import run_tasks
from tests.test_api_flow import upload_template


def test_profile_moves_between_accounts_without_retyping(auth_client, second_client):
    a = auth_client
    upload_template(a)
    a.put(
        "/api/profile",
        json={
            **a.get("/api/profile").json()["personal"],
            "full_name": "Test Person",
            "phone": "+91 90000 00000",
            "street": "12 Test Lane",
            "city": "Kadapa",
            "postal_code": "516001",
            "country": "India",
        },
    )
    a.post(
        "/api/profile/experiences", json={"organization": "Acme Labs", "title": "Intern", "bullets": ["Built APIs."]}
    )
    a.post("/api/profile/skills", json={"name": "Python"})
    a.put("/api/profile/answers", json={"question_key": "how_heard", "answer": "Company website"})
    export = a.get("/api/data/export")
    assert export.status_code == 200 and "portable" in export.json()

    b = second_client
    b.put("/api/profile", json={**b.get("/api/profile").json()["personal"], "full_name": "Kept Name"})
    r = b.post(
        "/api/data/import", files={"file": ("careerpilot-export.json", io.BytesIO(export.content), "application/json")}
    )
    assert r.status_code == 200, r.text
    counts = r.json()["imported"]
    assert counts["experiences"] == 1 and counts["skills"] >= 1 and counts["answers"] == 1 and counts["templates"] == 1
    run_tasks()
    prof = b.get("/api/profile").json()
    assert prof["personal"]["full_name"] == "Kept Name"  # existing values are never overwritten
    assert prof["personal"]["street"] == "12 Test Lane" and prof["personal"]["postal_code"] == "516001"
    assert any(e["organization"] == "Acme Labs" and e["bullets"] == ["Built APIs."] for e in prof["experiences"])
    templates = b.get("/api/templates").json()
    assert len(templates) == 1 and templates[0]["is_default"]
    # importing again adds nothing
    again = b.post(
        "/api/data/import", files={"file": ("x.json", io.BytesIO(export.content), "application/json")}
    ).json()
    assert sum(again["imported"].values()) == 0


def test_import_rejects_other_files(auth_client):
    r = auth_client.post(
        "/api/data/import", files={"file": ("x.json", io.BytesIO(json.dumps({"a": 1}).encode()), "application/json")}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_import"
