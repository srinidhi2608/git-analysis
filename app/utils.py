import re


USER_STORY_ID_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b", re.IGNORECASE)


def extract_user_story_id(text: str | None) -> str | None:
    """Extract the first issue tracker ID from a PR title or branch name."""
    if not text:
        return None

    match = USER_STORY_ID_PATTERN.search(text)
    if match is None:
        return None

    return match.group(1).upper()
