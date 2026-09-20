# Changelog

Releasing: bump `metadata.version` in `.claude-plugin/marketplace.json` and in
every changed skill's `SKILL.md` together, add an entry below, then tag
`vX.Y.Z`. CI checks the skills and attaches a zip of each one to the release.
Claude Code users get the update only when the version changes.

## 0.1.0 (unreleased)

- Repository layout, validation and release packaging.
- `livellm-cloud`: sign-in with a one-click approval link, then browsers,
  machines, desktops, apps and databases, with a reference per tool and
  Playwright examples.
