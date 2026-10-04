import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update

from pixel_station.app import create_app
from pixel_station.database import Database
from pixel_station.email_templates import EmailTemplate, retrieve_templates


def test_reviewed_template_changes_need_fresh_approval_and_survive_restart(tmp_path):
    app = create_app(tmp_path, discover=False)
    with TestClient(app) as client:
        made = client.post("/api/google/gmail/templates", json={
            "name": "Rental acknowledgement", "body": "Thank you for your rental request.",
            "keywords": ["rental", "booking"],
        }).json()
        id_ = made["id"]
        assert not made["approved"]
        with app.state.database.session() as session:
            assert retrieve_templates(session, "rental booking") == []
        assert client.post(f"/api/google/gmail/templates/{id_}/approve", json={"confirmed": False, "review_sha256": made["review_sha256"]}).status_code == 409
        assert client.post(f"/api/google/gmail/templates/{id_}/approve", json={"confirmed": True, "review_sha256": made["review_sha256"]}).json()["approved"]
        with app.state.database.session() as session:
            assert retrieve_templates(session, "a booking")[0]["body"] == made["body"]
        changed = client.put(f"/api/google/gmail/templates/{id_}", json={
            "name": made["name"], "body": "Updated wording for review.", "keywords": ["booking"],
        }).json()
        assert not changed["approved"]
        with app.state.database.session() as session:
            with pytest.raises(HTTPException, match="not approved"):
                retrieve_templates(session, "booking", [id_])
        assert client.delete(f"/api/google/gmail/templates/{id_}").status_code == 409
    with TestClient(create_app(tmp_path, discover=False)) as client:
        assert client.get("/api/google/gmail/templates").json()[0]["body"] == changed["body"]


def test_approved_template_context_is_bounded_and_never_uses_unmatched_templates(tmp_path):
    db = Database(tmp_path)
    db.migrate()
    with db.session() as session:
        for index in range(4):
            session.add(EmailTemplate(id=str(index), name=f"Template {index}",
                                     body="x" * 5000, keywords=["rental"], approved=True))
        session.add(EmailTemplate(id="private", name="Unreviewed", body="Secret", keywords=["rental"]))
        session.commit()
        assert retrieve_templates(session, "unrelated") == []
        retrieved = retrieve_templates(session, "rental")
        assert len(retrieved) <= 3
        assert sum(len(row["body"]) for row in retrieved) == 6000
        assert all(row["id"] != "private" for row in retrieved)
        assert retrieve_templates(session, "unrelated", ["2"])[0]["id"] == "2"
    db.engine.dispose()


@pytest.mark.parametrize("body", [
    {"name": " ", "body": "wording"},
    {"name": "Name", "body": " "},
    {"name": "Name", "body": "wording", "keywords": ["x" * 81]},
])
def test_template_limits_reject_blank_or_oversized_keyword_inputs(tmp_path, body):
    with TestClient(create_app(tmp_path, discover=False)) as client:
        assert client.post("/api/google/gmail/templates", json=body).status_code == 422


@pytest.mark.parametrize("change", ["name", "body", "keywords"])
def test_template_approval_requires_the_exact_displayed_content_version(tmp_path, change):
    with TestClient(create_app(tmp_path, discover=False)) as client:
        original = {"name": "TEST rental reply", "body": "Original TEST wording.", "keywords": ["rental"]}
        made = client.post("/api/google/gmail/templates", json=original).json()
        changed = {**original, change: ["booking"] if change == "keywords" else "Changed TEST wording"}
        current = client.put(f"/api/google/gmail/templates/{made['id']}", json=changed).json()
        endpoint = f"/api/google/gmail/templates/{made['id']}/approve"
        stale = client.post(endpoint, json={"confirmed": True, "review_sha256": made["review_sha256"]})
        assert stale.status_code == 409 and "changed after it was displayed" in stale.text
        assert not client.get("/api/google/gmail/templates").json()[0]["approved"]
        assert current["review_sha256"] != made["review_sha256"]
        fresh = client.post(endpoint, json={"confirmed": True, "review_sha256": current["review_sha256"]})
        assert fresh.status_code == 200 and fresh.json()["approved"]
        assert fresh.json()["review_sha256"] == current["review_sha256"]


def test_template_approval_rejects_edit_between_read_and_atomic_update(tmp_path, monkeypatch):
    import pixel_station.email_templates as module

    app = create_app(tmp_path, discover=False)
    with TestClient(app) as client:
        made = client.post("/api/google/gmail/templates", json={
            "name": "TEST reply", "body": "Displayed TEST wording.", "keywords": ["rental"],
        }).json()
        load = module._template

        def edit_after_read(session, id_):
            row = load(session, id_)
            with app.state.database.session() as other:
                other.execute(update(EmailTemplate).where(EmailTemplate.id == id_)
                              .values(body="Unseen TEST wording.", approved=False, updated_at="different-version"))
                other.commit()
            return row

        monkeypatch.setattr(module, "_template", edit_after_read)
        response = client.post(f"/api/google/gmail/templates/{made['id']}/approve",
                               json={"confirmed": True, "review_sha256": made["review_sha256"]})
        assert response.status_code == 409 and "changed during approval" in response.text
        current = client.get("/api/google/gmail/templates").json()[0]
        assert current["body"] == "Unseen TEST wording." and not current["approved"]


def test_template_edit_revokes_approval_committed_after_the_edit_was_loaded(tmp_path, monkeypatch):
    import pixel_station.email_templates as module

    app = create_app(tmp_path, discover=False)
    with TestClient(app) as client:
        made = client.post("/api/google/gmail/templates", json={
            "name": "TEST reply", "body": "Original TEST wording.", "keywords": ["rental"],
        }).json()
        load = module._template

        def approve_after_read(session, id_):
            row = load(session, id_)
            assert row.approved is False
            with app.state.database.session() as other:
                other.execute(update(EmailTemplate).where(EmailTemplate.id == id_)
                              .values(approved=True, updated_at="approved-in-other-tab"))
                other.commit()
            return row

        monkeypatch.setattr(module, "_template", approve_after_read)
        response = client.put(f"/api/google/gmail/templates/{made['id']}", json={
            "name": "TEST reply", "body": "New unreviewed TEST wording.", "keywords": ["rental"],
        })
        assert response.status_code == 200
        assert not response.json()["approved"]
        current = client.get("/api/google/gmail/templates").json()[0]
        assert current["body"] == "New unreviewed TEST wording." and not current["approved"]


def test_template_approval_does_not_accept_an_unversioned_review(tmp_path):
    with TestClient(create_app(tmp_path, discover=False)) as client:
        made = client.post("/api/google/gmail/templates", json={"name": "TEST", "body": "TEST wording."}).json()
        assert client.post(f"/api/google/gmail/templates/{made['id']}/approve", json={"confirmed": True}).status_code == 422
