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

The charm exposes **no actions**. The failure agent files issues and the updater
self-patches automatically — on any hook failure and on
`config-changed`/`update-status` — so operators don't need to trigger anything
manually.

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
