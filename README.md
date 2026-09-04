<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* charmcraft.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# infinicharms-base

Charmhub package name: infinicharms-base
More information: https://charmhub.io/infinicharms-base

The **InfiniCharms base (template) charm**: a workload-less,
infrastructure-agnostic, self-healing seed charm. The same single `.charm`
artifact deploys on both machine and Kubernetes clouds (no `containers:` block).

It bakes in three capabilities:

1. **Failure agent** — on *any* hook failure, `src/charm.py` wraps `ops.main()`,
   collects diagnostics, summarizes the failure with an LLM (any OpenAI-compatible
   provider, with a templated fallback), and files or updates a GitHub issue on
   the monorepo. It
   de-duplicates by failure fingerprint. See `SOUL.md` for the agent's persona
   and `prompts/` for the templates.
2. **Hot-patch / self-update (Option A)** — on `config-changed`/`update-status`,
   fetch the latest matching release for this charm from the monorepo
   (`<charm-name>/vX.Y.Z`), unpack it, and swap in the new code under
   `$JUJU_CHARM_DIR`.
3. **Hook monitoring** — records every hook run and status to
   `.infinicharms/hooks.log` for context.

See `PLAN.md` for the full design and rationale.

## Configuration

| Option | Type | Purpose |
|--------|------|---------|
| `monorepo` | string | `owner/repo` of the monorepo (issues + releases). |
| `charm-name` | string | Subdir/charm name; scopes releases and labels. |
| `github-token` | string | Fine-grained token for filing issues via `gh`. |
| `llm-api-token` | string | API token for LLM summarization (optional). |
| `llm-model` | string | Model in the provider's naming (default `z-ai/glm-5.3-flash`). |
| `llm-base-url` | string | OpenAI-compatible endpoint (default OpenRouter). |
| `auto-update` | boolean | Enable the self-update loop (default `true`). |
| `log-level` | string | Charm log level. |

### Choosing an LLM provider / model

The failure agent talks to any **OpenAI-compatible** endpoint via
[Pydantic AI](https://pydantic.dev/docs/ai/overview/), so you can use OpenRouter
(default), OpenAI, or a self-hosted gateway just by changing `llm-base-url` and
`llm-model`. If `llm-api-token` is unset, the agent skips the LLM and files a
templated issue instead.

```bash
# OpenRouter (default) — access many models behind one key
juju config <app> \
  llm-api-token="sk-or-..." \
  llm-model="z-ai/glm-5.3-flash"        # llm-base-url defaults to OpenRouter

# OpenAI directly
juju config <app> \
  llm-base-url="https://api.openai.com/v1" \
  llm-api-token="sk-..." \
  llm-model="gpt-4o-mini"

# Any other OpenAI-compatible gateway (self-hosted, Azure-compatible proxy, etc.)
juju config <app> \
  llm-base-url="https://your-gateway.example/v1" \
  llm-api-token="<token>" \
  llm-model="<model-id>"
```

`llm-model` uses **the selected provider's** model naming (e.g. OpenRouter
namespaces like `z-ai/glm-5.3-flash`, or OpenAI names like `gpt-4o-mini`).

## Actions

| Action | Purpose |
|--------|---------|
| `agent-status` | **Read-only.** Report the failure agent's most recent outcome (`filed` / `commented` / `skipped` / `failed`, with the issue number or error), the last hook failure it saw, and the fingerprint→issue ledger. Reads `.infinicharms/state.json`; makes no changes. |

Everything else is automatic: the failure agent files issues on any hook failure
and the updater self-patches on `config-changed`/`update-status`, so operators
don't normally trigger anything manually. See `DEBUGGING.md` for how to use
`agent-status` to diagnose the agent itself.

## Demo: failure agent files a `not-implemented` issue from an unexpected error

This walkthrough shows the end-to-end self-healing loop: a charm built on this
base **forgets to fully implement a relation**, an *unexpected* exception escapes
a hook, and the failure agent reasons about it and files a GitHub issue on the
monorepo — **without anyone raising `NotImplementedFeature`**.

To reproduce it, add a deliberately half-implemented PostgreSQL relation *on top
of the base* (this mimics a downstream author's mistake — the base itself ships
no such relation). Its `database-relation-changed` handler reaches straight for
the provider's `endpoints` field without first negotiating a database name, so
the key is missing and it raises a plain `KeyError`.

### Prerequisites

- A Juju controller (machine or Kubernetes) and a bootstrapped model.
- A GitHub **fine-grained token** with `issues:write` (and label-create
  permission) on your monorepo.
- Optionally, an LLM API token (OpenRouter by default). Without it, the agent
  still files a *templated* issue.

### Steps

First, add the demo relation. In `charmcraft.yaml`, declare the requirer:

```yaml
requires:
  database:
    interface: postgresql_client
    optional: true
```

In `src/charm.py`, observe it with a half-implemented handler (in `__init__`):

```python
framework.observe(
    self.on["database"].relation_changed, self._on_database_relation_changed
)
```

```python
def _on_database_relation_changed(self, event: ops.RelationChangedEvent) -> None:
    # Reaches for 'endpoints' without negotiating a database first -> KeyError,
    # an *unexpected* exception (NOT a NotImplementedFeature).
    endpoints = event.relation.data[event.app]["endpoints"]
    logger.info("connecting to postgresql at %s", endpoints)
```

Then build, deploy, and trigger the failure:

```bash
# 1. Build the charm (with the demo relation added above).
charmcraft pack

# 2. Deploy it with the required config. Use real values.
juju deploy ./infinicharms-base_amd64.charm \
  --config monorepo="<owner>/<repo>" \
  --config charm-name="boo" \
  --config github-token="<gh-fine-grained-token>" \
  --config llm-api-token="<openrouter-or-openai-token>"   # optional

# 3. Deploy a real PostgreSQL provider.
#    Kubernetes:
juju deploy postgresql-k8s --channel 14/stable --trust
#    Machine (instead of the above):
# juju deploy postgresql --channel 14/stable

# 4. Integrate — this fires `database-relation-changed` on our unit, which
#    raises KeyError('endpoints') and drives the unit into `error`.
juju integrate infinicharms-base:database postgresql-k8s:database

# 5. Watch the unit go into error on the failing hook.
juju status --relations
```

### Verify the agent did its job

```bash
# Ask the agent how it did (no juju ssh needed):
juju run infinicharms-base/0 agent-status
```

Expected results on success:

- `outcome: filed` (or `commented` on a repeat), with an `issue` number.
- `last-failure` shows `KeyError: 'endpoints'` on `database-relation-changed`.
- A new issue appears on your monorepo, labeled `charm:boo`, `type:*`
  (`type:not-implemented` when the LLM reasons it's a missing-feature gap), and
  `severity:*`. Missing labels are created automatically.

If `outcome: failed`, the `error_message` tells you why — see `DEBUGGING.md`
(common causes: wrong `monorepo`, token lacking permissions, no network egress).

### Reset / re-run

```bash
juju remove-relation infinicharms-base:database postgresql-k8s:database
juju resolved infinicharms-base/0        # clear the error state
juju integrate infinicharms-base:database postgresql-k8s:database   # trigger again
```

> The de-dup ledger means a second identical failure **comments on the existing
> open issue** instead of opening a duplicate — `agent-status` will then report
> `outcome: commented`.

## Development

```bash
tox -e format   # ruff format + ruff check --fix
tox -e lint     # codespell + ruff + pyright
tox -e unit     # unit tests (ops.testing / Scenario)
charmcraft pack # build the .charm
```

## Other resources

<!-- If your charm is documented somewhere else other than Charmhub, provide a link separately. -->

- [Read more](https://example.com)

- [Contributing](CONTRIBUTING.md) <!-- or link to other contribution documentation -->

- See the [Juju documentation](https://documentation.ubuntu.com/juju/3.6/howto/manage-charms/) for more information about developing and improving charms.
