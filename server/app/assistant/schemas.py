"""Assistant conversation and review actions."""

from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

Prompt = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
]
MessageId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{1,128}$")]
DraftSubject = Annotated[str, StringConstraints(max_length=998, pattern=r"^[^\r\n]*$")]
DraftBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)
]


class ChatExchange(BaseModel):
    prompt: Prompt
    answer: str = Field(min_length=1, max_length=8_000)


class DraftContext(BaseModel):
    to: str = Field(max_length=320)
    subject: DraftSubject
    body: str = Field(max_length=100_000)


class AssistantRequest(BaseModel):
    prompt: Prompt
    message_id: MessageId | None = None
    history: list[ChatExchange] = Field(default_factory=list, max_length=4)
    draft: DraftContext | Literal["sent", "uncertain", "compose"] | None = None


class WritePermission(BaseModel):
    send: bool = False
    labels: bool = False
    label_limit: int | None = Field(None, ge=1)


class DraftAction(BaseModel):
    kind: Literal["draft"]
    subject: DraftSubject
    body: DraftBody
    to: EmailStr | None


class LabelAction(BaseModel):
    kind: Literal["label"]
    message_id: MessageId
    label_id: str
    label_name: str


AssistantAction = Annotated[DraftAction | LabelAction, Field(discriminator="kind")]


class MessageLabelUpdate(BaseModel):
    message_id: MessageId
    label_ids: list[str]


class AssistantResponse(BaseModel):
    answer: str
    actions: list[AssistantAction] | None = None
    label_updates: list[MessageLabelUpdate] | None = None
    draft_delivery: Literal["sent", "uncertain"] | None = None
