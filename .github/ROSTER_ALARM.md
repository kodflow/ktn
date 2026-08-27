The roster was re-signed with very little of its 24-hour window left, which
means scheduled signatures have been failing or skipped for most of a day.

Signing runs every 20 minutes, so a healthy roster is always replaced while
nearly full. Getting this low takes dozens of consecutive misses.

## Why this issue exists

The last outage was invisible for thirteen hours. A branch ruleset started
refusing the signing bot's push, twelve runs failed in a row, and nothing said
anything until every licensed binary refused to start. A failing cron on a
quiet repository looks exactly like a quiet repository.

## What to check

- **Recent runs of `Licence roster`** — are they failing, or not starting at
  all? The two have different causes.
- **If failing:** read the push step. A branch rule refusing the bot is what
  did it last time; licence state lives on the `licenses` branch precisely so
  that cannot recur, but a new rule could.
- **If not starting:** GitHub drops scheduled runs under load. Confirm the
  workflow is still `active` in the Actions tab and that the `schedule`
  trigger is still present in the file on the default branch.

## Recovering

`gh workflow run license-roster.yml --repo kodflow/ktn` signs immediately and
does not wait for the next slot. Close this issue by hand once scheduled runs
are green again.
