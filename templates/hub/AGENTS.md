# Agent Instructions

Read `README.md` and `ORCHESTRATION.md` before changing plan state. Resolve IDs with `planctl show`, synchronize with the private remote before reading or writing, claim ready work before implementation, validate every state change, and commit only related files. Never force-push a claim. After a rejected push, fetch and re-evaluate current readiness and claim state instead of retrying blindly.
