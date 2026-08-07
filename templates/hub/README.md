# Private Plan Datastore

This company-internal repository is a passive Git-backed datastore for implementation plans and orchestration state. It does not require hooks, validators, or embedded client code. Install the public `plans-hub` client separately and select this checkout with `--root` or a named roots-registry entry when policy-aware scanning or lifecycle operations are needed.

Keep one lowercase kebab-case folder per project. The preferred managed filename is `<status>--<ID>--<name>.md`; preserve every existing stable ID. Arbitrary tools may add other files, but the client treats them as inactive raw input until they satisfy the managed-plan contract and are registered in `ORCHESTRATION.md`.

Durable investigation and requirements discussions belong in the separate `research/` namespace, not in a plan status or temporary `findings/` directory. The client indexes records in `RESEARCH.md` using hub-local `RES-###` IDs. Each record declares `Scope: project`, `cross-project`, or `unknown`; never infer a missing scope. Research is retained when explicitly linked to a plan, and conversion records backlinks without changing dependencies, claims, readiness, or plan ID allocation.

Never make a hub public merely by deleting its current plans: Git history can retain private content. Do not commit credentials, caches, generated artifacts, or machine-local configuration.
