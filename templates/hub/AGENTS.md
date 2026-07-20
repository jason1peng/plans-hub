# Agent Instructions

Treat this repository as company-internal plan data, not policy implementation. Read `README.md` and `ORCHESTRATION.md`, synchronize with the private remote before reading or writing shared state, and preserve existing stable IDs and unrelated files.

A compatible external client may scan or maintain managed plans. When using `plans-hub`, select this checkout explicitly, resolve IDs through the client, claim ready work before implementation, validate each state change, and commit only related files. Whenever creating a plan, include its absolute filesystem path in the result or final response so it can be opened directly. Never force-push a claim; after a rejected push, fetch and re-evaluate current state rather than replaying blindly.

Raw files from other tools may remain in the datastore as inactive input. Do not silently assign IDs, change lifecycle, dependencies, or claims, delete content, or commit repair proposals without explicit review.
