# Agent Instructions

Read `README.md` and `ORCHESTRATION.md` before changing plan state. Resolve IDs with `$HOME/.agents/skills/shared-plan-storage/bin/planctl show` (the installer does not add `planctl` to `PATH`), synchronize with the private remote before reading or writing, claim ready work before implementation, validate every state change, and commit only related files. Never force-push a claim. After a rejected push, fetch and re-evaluate current readiness and claim state instead of retrying blindly.
