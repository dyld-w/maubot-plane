# maubot-plane

## Plane Webhook Quirks (Only covering Issues and some Issue Comments):
- When updating assignees for a task, the `assignees` field `old_value` and `new_value` values display only the name for the individual assignee added or removed, whereas the `assignee_ids` field includes values for the all of the assignees.
  - It would be really helpful if the `assignees` values showed all assignees, old and new, like `assignee_ids` does so I don't have to maintain an id to name mapping on my side.
- When creating a new task, the issue creation event correctly fires, but then, if there are assignees on this newly created task, the issue updated for `assignees` field webhook is triggered as well, resulting in extra notifications and a misleading `updated` notification.
- Field changes that trigger double-sending webhooks:
  - `issue_comment` `created`
  - `target_date`
  - `start_date`
  - `name`
  - `priority`
- The only way to retrieve issue title from a comment webhook is to use the ids and make an API call, which is more complicated than I'd like just to see the name of the issue a comment is associated with.
- When you edit an issue's description, both the `description` and `description_html` field webhooks trigger; however, the `old_value` and `new_value` values are HTML for both. And then sometimes, depending on the content, the `description` `old_value` and `new_value` values are `null` when `description_html` has content.
- The `description` and `name` webhooks trigger on every autosave, causing them to spam if there is any pause in typing the updated description.
- Assigning a module triggers the `issue` **`created`** action instead of `issue` `updated`. Similarly, removing a module triggers the `issue` **`deleted`** action instead of `issue` `updated`.
  - Cycle assignment and removal shows the same behavior.

## TODO: Add pipeline to build mbp file
## TODO: Add usage instructions
## TODO: Explain what it does
## TODO: refactor so it is less messy