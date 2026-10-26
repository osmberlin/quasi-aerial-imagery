# Working rules

These rules apply to everyone working in this repository, human or agent.

## Record decisions and results, always

Anything not written down is lost.

- **A decision** (an approach chosen or dropped, a default changed, a tool picked) gets a row in
  [docs/log/DECISIONS.md](docs/log/DECISIONS.md) in the same change that implements it. Never rewrite
  an old row: add a new one and mark the old one `superseded by D-xxx`.
- **A run that produces a number or a judgement** gets a row in [docs/log/RESULTS.md](docs/log/RESULTS.md),
  including failed and crashed runs. State how the number was obtained.
- When the state of the project changes, update [docs/learnings.md](docs/learnings.md) (current knowledge
  only, rewritten, not appended to) and, for a dead end, [docs/failed-explorations.md](docs/failed-explorations.md).
- The logs are public. Write them so they can stay public (see "Public repository" below).

## Evidence

- Say what was measured and what was only assumed. "Tests pass" and "looks better" are not results.
  A claim of success needs the command, the artefact and the number.
- Internal consistency (frames agree, GPS residuals are small) is not accuracy. Accuracy means
  comparison with something outside the pipeline: a tape measure, surveyed lines, an official orthophoto.
- Do not loosen a test threshold to make a test pass without recording it as a decision.
- Show results the owner can open (a GeoTIFF on a map, the viewer), not descriptions.

## Data

- No imagery, reconstructions, model weights or videos in git. They live outside the repository in
  `<workspace>/imagery/` and `<workspace>/third-party/`. Frames carry GPS positions and show people and
  number plates.
- Never write run outputs to a path shared between datasets. Each scene writes below its own folder.
- Do not delete anything under `imagery/` without asking. Reconstructions take hours to redo and the
  source frames may have no other copy.
- Heavy jobs (COLMAP, segmentation, renders) run under `pipelines/common/guarded_run.sh`, one at a time.
  Check for other running jobs first (`ps -ww -eo pid,etime,command | grep -E "colmap|python"`).

## Public repository

Everything in git is public. Before every commit, run

```bash
python3 tools/check_public.py
```

It fails on images and other binary data, files over 5 MB, local absolute paths, account names and
e-mail domains, private tile servers and tokens. Secrets stay in `<workspace>/secrets.env`, outside the
repository.

Internal material (owner notes, private experiment notes, blog drafts, chats, the scene list of the
viewer) stays on disk through `.gitignore` files placed at the branch of the tree they concern, not at
the root and not in every leaf: `docs/internal/` is ignored by [docs/.gitignore](docs/.gitignore),
environments and data files by [pipelines/.gitignore](pipelines/.gitignore). Public documents do not
link into `docs/internal/`.

## Layout

See [README.md](README.md) and [pipelines/README.md](pipelines/README.md). One folder per pipeline step,
each with its own README; link between READMEs instead of repeating them.

## Delegation

The expensive model plans, reviews and debugs. Reading, searching, mechanical edits and checks go to
cheaper models (sub-agents), and their reports are reviewed before anything is recorded as fact.

## Language

Documents and code comments are in English. The viewer's interface is in German.
