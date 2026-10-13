from sqlalchemy import Column, String, Text
from app.database import Base


class JiraIssueNote(Base):
    __tablename__ = "jira_issue_note"

    jira_key = Column(String(30), primary_key=True, index=True)
    description = Column(Text, nullable=True)