# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- Spec: `.scratch/<feature-slug>/spec.md`.
- Implementation tickets: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Record triage with a `Status:` line near the top of each issue.
- Append conversation under `## Comments`.

When a skill says “publish,” create a file under `.scratch/<feature-slug>/`.
When it says “fetch a ticket,” read the referenced file.

## Wayfinding

- Map: `.scratch/<effort>/map.md`; child tickets: `.scratch/<effort>/issues/<NN>-<slug>.md`.
- A child carries `Type:` and `Status:`; dependencies use `Blocked by: NN, NN`.
- The frontier is the first open, unblocked, unclaimed child by number.
- Claim by setting `Status: claimed`. Resolve by recording `## Answer`,
  setting `Status: resolved`, and adding a pointer to the map’s decisions.
