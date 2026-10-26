# Instructions for AI agents

All project instructions are in [`CLAUDE.md`](CLAUDE.md). They apply to any AI assistant,
not only Claude. Read it before changing anything. The detailed history (German) is in
[`PLAN.md`](PLAN.md), and the user-facing setup guide is in [`README.md`](README.md).

## Collaborators: proposals only, no code changes

This rule applies to every agent working for a collaborator other than the owner
(for example ChatGPT/Codex). The owner decided it on 26.10.2026.

1. **Do not change code, tests, scripts, configuration or the existing docs.**
   You may read and run everything.
2. Write each change you want as a **proposal file**:
   `proposals/<YYYY-MM-DD>-<short-topic>.md`, one file per idea. Each proposal holds:
   - what should change and why,
   - the files and functions involved,
   - how it would be tested (the test that should fail today),
   - which rule in CLAUDE.md section 6 it touches, if any,
   - what is measured and what is only assumed.
   Code sketches inside the proposal are welcome. They are not applied.
3. Put the proposal on its own branch (`proposal/<short-topic>`) and open a pull request
   against `main` that changes **only** files under `proposals/`.
4. A Claude agent on the owner's machine reviews the proposal against CLAUDE.md. The
   owner then decides. Only after a yes is the change implemented, with its test.
5. Never commit secrets, `.env.local`, anything under `data/`, or data downloaded from
   WRDS, Compustat, CRSP or Sharadar (university and vendor licences forbid passing it on).
