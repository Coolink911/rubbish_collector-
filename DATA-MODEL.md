# Data model - working notes

Day 1. Answer these before writing any schema. Prose is fine; this is thinking,
not documentation. It gets tidied into ARCHITECTURE.md on Sunday.

## 1. What tables exist?

You need people and pickups at minimum.

- Is a collector a different table from a resident, or the same table with a
  `role` column? What does each choice cost you later?
- Does a claim live as columns *on* the pickup row, or in its own `claims`
  table? Both work. Write down how each one fails.

## 2. What are the legal statuses of a pickup?

List them. Then draw the arrows between them - which transitions are allowed,
and which are nonsense?

    open ──▶ ? ──▶ ?
      │
      ▼
      ?

Which transitions can a *resident* trigger? Which can a *collector* trigger?

## 3. What changes when a pickup is claimed?

Name every column that moves, and what it moves from and to.

## 4. The one that matters

**What stops a pickup ending up with two collectors?**

Write down your first instinct now, even if you think it's wrong. Then answer
this: if your instinct is "the code checks whether it's still open before
assigning it" - what happens when two requests run that check at the same
instant, before either of them has written anything?

Don't solve it today. Thursday is for it. But the gap between what you write
here and what you end up building is a LOG.md entry, and you can't reconstruct
it later.

## Notes to self
