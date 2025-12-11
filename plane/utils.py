import hashlib
import hmac
from typing import Iterable, List, Optional, Dict, Any, Type, TypeVar, Union, cast

Json = Dict[str, Any]


# ---------- Signature verification ----------


def verify_plane_signature(
    secret: str, body: bytes, received_sig: Optional[str]
) -> bool:
    """
    Verify the HMAC-SHA256 signature of the incoming Plane webhook request.

    :param secret: Shared webhook secret.
    :param body: Raw HTTP request body (bytes).
    :param received_sig: Value of the X-Plane-Signature header (hex string).
    """
    if not received_sig:
        return False

    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected_sig = mac.hexdigest()
    # Use compare_digest to avoid timing attacks.
    return hmac.compare_digest(expected_sig, received_sig)


# ---------- Issue Helpers ----------

T = TypeVar("T")


def _get_nested_value(
    payload: Dict[str, Any],
    path: Iterable[str],
    expected_type: Optional[Union[Type[T], tuple[Type[Any], ...]]] = None,
    default: Optional[T] = None,
) -> Optional[T]:
    """
    Safely extract a nested value from a JSON-like dict.

    This walks the given key path (for example, ["activity", "actor", "id"])
    and returns the final value if all keys exist. If any intermediate key
    is missing, or the leaf value is missing, ``default`` is returned.

    If ``expected_type`` is provided, the value is only returned when
    ``isinstance(value, expected_type)`` is true; otherwise ``default``
    is returned.

    :param payload: Parsed JSON payload.
    :param path: Sequence of keys representing the nested path.
    :param expected_type: Optional type or tuple of types to enforce via isinstance.
    :param default: Default value to return when the path is missing or type check fails.
    :return: The nested value (possibly typed), or ``default``.
    """
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]

    if expected_type is not None and not isinstance(current, expected_type):
        return default

    return cast(Optional[T], current)


def get_actor_value_from_payload(
    payload: Dict[str, Any],
    key: str,
) -> Optional[str]:
    """
    Extract an actor field value from a Plane webhook payload.

    Looks for ``activity.actor.<key>`` and returns it as a string if present
    and well-formed; otherwise returns None.
    """
    return _get_nested_value(payload, ["activity", "actor", key], expected_type=str)


def get_activity_value_from_payload(
    payload: Dict[str, Any],
    key: str,
) -> Optional[str]:
    """
    Extract an activity field value from a Plane webhook payload.

    Looks for ``activity.<key>`` and returns it as a string if present
    and well-formed; otherwise returns None.
    """
    return _get_nested_value(payload, ["activity", key], expected_type=str)


def get_data_value_from_payload(
    payload: Dict[str, Any],
    key: str,
) -> Optional[str]:
    """
    Extract an data field value from a Plane webhook payload.

    Looks for ``data.<key>`` and returns it as a string if present
    and well-formed; otherwise returns None.
    """
    return _get_nested_value(payload, ["data", key], expected_type=str)


def get_assignee_name_list_from_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Extract a normalized list of assignee names from a Plane webhook payload.

    This function looks for ``data.assignees`` in the payload and supports
    assignee entries of the form::

        {"display_name": "username"}

    It returns a list of string names. If the field is missing, not a list,
    or contains malformed entries, those entries are skipped and an empty
    list is returned when nothing valid is found.

    :param payload: Parsed JSON payload from a Plane webhook.
    :return: List of assignee names as strings (possibly empty).
    """
    assignees_raw = _get_nested_value(
        payload,
        ["data", "assignees"],
        expected_type=list,
        default=[],
    )

    assignee_names: List[str] = [
        str(assignee["display_name"])
        for assignee in assignees_raw
        if isinstance(assignee, dict) and "display_name" in assignee
    ]

    return assignee_names


def _get_assignee_id_list_from_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Extract a normalized list of assignee IDs from a Plane webhook payload.

    This function looks for ``data.assignees`` in the payload and supports
    assignee entries of the form::

        {"id": "user-id"}  or  {"id": 123}

    It returns a list of string IDs. If the field is missing, not a list,
    or contains malformed entries, those entries are skipped and an empty
    list is returned when nothing valid is found.

    :param payload: Parsed JSON payload from a Plane webhook.
    :return: List of assignee IDs as strings (possibly empty).
    """
    assignees_raw = _get_nested_value(
        payload,
        ["data", "assignees"],
        expected_type=list,
        default=[],
    )

    assignee_ids: List[str] = [
        str(assignee["id"])
        for assignee in assignees_raw
        if isinstance(assignee, dict) and "id" in assignee
    ]

    return assignee_ids


def is_actor_sole_assignee(payload: Dict[str, Any]) -> bool:
    """
    Determine whether the actor is the sole assignee on the Plane issue.

    The function retrieves the list of assignee IDs from ``data.assignees``
    and the actor ID from ``activity.actor.id``. It returns ``True`` if:

    - The actor ID exists, and
    - There is exactly one assignee, and
    - That assignee is the actor.

    Otherwise, it returns ``False``.

    :param payload: Parsed JSON payload from a Plane webhook.
    :return: ``True`` if the actor is the only assignee, ``False`` otherwise.
    """
    assignee_ids: List[str] = _get_assignee_id_list_from_payload(payload)
    actor_id: Optional[str] = get_actor_value_from_payload(payload, "id")
    return (
        actor_id is not None and len(assignee_ids) == 1 and assignee_ids[0] == actor_id
    )


def was_non_actor_sole_assignee_removed(payload: dict) -> bool:
    """
    Return True if this update cleared the assignee list, and previously there
    was exactly one assignee who was not the actor.

    This is used to ensure we still send a notification even when
    `send_notification_with_no_assignees` is disabled, because clearing a
    non-actor sole assignee is significant for others.
    """
    field_changed: str | None = get_activity_value_from_payload(payload, "field")
    if field_changed != "assignee_ids":
        return False

    current_assignee_ids: list[str] = _get_assignee_id_list_from_payload(payload)
    if current_assignee_ids:
        # There are still assignees; nothing was fully cleared.
        return False

    # Get the previous assignee list from the activity's old value.
    previous_assignee_ids = get_activity_value_from_payload(payload, "old_value") or []

    if isinstance(previous_assignee_ids, str):
        # Adjust this parsing if your payload uses a different encoding.
        previous_assignee_ids = [
            assignee_id.strip()
            for assignee_id in previous_assignee_ids.split(",")
            if assignee_id.strip()
        ]

    if not isinstance(previous_assignee_ids, list):
        return False

    if len(previous_assignee_ids) != 1:
        # We only care about the "sole assignee" case.
        return False

    actor_id: str | None = get_actor_value_from_payload(payload, "id")
    previous_assignee_id: str = previous_assignee_ids[0]

    return bool(actor_id and previous_assignee_id and previous_assignee_id != actor_id)

def generate_issue_url(payload, workspace_base_url):
    project_id = get_data_value_from_payload(payload, "project")
    issue_id = get_data_value_from_payload(payload, "id")
    return f"{workspace_base_url}/projects/{project_id}/issues/{issue_id}"

def generate_comment_url(payload, workspace_base_url):
    project_id = get_data_value_from_payload(payload, "project")
    issue_id = get_data_value_from_payload(payload, "issue")
    comment_id = get_data_value_from_payload(payload, "id")
    return f"{workspace_base_url}/projects/{project_id}/issues/{issue_id}/#comment-{comment_id}"
