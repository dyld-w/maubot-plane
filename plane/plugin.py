import json
from typing import Any, Dict, List, Optional, Type

from aiohttp.web import Request, Response, json_response
from maubot import Plugin
from maubot.handlers import web
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from mautrix.types import RoomID, MessageType

from .utils import (
    verify_plane_signature,
    get_actor_value_from_payload,
    is_actor_sole_assignee,
    get_activity_value_from_payload,
    get_data_value_from_payload,
    get_assignee_name_list_from_payload,
    was_non_actor_sole_assignee_removed,
    generate_issue_url,
    generate_comment_url
)

FIELD_CHANGED_RENAMING: dict[str, str] = {
    "target_date": "due date",
    "name": "title",
}


class Config(BaseProxyConfig):
    """Configuration proxy for the PlaneBot plugin."""

    def do_update(self, helper: ConfigUpdateHelper) -> None:
        """Update config values from defaults and existing config."""
        helper.copy("room_id")
        helper.copy("secret")
        helper.copy("workspace_url")
        helper.copy("send_notification_with_no_assignees")
        helper.copy("send_notification_when_actor_is_sole_assignee")
        helper.copy("issue_updated_notification_fields")


class PlaneBot(Plugin):
    async def start(self) -> None:
        """Initialize the Plane bot and load configuration."""
        await super().start()
        self.config.load_and_update()

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        """Return the proxy config class for this plugin."""
        return Config

    @web.post("/webhook")
    async def webhook(self, request: Request) -> Response:
        body_bytes = await request.read()
        received_signature = request.headers.get("X-Plane-Signature", "")

        secret: str = self.config["secret"]
        room_id_value: str = self.config["room_id"]

        # Check HMAC signature
        if not verify_plane_signature(secret, body_bytes, received_signature):
            return json_response({"status": "unauthorized"}, status=403)

        # Load & validate JSON
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
            pretty_json = json.dumps(payload, indent=4)
            self.log.info(pretty_json)
        except json.JSONDecodeError:
            self.log.warning("Invalid JSON from Plane")
            return json_response({"status": "bad json"}, status=400)

        # Dispatch based on event type / activity
        event_type = payload.get("event")
        action_type = payload.get("action")
        message: Optional[str] = "test"

        if event_type == "issue":
            if action_type == "created":
                self.log.info("Issue created event")
                message = self.handle_issue_created(payload)
            if action_type == "updated":
                message = self.handle_issue_updated(payload)
        elif event_type == "issue_comment":
            if action_type == "created":
                self.log.info("Issue comment created event")
                message = self.handle_issue_comment(payload)
        else:
            self.log.info(f"Unhandled Plane event_type={event_type}")

        if not message:
            self.log.info("No message to send. Message contents empty.")
            return json_response({"status": "ok"})

        # RoomID is a str, wrap this here for the typechecker,
        # since send_markdown() and send_text() expect a RoomID
        mautrix_room_id = RoomID(room_id_value)
        msgtype = MessageType("m.text")

        self.log.info(
            f"Sending message ({msgtype}) to room {mautrix_room_id}: {message}"
        )
        try:
            await self.client.send_markdown(
                mautrix_room_id, message, msgtype=msgtype
            )
        except Exception as e:
            error_message = (
                f"Failed to send message '{message}' to room {mautrix_room_id}: {e}"
            )
            self.log.error(error_message)
            return json_response(
                {
                    "status": "failed to send message",
                    "error_message": error_message,
                },
                status=500,
            )

        return json_response({"status": "ok"})

    def handle_issue_updated(self, payload: Dict[str, Any]) -> str | None:
        """
        Build a Markdown-formatted summary for an updated Plane issue.

        Applies configuration to decide whether to skip notifications based on:
        - Empty assignee list.
        - Actor being the sole assignee.
        - Special case where a non-actor sole assignee was just removed.
        """
        assignee_ids: list[str] = get_assignee_name_list_from_payload(payload)
        assignees_empty: bool = len(assignee_ids) == 0

        actor_is_sole_assignee: bool = is_actor_sole_assignee(payload)
        non_actor_sole_assignee_removed: bool = was_non_actor_sole_assignee_removed(
            payload
        )

        send_when_no_assignees: bool = bool(
            self.config.get("send_notification_with_no_assignees", False)
        )
        send_when_actor_is_sole_assignee: bool = bool(
            self.config.get("send_notification_when_actor_is_sole_assignee", False)
        )

        # 1) Empty assignees:
        #    - Normally follow `send_notification_with_no_assignees`.
        #    - But if we just cleared a non-actor sole assignee, always allow the
        #      notification (override the "empty" suppression).
        if (
            assignees_empty
            and not non_actor_sole_assignee_removed
            and not send_when_no_assignees
        ):
            self.log.info(
                "Assignee list is empty and send_notification_with_no_assignees is disabled; "
                "no non-actor sole assignee was removed. Skipping notification."
            )
            return None

        # 2) Actor is sole assignee:
        #    - If disabled, skip; they already know what they did, and no one else needs pinged.
        if actor_is_sole_assignee and not send_when_actor_is_sole_assignee:
            self.log.info(
                "Actor is the sole assignee and send_notification_when_actor_is_sole_assignee "
                "is disabled. Skipping notification."
            )
            return None

        # 3) Field filtering
        field_changed_raw = get_activity_value_from_payload(payload, "field") or ""
        field_changed: str = field_changed_raw.strip().lower()

        issue_updated_notification_fields: list[str] = self.config.get(
            "issue_updated_notification_fields",
            [],
        )
        if field_changed not in issue_updated_notification_fields:
            self.log.info(
                f"Updated field `{field_changed}` is not in issue_updated_notification_fields. "
                f"Skipping notification."
            )
            return None

        self.log.info(
            "Issue updated event matches configuration. Building notification."
        )

        issue_title: str = get_data_value_from_payload(payload, "name") or "Untitled"
        actor_name: str = (
            get_actor_value_from_payload(payload, "display_name") or "Unknown user"
        )
        raw_field_changed: str = (
            get_activity_value_from_payload(payload, "field") or "Unknown field"
        )
        display_field_changed: str = FIELD_CHANGED_RENAMING.get(
            raw_field_changed,
            raw_field_changed,
        )
        old_value = get_activity_value_from_payload(payload, "old_value") or "None"
        new_value = get_activity_value_from_payload(payload, "new_value") or "None"

        issue_url: str = generate_issue_url(payload, self.config["workspace_url"])

        return (
            f"Task: **[{issue_title}]({issue_url})** — **{display_field_changed}** updated by **{actor_name}**\n\n"
            f"- **New:** `{new_value}`\n"
            f"- **Old:** `{old_value}`\n"
        )

    def handle_issue_created(self, payload: Dict[str, Any]) -> str:
        """
        Build a Markdown-formatted summary for a newly created Plane issue.

        The message includes:
        - Issue title
        - Actor (creator)
        - Priority
        - Due date
        - Assignee list
        """
        issue_title: str = get_data_value_from_payload(payload, "name") or "Untitled"
        actor_name: str = (
            get_actor_value_from_payload(payload, "display_name") or "Unknown user"
        )
        priority: str = get_data_value_from_payload(payload, "priority") or "None"
        target_date: str = get_data_value_from_payload(payload, "target_date") or "None"

        assignee_names: List[str] = get_assignee_name_list_from_payload(payload)
        assignee_display: str = (
            ", ".join(assignee_names) if assignee_names else "Unassigned"
        )

        issue_url: str = generate_issue_url(payload, self.config["workspace_url"])

        return (
            f"**New task created** by **{actor_name}**\n\n"
            f"- **Title & Link:** [{issue_title}]({issue_url})\n"
            f"- **Priority:** {priority}\n"
            f"- **Due date:** {target_date}\n"
            f"- **Assignees:** {assignee_display}\n"
        )
    
    def handle_issue_comment(self, payload: Dict[str, Any]) -> str:
        comment_url: str = generate_comment_url(payload, self.config["workspace_url"])
        return f"[Comment event]({comment_url})"
