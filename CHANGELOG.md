# Changelog

Releasing: bump `metadata.version` in `.claude-plugin/marketplace.json` and in
every changed skill's `SKILL.md` together, add an entry below, then tag
`vX.Y.Z`. CI checks the skills and attaches a zip of each one to the release.
Claude Code users get the update only when the version changes.

## 1.0.0

- Checked end to end against a running LiveLLM: an agent signs in with the
  approval link, creates an app, a database, a browser and a machine, reaches
  each of them, and deletes what it created. The app's address refuses a
  request without its password, a real Playwright client drives the browser and
  is refused without a token, and the machine answers on its SSH port.

## 0.2.0

- First release you can install.
- `livellm-cloud`: sign-in with a one-click approval link, then browsers,
  machines, desktops, apps and databases, with a reference per tool and
  Playwright examples.
- Checked against the running platform: signing in, listing resources and
  connecting to a browser and a machine. Creating each kind of resource is
  described from the API's own definitions but not yet run end to end, which
  is why this is 0.2 and not 1.0.

## 0.1.0

- Repository layout, validation and release packaging.
