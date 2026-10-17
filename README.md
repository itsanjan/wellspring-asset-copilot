# Wellspring: Asset Intelligence Copilot

A chat copilot that answers oil-field operators' plain-English questions about their wells,
correctly and with cited evidence, and shows each answer with a fitting inline visual.
Built for the FlytBase Hack Day (Hacktoberfest 2026).

The LLM decides what to ask; deterministic tools decide what is true. See
[`CONCEPT.md`](CONCEPT.md) for the problem, architecture, build steps and todos.

## Status

Early stage. The ground-truth script reproduces the expected answers for sample questions Q1-Q8.

## Quick start

```
scripts/fetch_data.sh          # downloads the organizer's sample dataset into data/
python3 -I ground_truth/ground_truth.py    # recomputes Q1-Q8 straight from the raw files
```

## Layout

| Path | What |
|---|---|
| `CONCEPT.md` | Proposal and architecture |
| `ground_truth/` | LLM-free ground-truth calculations used as tests |
| `scripts/` | Helper scripts (data fetch) |
| `data/` | Local dataset (git-ignored) |

## Data

The sample dataset belongs to the challenge organizers
([source](https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset))
and is synthetic. It is fetched, not redistributed here.

## License

[MIT](LICENSE)
