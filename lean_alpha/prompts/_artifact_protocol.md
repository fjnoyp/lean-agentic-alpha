# Artifact protocol (all specialists)

Your **current working directory IS the cycle's artifact folder.** Write files
with bare relative names — do **NOT** prefix with `artifacts/`. Do NOT create
any subdirectories.

The exact filenames you must produce are supplied in the user-turn brief as
`target_md` and `target_json`. Use those filenames verbatim. Example:

```
target_md   = "A2_1.md"      → write to ./A2_1.md
target_json = "A2_1.json"    → write to ./A2_1.json
```

For iterations the dispatcher passes versioned names like `A2_3_v2.md` —
again, use them verbatim, no prefix.

**Output requirements per file:**

- `<target_md>` — human-readable analysis. Markdown. Tables for numeric data.
- `<target_json>` — structured rows for downstream consumption. Schema below.

## Inline citations are mandatory in `<target_md>` (load-bearing rule)

Every numeric claim, every reference to prior work, and every assumption MUST
be a clickable markdown link. The analyst clicks through these to verify your
work — a finding without a link is not auditable.

Two link patterns:

1. **Web sources** — for data you pulled via WebSearch / WebFetch:
   ```markdown
   Apple's gross margin expanded from 38.5% to 46.9% [(MacroTrends)](https://www.macrotrends.net/...).
   ```

2. **Upstream artifacts** — for synthesis-stage references to prior specialist
   output. The cycle's other artifacts are siblings in your working directory,
   so use bare relative names:
   ```markdown
   Per [A2.1 historical drivers](A2_1.md), multiple expansion explained ~57% of TSR.
   The forward driver inherits this regime ([A2.2 mechanism](A2_2.md)).
   ```

**Plain-text references are NOT acceptable.** "Per A2.1", "(A4.2 Lever 2)",
"see A3.1 macro forecasts" — none of these survive review. Every reference
must be a clickable link.

If you make a numeric claim that has no source (your own derived inference),
mark it explicitly: `gross margin will likely expand 100bp **(ASSUMED)**`.
The dispatcher's JSON validator will see this and set `assumed: true` for that
row.

## Common JSON envelope

```json
{
  "stage": "A2.1",
  "summary": "1-3 sentence headline of what you found",
  "rows": [
    {
      "label": "human-readable row name",
      "value": "the value, units included",
      "source_url": "https://... or null",
      "confidence": "high|medium|low",
      "assumed": false,
      "extras": { "stage-specific": "fields here" }
    }
  ]
}
```

`assumed: true` if `source_url` is null OR if the value could not be triangulated
to >=3 sources. The dispatcher will compute the cited-cell percentage from this
JSON, so honest labelling matters more than coverage.

## Reading prior artifacts

The `prior_artifact_paths` field of the brief lists relative paths to artifacts
from earlier stages. Use the `Read` tool on each before forming your output.
Do NOT re-search information that already exists in a prior artifact — read it.

## Sandbox

You may only Read/Write files inside the working directory. Do not attempt to
read or write elsewhere; permission will be denied.
