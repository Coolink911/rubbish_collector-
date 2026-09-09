# Side quest — NOT DONE YET

<!-- COLLINS: this one cannot be delegated. The brief: ~45 minutes hands-on
     with a tool/model/library you did NOT use this week, then half a page
     on whether you'd switch and why. Half a page means half a page.
     Do it while the build is in flight (the brief says so) - Friday slot. -->

## Chosen tool: Ollama (suggested — replace if you prefer)

Why this one: it's already installed on this machine (listening on :11434),
it's the free-only answer to "what if the parser had no API key at all", and
it's a genuine either/or against the Anthropic API used in `app/parse.py`.

## The 45 minutes, structured

1. (10 min) Pull a small model. `ollama pull llama3.2` or similar.
2. (25 min) Point `parse._request` at it — Ollama speaks an HTTP API on
   localhost — and feed it the same test sentences the suite uses, plus the
   garbage ones. Note: does it return clean JSON? How often? How slow, cold
   and warm?
3. (10 min) Write the half page below. Delete these instructions.

## Would I switch? (half a page, yours)

- What it did better:
- What it did worse:
- What surprised me:
- Verdict for THIS app (a free product should arguably not depend on a paid
  key — does the quality hold up enough to matter?):
