# Demo plan — 10 to 15 minutes

Idea first, then walkthrough, per the brief. Record with two browser windows
side by side from the start (one normal, one private) so the race needs no
setup fumbling. Have `pytest` already run once so the second run is warm.

## 0:00 – 2:00 · The idea
Municipal trucks come once a week. Bin Run covers the gaps: forgot the bin,
extra bags after a braai, a broken chair. Residents post; nearby collectors
claim. One sentence on what's new to me: **concurrent server-side state** —
everything I'd shipped before was read-only and client-side.

## 2:00 – 4:30 · Resident flow (left window)
Join as Thandi, resident. Type a sentence into the parse box — show it fill
the form (or, with no key set, show it *degrade* and say why that's a design
choice: the parser is a convenience, never a gate). Post it. Point at the
flash: geocoded, on the map. Post a second one with an unfindable address —
"behind the koppie past the blue house" — and show it still posts, marked
"not on map". A pickup with no coordinates is a real pickup.

## 4:30 – 8:30 · The claim race (both windows)
Join as two collectors. Same job board, same map, distance sort. Put both
windows on the same job. Click Claim in both as close to together as I can.
One wins; the other gets "«name» claimed this one first" — and lands back on
the board, not an error page.

Then the honest version: clicking two buttons is theatre, eight threads
through a barrier is proof. Show `tests/test_claim_race.py` and run it.
Explain the one statement that decides it:

    UPDATE pickups SET status='claimed', collector_id=?, claimed_at=?
     WHERE id=? AND status='open'

rowcount 1 you won, 0 you lost — and why the read-then-write version I wrote
on paper first is wrong (both read 'open', both write).

## 8:30 – 10:30 · Close the loop
Winner marks it collected. Resident's window: reload, status changed, can't
cancel a claimed job. Show release putting a job back.

## 10:30 – 12:30 · What it's built on
ARCHITECTURE diagram: one page. State lives in Neon because the Space's disk
is wiped on restart — kill the server live, restart it, show the claim still
standing. Two engines, and why that's the decision I'm least confident about.

## 12:30 – 15:00 · Honesty section
LOG.md highlights, fast: the AI built this and where it got things wrong —
the fabricated log, the deprecated API, the service-worker ghost of my old
app. What I cut on purpose (passwords, notifications) and why. Stop talking
at 15:00.

## Questions I should be able to answer cold
(from the coach checklist — rehearse these, don't read them)
- Browser form → row in the database: every step.
- Why zero rows means someone else won; what breaks if read and write split.
- What's in the session cookie; what could be forged and what couldn't.
- Where state lives on the deployed host and what a restart does.
- Geocoder timeout path; why a pickup with no coordinates is still valid.
- Parser returns prose: what happens, line by line.
