from pydantic import BaseModel, Field


class UserProfileOut(BaseModel):
    id: int
    email: str
    display_name: str
    meeting_link: str | None = None


class UserProfileUpdate(BaseModel):
    meeting_link: str | None = Field(default=None, max_length=500)
