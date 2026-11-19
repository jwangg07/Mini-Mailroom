"""Validation for mail HTTP requests."""

from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

from app.mail.formatting import MAX_INLINE_IMAGES


class InlineImageRequest(BaseModel):
    attachment_ids: list[
        Annotated[
            str,
            StringConstraints(
                min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9_=-]+$"
            ),
        ]
    ] = Field(min_length=1, max_length=MAX_INLINE_IMAGES)


class MailLabelChange(BaseModel):
    label_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    applied: bool


class MailDraft(BaseModel):
    to: EmailStr
    subject: str = Field(max_length=998)
    body: str = Field(min_length=1, max_length=100_000)

    @field_validator("subject", "to")
    @classmethod
    def no_header_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Mail headers must be a single line")
        return value
