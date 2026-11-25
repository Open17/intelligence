# Contributing

## Branches

| Branch | Purpose |
|---|---|
| `main` | Released, tagged versions only (`v0.1.0`, ...) |
| `development` | Integration branch. Everything merges here first |
| `feature/<area>/integration` | Integration branch for a multi-part feature, e.g. `feature/mic-to-text/integration` |
| `feature/<area>/<part>` | One piece of that feature, e.g. `feature/mic-to-text/silero-vad`, merged into its integration branch |
| `feature/<name>` | A single-part feature, merged straight into `development` |
| `chore/<name>`, `docs/<name>`, `fix/<name>` | Tooling, documentation, bug fixes |
| `release/<version>` | Final checks before merging into `main` |

Merge with `--no-ff` so every feature remains visible as a unit in the history.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):
`feat(vad): ...`, `fix(stt): ...`, `build: ...`, `test(core): ...`, `docs: ...`,
`chore(scripts): ...`. Keep each commit small and buildable, and explain
*why* in the body.

## Before you push

```bash
cmake --build --preset default
ctest --preset default
```

Format C++ with the repository's `.clang-format` (`clang-format -i <file>`).
