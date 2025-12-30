"""Authentication handling for Claude Code sessions."""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path

from .config import ANTHROPIC_API_KEY, get_user_workspace

logger = logging.getLogger("codesmith.auth")


class AuthMethod(Enum):
    """Authentication method for Claude Code."""

    OAUTH = "oauth"
    API_KEY = "api_key"
    NONE = "none"


def get_credentials_path(user_id: str) -> Path:
    """Get path to user's credentials file.

    Args:
        user_id: Discord user ID

    Returns:
        Path to .credentials.json in user's workspace
    """
    workspace = get_user_workspace(user_id)
    return workspace / ".claude" / ".credentials.json"


def validate_credentials_json(json_str: str) -> dict | None:
    """Validate and parse credentials JSON.

    Args:
        json_str: Raw JSON string from user

    Returns:
        Parsed credentials dict if valid, None otherwise
    """
    try:
        data = json.loads(json_str.strip())
    except json.JSONDecodeError:
        logger.debug("Failed to parse credentials JSON")
        return None

    # Check for required structure
    if "claudeAiOauth" not in data:
        logger.debug("Missing claudeAiOauth key")
        return None

    oauth = data["claudeAiOauth"]
    required_keys = ["accessToken", "refreshToken", "expiresAt"]

    for key in required_keys:
        if key not in oauth:
            logger.debug(f"Missing required key: {key}")
            return None

    # Basic sanity checks
    if not isinstance(oauth["accessToken"], str) or not oauth["accessToken"]:
        return None
    if not isinstance(oauth["refreshToken"], str) or not oauth["refreshToken"]:
        return None
    if not isinstance(oauth["expiresAt"], int | float):
        return None

    return data


def store_credentials(user_id: str, credentials: dict) -> Path:
    """Store credentials in user's workspace.

    Args:
        user_id: Discord user ID
        credentials: Validated credentials dict

    Returns:
        Path where credentials were stored
    """
    creds_path = get_credentials_path(user_id)

    # Ensure directory exists
    creds_path.parent.mkdir(parents=True, exist_ok=True)

    # Write credentials
    with open(creds_path, "w") as f:
        json.dump(credentials, f, indent=2)

    # Restrict permissions
    creds_path.chmod(0o600)

    logger.info(f"Stored credentials for user {user_id}")
    return creds_path


def has_valid_credentials(user_id: str) -> bool:
    """Check if user has stored OAuth credentials.

    Args:
        user_id: Discord user ID

    Returns:
        True if valid credentials exist
    """
    creds_path = get_credentials_path(user_id)

    if not creds_path.exists():
        return False

    try:
        with open(creds_path) as f:
            data = json.load(f)
        return validate_credentials_json(json.dumps(data)) is not None
    except (json.JSONDecodeError, OSError):
        return False


def get_auth_method(user_id: str) -> AuthMethod:
    """Determine authentication method for a user.

    Priority:
    1. Per-user OAuth credentials
    2. Global ANTHROPIC_API_KEY
    3. None

    Args:
        user_id: Discord user ID

    Returns:
        AuthMethod to use
    """
    if has_valid_credentials(user_id):
        return AuthMethod.OAUTH

    if ANTHROPIC_API_KEY:
        return AuthMethod.API_KEY

    return AuthMethod.NONE


def delete_credentials(user_id: str) -> bool:
    """Remove user's stored credentials.

    Args:
        user_id: Discord user ID

    Returns:
        True if credentials were deleted
    """
    creds_path = get_credentials_path(user_id)

    if creds_path.exists():
        creds_path.unlink()
        logger.info(f"Deleted credentials for user {user_id}")
        return True

    return False
