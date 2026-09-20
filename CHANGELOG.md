# Changelog

Releasing: bump `metadata.version` in `.claude-plugin/marketplace.json` and in
every changed skill's `SKILL.md` together, add an entry below, then tag
`vX.Y.Z`. CI checks the skills and attaches a zip of each one to the release.
Claude Code users get the update only when the version changes.

## 1.3.0

- Desktops can be worked on, not only watched: `connect <id> --tool computer`
  gives an address and the seventeen actions a computer-use tool already knows
  — screenshot, click, type, drag, scroll and the rest. Checked against a
  running KDE desktop.

## 1.2.0

- `logs <id>`: the last log lines of a resource with each container's state,
  restarts and resource use — what to read when something runs but misbehaves.
- Never ask the user to send a login code: send them the live view link so they
  type it themselves. Two independent judges scored the hand-over against a
  version of this task without the skill; both preferred the skill's answer, and
  both named the same failure without it — tearing down the user's own browser.

## 1.1.2

- Says that stopping a machine clears its stop time.

## 1.1.1

- Says how long a key change takes to reach a running machine: a minute or two,
  the same both ways. Checked against a running machine, adding and removing.

## 1.1.0

- SSH keys: a machine can be created with its own public keys
  (`credentials.sshKeys`), and `ssh-keys` reads the workspace keys its owner
  set. Both sets are installed together on every Ubuntu machine, including ones
  already running; neither removes the other. A machine that booted with no
  keys at all takes its first one after a restart.
- A machine can be given a stop time (`stopAfter`), so one made for a single
  job doesn't run forever. `connect` shows when it stops; stopping keeps the
  disk.

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
