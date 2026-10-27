# Project guidance

- Do simple tasks directly; plan complex tasks before implementation.
- Use a skill when it is clearly useful to the task.
- Read, change, and verify only the scope needed for the task.
- Do not expand requirements, refactor unrelated code, or repeat checks without a reason.
- Verify important uncertain facts against primary evidence.
- Report the result concisely.

For cross-workspace continuity, start with [`docs/HANDOFF.md`](docs/HANDOFF.md)
and [`docs/WORKSPACE-MIGRATION.md`](docs/WORKSPACE-MIGRATION.md). Never open
private PLAN/VERIFICATION notes or credential stores. Do not expose real keys,
subscription details, balances, task text, screenshots, or runtime backups in
source, tool output, logs, handoff documents, or external services. For an
explicitly requested local migration/retention check, operate only on its
allowlisted files; verify using counts, schema, metadata or hashes and keep file
contents out of output. User state may be copied only between approved local
state locations by the requested migration; never put it in the source repo.
