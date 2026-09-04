<!--
Prompt/issue guidance for `not-implemented` failures.

The failure agent uses the same failure_summary.md prompt for both classes, but
when the classification is `not-implemented` it prepends this framing so the LLM
biases toward "please implement X" rather than "something broke".
-->
This failure is a **request to implement a scaffolded feature**, not a bug. A
handler raised `NotImplementedFeature`, which means the base template stubbed out
a capability that this charm now needs.

When you write the issue:

- Frame the title as a feature request, e.g.
  `boo: implement <feature> for <hook>`.
- In the body, describe the expected **interface and behavior** of the missing
  feature as precisely as the context allows (inputs, relations, config,
  workload actions, resulting unit status).
- Suggest where in the charm implementation the feature should live.
- Severity is usually `low`–`medium` unless the missing feature blocks the unit
  from ever reaching `active`.
