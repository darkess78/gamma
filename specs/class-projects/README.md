# Gamma Class Projects

Status: Current planning sources
Last verified: 2026-06-29

This directory contains maintained specifications for class projects that may
eventually integrate with Gamma. These projects begin as offline research and
must not change Gamma's production runtime until their datasets, evaluations,
and integration boundaries have been reviewed.

## Projects

- [CS 4700: Gamma Memory Triage](cs4700-memory-triage.md)
- [CS 4710: Gamma-Assisted OSRS Screen Recognition](cs4710-osrs-screen-recognition.md)

## Shared Rules

- **Confirmed:** Gamma runtime source remains under `src/gamma/`; offline class
  work lives under `research/`.
- **Working decision:** Experimental dependencies must remain outside Gamma's
  primary install unless a later reviewed integration explicitly adds an
  optional extra.
- **Working decision:** Datasets must be documented, privacy-reviewed, and
  separated into leakage-safe evaluation groups before model training.
- **Working decision:** No model result may be described as complete without a
  reproducible evaluation on an untouched test set.
- **Future work / intentionally out of scope:** Live endpoints, automatic
  filtering, automatic screenshot capture, and production model loading are
  not part of the initial scaffolds.

The operational offline folders are:

- `research/cs4700_memory_triage/`
- `research/cs4710_osrs_screen_recognition/`
