# Contributing

## Branches

Same model as [echo](third_party/echo/CONTRIBUTING.md):

| Branch | Purpose |
|---|---|
| `main` | Released, tagged versions only (`v0.1.0`, ...) |
| `development` | Integration branch. Everything merges here first |
| `feature/<area>/integration` | Integration branch for a multi-part feature, e.g. `feature/voice-server/integration` |
| `feature/<area>/<part>` | One piece of that feature, e.g. `feature/voice-server/tts`, merged into its integration branch |
| `feature/<name>` | A single-part feature, merged straight into `development` |
| `chore/<name>`, `docs/<name>`, `fix/<name>` | Tooling, documentation, bug fixes |
| `release/<version>` | Final checks before merging into `main` |

Merge with `--no-ff` so every feature stays visible as a unit in the history.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat(voice): ...`,
`fix(client): ...`, `build(docker): ...`, `test(brain): ...`, `docs: ...`,
`chore(scripts): ...`. Keep each commit small and working, and explain *why*
in the body.

## Before you push

```bash
make test        # Python unit + integration tests (server and client)
make lint        # shellcheck + ruff, if installed
```

`third_party/echo` is a `git subtree` of the echo repository. Update it with:

```bash
git subtree pull --prefix third_party/echo ../echo main --squash
```
