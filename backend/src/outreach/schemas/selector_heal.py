from typing import Literal

from pydantic import BaseModel, Field

PageType = Literal["profile", "messaging", "search", "feed", "company", "other"]
Intent = Literal[
    "connectButton",     # main "Connect" button on profile
    "moreButton",        # "More" dropdown that sometimes hides Connect
    "addNoteButton",     # "Add a note" in connect modal
    "noteTextarea",      # the note input field
    "sendInvitationButton",  # "Send invitation" / "Send now"
    "messageButton",     # "Message" button on profile
    "composeEditor",     # DM compose textarea / contenteditable
    "sendDmButton",      # "Send" button in DM composer
    "connectionCard",    # profile link in the My Network connections list
]


class HealRequest(BaseModel):
    intent: Intent
    page_type: PageType = "profile"
    url: str = Field(default="", max_length=500)
    failed_selectors: list[str] = Field(default_factory=list, max_length=10)
    dom_snapshot: str = Field(default="", max_length=20000)


class HealResponse(BaseModel):
    selectors: list[str] = Field(default_factory=list, max_length=5)
    method: str
    model: str
    intent: Intent
    cost_usd: float | None = None
