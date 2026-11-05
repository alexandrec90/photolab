# TODO — Photolab

## Setup

- [ ] Fill in `.env` from `.env.example` (it is gitignored; nothing works without it)
- [ ] Confirm `python scripts/sync-devkit.py --list` shows a stamped `DEVKIT_VERSION`
- [ ] Set `DEVKIT_DIR` in CI so the drift check actually gates — a
      `--check` that prints "nothing to do (skipping)" is checking nothing
      (once `DEVKIT_VERSION` is stamped it fails instead of skipping; to check
      drift on a machine with no devkit clone, `pre-commit run devkit-drift`)
- [ ] Replace the placeholder DB password in `.env` (the committed one is a
      local-dev placeholder and is fine to keep local, but never reuse it remotely)

## Not exercised yet

These work against fakes in the suite, and have never met the real service.

- [ ] The `api` annotation backend: only with an `ANTHROPIC_API_KEY`
- [ ] Try a real stock search: set `PEXELS_API_KEY`
- [ ] What a plan's real limit message says: `agents._RUN_WIDE_MARKERS` is a guess at its
      wording. If one is missed, the run marks photos failed; `--retry-failed` recovers them

## Decide on your own photos

- [ ] Press **Try 20** with each backend and audit them, before annotating a whole library
- [ ] Turn off training in ChatGPT's Data Controls first; see "What the subscriptions cost"
- [ ] Judge how close a generated face is to you. All of this was tuned on two samples

## Next

- [ ] Paste the real face back over a generated one, so a likeness becomes the person
- [ ] Compare a pixel-preserving relighter, such as LBM (free Space, CC BY-NC weights)
- [ ] Use Message Batches for large annotation runs on the `api` backend, at half the price
- [ ] RAW files (`rawpy`) and duplicate detection beyond exact bytes

## Archive

<!-- Completed items move here. -->
