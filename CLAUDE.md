# Photolab

An audited photo album: annotate each photo once so an LLM need not re-read it, and make
realistic new photos from real ones. `README.md` covers what it does and why.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Database | PostgreSQL |
| ORM / Migrations | SQLAlchemy 2 (async) + Alembic |
| Cache / queue | Redis |
| Container | Docker + Docker Compose |
| Front-end | React (TSX) + Vite |
| Tests | pytest |
| Lint | ruff |

## Parallel worktrees and the dev server

Two agent sessions run at once, each in its own worktree, each running `npm run dev`.
`frontend/src/worktreePort.ts` derives a port offset from the checkout's own directory
name, so the static checkout keeps the conventional port and a worktree gets its own with
nothing configured — the only thing that works across every way a worktree is cut
(`claude --worktree`, `codex --worktree`, devkit's task, and Remote Control), because
most of them run no project code at the time. Wire it into `vite.config.ts` as that
file's docstring shows, and keep `strictPort: true`: the offset is a hash, and without
it a collision slides the second server onto a port nobody asked for and serves the
wrong branch's app.

**The file is devkit's, not this project's.** It is vendored by `sync-devkit.py` and
drift-checked like every other harness file: it arrives on the first
`python scripts/sync-devkit.py --pull` after `.devkit.toml` says `[frontend] enabled =
true`, at that section's `src`. Edit it in devkit and pull; a local edit is reported as
drift by the PR gate.

## Environment Variables

See `.env.example` for every variable. `.env` is gitignored and holds this
checkout's ports and credentials.

## Tooling

> Everything in this section needs the local Docker Desktop daemon. If it isn't
> running, make the code change and defer container/stack verification until it is
> (or to CI). Run `docker ps` first — an `npipe`/daemon error means Desktop is stopped.

### Scripts and the vendored harness

Both are covered by **`.claude/rules/engineering.md`** — script conventions (pure
importable functions, stdlib-only hooks, tests in the same change), the failure-artifact
rule, and how the `.devkit.toml` seam works. That file is vendored from
[devkit](https://github.com/alexandrec90/devkit) and drift-gated, so it is the
authority; this file does not repeat it.

### VS Code tasks

- Use `"type": "process"` so VS Code monitors the process directly — that is what
  makes the spinner stop and the exit-code icon appear reliably.
- Set `"close": false` in `presentation` so the terminal stays open for review.
- **Wrap with `notify-wrap.py`** for the completion toast; never call `notify.py`
  from inside a script. Notifications are a task-layer concern only.
- Label convention: `"Domain: Title Case Action"`, and **every task carries a
  `detail`** — that is the second line in the quick-pick, and the only place a
  one-click action can state its cost or blast radius.

### Failure artifacts (fix from a file, not from the terminal)

Any task or script whose failures an agent is expected to act on must persist the
failure to a **parseable artifact file** under `logs/`. Never rely on streamed
terminal output — it scrolls away and buries the signal. Keep the terminal to a
status line plus the artifact path; put everything needed to diagnose in the file.
Write the artifact on failure too, not just success, and overwrite per run.

### Docker subprocess calls

- **`docker compose exec` must use `-T`** — without it a pseudo-TTY is allocated and
  the subprocess handle can outlive the command, leaving the caller hung.

## Testing

**`.claude/rules/engineering.md`** is the authority: tests ship in the same commit,
every testable unit of logic is covered, regression test first, targeted runs locally
and full runs in CI.

Add this project's specifics *below* — fixtures, isolation rules, markers, what to mock
and where — but do not restate the policy above. It is vendored and drift-gated; a copy
here is a fork that will disagree with it the first time either is edited.

- **Never truncate or drop tables outside a test schema.** `DATABASE_URL` is the album's
  own database, and its annotations cost money to produce. `tests/conftest.py` gives the
  run a schema of its own; keep it that way.
- **The suite needs this checkout's own Postgres.** Most tests error with a psycopg
  `OperationalError` until `docker compose up --detach --wait db` has run in this tree;
  nothing starts it for a test run, and that is not a code failure.
- **No test reaches a network or spends a plan.** The Claude client is `FakeAnthropic`,
  Pexels is an `httpx.MockTransport`, and the `codex` and `claude` commands are `FakeCli`;
  rembg is faked at `matting.run_model`, since a real matte takes minutes and a 170 MB
  download.
- **A green suite says nothing about realism.** Anything changing `compositing.py` output
  or `ai_images.py` prompts gets looked at, on real photographs, before it is called done.
  Synthetic fixtures passed a version that blurred the subject and left them floating.
- **The Docker run hides Windows.** The app runs natively for AI work, and Docker never
  showed that uvicorn's default loop kills psycopg there, or that stopping a server orphans
  what `node` and a venv's `python` really are. Run `scripts/album.py` after changing how
  anything starts or stops.
- **Never widen what `agents.py` passes to a CLI.** The owner's own Codex config is
  `danger-full-access`, and `--ignore-user-config` is what keeps that away from anything a
  photo might say.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`. Cross-reference
this project's own scoped rules here, one line each.
