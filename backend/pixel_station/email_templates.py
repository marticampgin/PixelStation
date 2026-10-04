"""User-reviewed email wording, separate from general personal memory."""

import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, Text, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base, get_session, new_id, now, record_dict

router = APIRouter(prefix="/api/google/gmail/templates", tags=["approved email templates"])


class EmailTemplate(Base):
    __tablename__ = "email_templates"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column()
    body: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)


class TemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=6000)
    keywords: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("name", "body")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter nonempty template wording and a name")
        return value.strip()

    @field_validator("keywords")
    @classmethod
    def bounded_keywords(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 80 for value in values):
            raise ValueError("Each keyword must contain 1–80 characters")
        return list(dict.fromkeys(value.strip() for value in values))


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _template(session: Session, id_: str) -> EmailTemplate:
    row = session.get(EmailTemplate, id_)
    if not row:
        raise HTTPException(404, "Email template not found")
    return row


def _review_sha256(row: EmailTemplate) -> str:
    reviewed = {key: getattr(row, key) for key in ("name", "body", "keywords")}
    return hashlib.sha256(json.dumps(reviewed, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _public_template(row: EmailTemplate) -> dict:
    return {**record_dict(row), "review_sha256": _review_sha256(row)}


@router.get("")
def list_templates(session: Session = Depends(get_session)):
    return [
        _public_template(row)
        for row in session.scalars(
            select(EmailTemplate).order_by(EmailTemplate.updated_at.desc()).limit(100)
        )
    ]


@router.post("", status_code=201)
def add_template(body: TemplateInput, session: Session = Depends(get_session)):
    row = EmailTemplate(**body.model_dump())
    session.add(row)
    session.commit()
    return _public_template(row)


@router.put("/{id_}")
def edit_template(id_: str, body: TemplateInput, session: Session = Depends(get_session)):
    row = _template(session, id_)
    # Explicitly write approved=False even when the loaded ORM value was already false.
    # An approval in another tab must never survive a subsequently committed text edit.
    matched_id = session.scalar(update(EmailTemplate).where(EmailTemplate.id == id_)
                             .values(**body.model_dump(), approved=False, updated_at=now())
                             .returning(EmailTemplate.id)
                             .execution_options(synchronize_session=False))
    if matched_id is None:
        raise HTTPException(404, "Email template not found")
    session.commit()
    session.refresh(row)
    return _public_template(row)


@router.post("/{id_}/approve")
def approve_template(id_: str, body: ReviewInput, session: Session = Depends(get_session)):
    if not body.confirmed:
        raise HTTPException(409, "Review the template wording before approving it")
    row = _template(session, id_)
    if body.review_sha256 != _review_sha256(row):
        raise HTTPException(409, "The template wording changed after it was displayed. Review the current wording again.")
    matched_id = session.scalar(update(EmailTemplate)
                             .where(EmailTemplate.id == id_, EmailTemplate.updated_at == row.updated_at,
                                    EmailTemplate.name == row.name, EmailTemplate.body == row.body,
                                    EmailTemplate.keywords == row.keywords)
                             .values(approved=True, updated_at=now())
                             .returning(EmailTemplate.id)
                             .execution_options(synchronize_session=False))
    if matched_id is None:
        session.rollback()
        raise HTTPException(409, "The template changed during approval. Review the current wording again.")
    session.commit()
    session.refresh(row)
    return _public_template(row)


@router.delete("/{id_}")
def delete_template(id_: str, confirmed: bool = False, session: Session = Depends(get_session)):
    if not confirmed:
        raise HTTPException(409, "Confirm removal of this email template")
    session.delete(_template(session, id_))
    session.commit()
    return {"deleted": True}


def retrieve_templates(
    session: Session, query: str, selected_ids: list[str] | None = None
) -> list[dict]:
    """Only approved exact selections or keyword matches enter drafting context."""
    rows = list(session.scalars(select(EmailTemplate).where(EmailTemplate.approved.is_(True))))
    if selected_ids:
        by_id = {row.id: row for row in rows}
        if any(id_ not in by_id for id_ in selected_ids):
            raise HTTPException(
                409, "A selected template was changed or is not approved. Review it again."
            )
        chosen = [by_id[id_] for id_ in dict.fromkeys(selected_ids)][:3]
    else:
        words = set(re.findall(r"\w+", query.casefold()))
        ranked = [
            (
                len(
                    words & set(re.findall(r"\w+", " ".join(row.keywords + [row.name]).casefold()))
                ),
                row,
            )
            for row in rows
        ]
        chosen = [
            row for score, row in sorted(ranked, key=lambda pair: pair[0], reverse=True) if score
        ][:3]
    result, remaining = [], 6000
    for row in chosen:
        if remaining <= 0:
            break
        content = row.body[:remaining]
        result.append({"id": row.id, "name": row.name, "body": content})
        remaining -= len(content)
    return result
