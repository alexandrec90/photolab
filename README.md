# Photolab

A photo album you can audit. It annotates each photo once, so an LLM never has to look
at it again, and it makes realistic new photos from your own, with your subscriptions'
image model or with compositing that keeps every real pixel.

Generated from [devkit](https://github.com/alexandrec90/devkit)'s project
template. The agent harness in `scripts/hooks/` is vendored from there — see
`CLAUDE.md`, "Vendored agent harness".

## Quick start

```bash
cp .env.example .env              # set PHOTO_LIBRARY_HOST_DIR to your photos
```

Then run **Photolab: Open Album** from VS Code's task picker. It starts Postgres in Docker
and the rest on this machine, and opens <http://localhost:5180>; press **Scan library** and
audit from there. Close the task's terminal to stop it. Outside VS Code,
`python scripts/album.py` is the same thing, and Ctrl+C stops it.

Nothing here writes to your photo folder: everything derived lives in
`PHOTOLAB_DATA_HOST_DIR`. It binds to loopback only, because the API has no login.

**Why not all in Docker:** the `codex` and `claude` commands, and their logins, are on this
machine. `docker compose --profile full up -d` still runs the whole stack, with the photo
folder mounted read-only, but with no AI images and only `ANTHROPIC_API_KEY` to annotate with.
A plain `docker compose up -d`, which is what **Docker: Start Stack** runs, starts only
Postgres and Redis, so that it cannot take the album's ports.

## What it does

**Annotate once.** Each photo is keyed by the SHA-256 of its bytes, so renaming, moving or
re-importing one never costs another LLM call. **Annotate** sends a downscaled copy with no
EXIF or GPS to the backend you choose. The model describes people but never identifies them;
you add names in the album, and they survive re-annotation.

| `ANNOTATION_BACKEND` | Paid by | Default model |
| --- | --- | --- |
| `codex` (the default) | your ChatGPT plan | `gpt-6-luna` |
| `claude` | your Claude plan | `haiku` |
| `api` | `ANTHROPIC_API_KEY`, per token | `claude-haiku-5-5` |

If the configured one is not here, the next that is gets used. **Try 20** annotates a sample,
and any run can name another backend, so several can be audited side by side before
committing a library. Each annotation records what made it:

```bash
python -m photolab annotate --limit 20 --backend claude --model sonnet
```

Hitting a plan's limit stops a run without marking any photo, so the next run carries on
from where it stopped.

**Read the catalog instead of the photos.** One tab-separated line per photo, around 60
tokens each against roughly 2,000 for the image:

```bash
python -m photolab catalog beach
curl localhost:8007/api/catalog?q=beach
```

Lines are marked `verified` once you have approved them. Rejected annotations are left out
and redone on the next run.

**Audit.** The album keeps what the model said beside what you changed. Open a photo, then
`A` approves and `R` rejects and moves to the next; arrows step, `Esc` closes. Rejecting an
output keeps it, and its row records exactly how it was made.

**Make new photos.** Open any photo for one-click recipes. **Compose** puts a person from
one photo into a scene from another, or a Pexels stock photo with `PEXELS_API_KEY`.
Annotations warn about mismatches colour cannot fix, such as light from opposite sides.

**Imagine** describes a photo in words. **Find photos in the album** reads the catalog, not
the images, to choose references; each one goes to the model with what the album knows about
it, including who it shows. **Relight with AI**, on any photo, keeps the picture and redoes
only the light and the subject's contact with the ground, which is what compositing cannot
do. Both are recorded like any other output, and open for the same audit.

### Three ways to put someone on a beach

Measured on one sample photo of a person and one of a beach:

| Route | Light and shadow | The person | Time |
| --- | --- | --- | --- |
| **Compose** | their original light, a soft fake shadow | their real pixels | 26s, on the CPU |
| **Compose, then Relight with AI** | believable, real cast shadow | small and in place; redrawn | 40s more |
| **Imagine** with both photos | believable | a close likeness, redrawn; face drifts | 60s |

So: **Compose then Relight** is the default for anyone you would recognise, and **Imagine** for
scenes with no one you know, or where a likeness will do. An AI output comes back at around
1450×1080. Codex decides the image model (`gpt-image-2` at the time of writing).

`photolab/compositing.py` removes the specific tells of a cut-out: edge colour from the old
background, colour cast, too much sharpness, missing grain, and no contact with the ground.
What it cannot do is **change the light on the subject**, so a badly matched pair looks
pasted whatever the sliders say until it is relit.

### What the subscriptions cost, and see

- **Limits, not money.** Image generation counts towards the ChatGPT plan's Codex limits, and
  [uses them 3-5x faster](https://learn.chatgpt.com/codex/pricing.md) than an ordinary turn.
  The page gives no per-image number, so watch the usage dashboard. Each Codex annotation
  also carries about 15,000 tokens of Codex's own instructions, whatever the photo, which is
  why it uses the cheapest model. A large library will take several limit windows.
- **Your photos go to OpenAI or Anthropic** under the consumer terms of your plan, not the API
  terms. That is a choice to check: in ChatGPT turn off **Improve the model for everyone** in
  [Data Controls](https://help.openai.com/en/articles/7730893-data-controls-faq), and check
  the equivalent setting in Claude. Nothing is sent until you press something.
- **Every call is locked down**, because these are agents and a photo can contain text: no
  shell, plugins or MCP servers, a read-only sandbox, a throwaway folder, and none of your own
  Codex or Claude configuration. `photolab/agents.py` has the exact flags.

### Matting

The cut-out runs on your CPU and is cached per photo, so only the first composite of a
person pays for it. Measured on a 4-core i7-6700 with one 1920x1280 photo:

| `MATTING_MODEL` | Time | Peak memory | Edges |
| --- | --- | --- | --- |
| `isnet-general-use` (default) | 26s | 1.0 GB | good, rough on fine detail |
| `birefnet-portrait` | 115s | 5.1 GB | visibly cleaner, especially hair |

Run with `scripts/album.py`, `birefnet-portrait` has the machine's memory. In Docker it is
killed in a default Desktop VM: give Docker at least 7 GB and set `APP_MEM_LIMIT=6g`.

## Development

```bash
uv sync --all-extras          # creates .venv from the committed uv.lock
npm --prefix frontend install
python scripts/run-tests.py   # the tests for what changed
```

The suite needs Postgres: `docker compose up -d db`. It works in a schema of its own that
it drops afterwards, so running it never touches the album.

In VS Code, `Ctrl+Shift+B` runs the default build task and the task quick-pick
(`Ctrl+Shift+P` → "Run Task") lists everything else, each with a one-line `detail`
explaining what it costs and what it touches.

## Host ports

This checkout is **slot 7** in devkit's `ports.toml`. Every published port
is its conventional base plus the slot:

| Service | Host port |
| --- | --- |
| app | 8007 |
| postgres | 5439 |
| redis | 6386 |
| vite | 5180 |

Regenerate the `.env` block for any checkout with
`python <devkit>/scripts/devkit_ports.py <checkout-name>`.

## Layout

```text
photolab/                  application code
frontend/                the album (React + Vite)
tests/                tests
scripts/album.py         runs the album on this machine
scripts/                 project scripts (Python, each with tests)
scripts/hooks/           vendored agent harness — edit upstream in devkit
.devkit.toml      the per-project harness seam (NOT vendored)
docker-compose.yml       the local stack
```

## CI

`.github/workflows/pr-gate.yml` runs lint, tests, and the harness drift check on
every PR. The drift check is only meaningful when it can see devkit — if it prints
"nothing to do (skipping)", the gate is inert and the wiring needs fixing. Locally,
where `DEVKIT_DIR` is usually unset, `pre-commit run devkit-drift --all-files` is the
same check against the devkit rev pinned in `.pre-commit-config.yaml`.

`.github/dependabot.yml` opens weekly dependency PRs, and
`.github/workflows/dependabot-automerge.yml` merges them once the gate passes —
patch/minor bumps of anything, plus majors confined to dev tooling. A major that
touches a runtime dependency is labelled `needs-manual-merge` and waits for you.

That auto-merge workflow is **vendored** from devkit, so `sync-devkit.py --check`
compares it byte-for-byte and editing it here shows up as drift. Keep the gate's title
`PR Gate`: the auto-merge workflow waits on it by name, and a rename makes the merge job
inert without turning anything red.

`.github/actions/setup-python-env/` is **yours**, by contrast. devkit shipped it through
that same channel for one release and pulled it back out when two projects turned out to
install in ways `uv sync` cannot express, so it now arrives from `templates/` as a
one-shot copy: nothing compares it, and rewriting its steps to match how this project
installs is the supported move rather than drift. What owning it costs is the other
direction — later devkit fixes to that file never reach here.
