<!--
Prompt template for the failure agent's LLM summarization step.

Placeholders (Python str.format style) filled in by failure_agent.py:
  {charm_name}       - configured charm-name
  {hook}             - failing hook/action name
  {classification}   - "not-implemented" | "error"
  {substrate}        - "machine" | "kubernetes" | "unknown"
  {applied_tag}      - currently applied release tag (or "none")
  {exception_type}   - exception class name
  {exception_message}- exception message
  {traceback}        - full traceback text
  {recent_hooks}     - JSON list of recent hook-run entries
-->
A hook in the InfiniCharms charm `{charm_name}` failed. Analyze the failure and
produce an issue for the monorepo, following the output contract in your SOUL.

## Failure classification hint (heuristic — you may override)

A cheap type-based guess: `{classification}`. Do **not** trust it blindly.
Reason about the traceback and hook below: if the charm hit an event or data
path its author never implemented (e.g. an unobserved relation/config/pebble
event surfacing as `AttributeError`/`KeyError`), classify it as
`not-implemented` even when the guess says `error`. Put your final verdict in
the `classification` field of your JSON reply.

## Context

- charm-name: `{charm_name}`
- hook: `{hook}`
- substrate: `{substrate}`
- applied release tag: `{applied_tag}`
- exception type: `{exception_type}`
- exception message: `{exception_message}`

## Traceback

```
{traceback}
```

## Recent hook runs (newest last)

```json
{recent_hooks}
```

Reply with ONLY the JSON object described in your SOUL (title, body, severity,
classification).
