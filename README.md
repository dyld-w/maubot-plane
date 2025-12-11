# maubot-plane

# Plane Webhook Quirks:
- When updating assignees for a task, the `assignees` field `old_value` and `new_value` values display only the name for the individual assignee added or removed, whereas the `assignee_ids` field includes values for the all of the assignees.
  - It would be really helpful if the `assignees` values showed all assignees, old and new, like `assignee_ids` does so I don't have to maintain an id to name mapping on my side.
- When creating a new task, the issue creation event correctly fires, but then, if there are assignees on this newly created task, the issue updated for `assignees` field webhook is triggered as well.
  - This results in excessive notifications and is frustrating to filter through.
- Changes to the due date and start date double-trigger the corresponding webhook event.
- The comment created webhook double-triggers as well.