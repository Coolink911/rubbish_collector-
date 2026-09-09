# Data model - working notes

The day-1 questions, with the answers the schema ended up giving. Kept as
process notes; the tidied version lives in ARCHITECTURE.md.

## 1. What tables exist?

`users`, `pickups`, `geocode_cache`, `schema_version`.

One `users` table with a `role` column, not two tables. Two tables would
duplicate every column and make "who is user 7" a two-table question; the
cost of one table is that a resident's lat/lng columns sit empty. Cheap.

The claim lives as columns **on the pickup row** (`collector_id`,
`claimed_at`), not in a separate `claims` table. How each fails: columns on
the row can't remember history (who claimed and released it before), and a
separate table can represent two live claims for one pickup unless you add a
partial unique index - which is exactly the bug this project exists to
prevent, so the representation that *can't express* the bug won. If claim
history ever matters, that's an events table appended on the side, not a
redesign.

## 2. Legal statuses and transitions

    open ──claim──▶ claimed ──done──▶ done
     │ ▲               │
     │ └───release─────┘
     └─cancel─▶ cancelled

Resident triggers: cancel (only while open). Collector triggers: claim,
release, done (only their own claim). Nothing leaves `done` or `cancelled`.

## 3. What changes on a claim?

Three columns, one statement: `status` open->claimed, `collector_id`
NULL->the winner, `claimed_at` NULL->now (UTC, written by the app).

## 4. What stops two collectors owning one pickup?

First instinct (written down before building, as instructed): read the
pickup, check it's open, then write the claim.

What's wrong with it: between the read and the write the world can change.
Two requests both read 'open', both pass the check, both write; the slower
write silently wins and one collector drives to a job that isn't theirs.

What was built instead: the check *is* the write -

    UPDATE pickups SET status='claimed', collector_id=?, claimed_at=?
     WHERE id=? AND status='open'

rowcount 1 = won, 0 = someone got there first. The database serialises
writers, so there is no gap for the world to change in. A CHECK constraint
(claimed/done must have a collector, open/cancelled must not) backstops any
future code that forgets the rule.

The gap between the instinct and the answer is written up in LOG.md.
