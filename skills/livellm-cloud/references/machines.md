# Machines and desktops

A machine is a whole computer: an Ubuntu, Debian or Fedora server for jobs and
services, an Ubuntu desktop or Windows 11 when something needs a screen, and
Windows Server Core for Windows services. A Desktop App is lighter: a set of
Linux desktops that start in seconds. In the console, machines are under
Machines (New resource → Linux, Windows 11 or Windows Server) and Desktop Apps
under Apps.

| Type | Use it for |
|---|---|
| `vm-ubuntu` | a Linux server: builds, tests, scripts, anything with a command line |
| `vm-ubuntu-desktop` | an Ubuntu desktop a person will look at |
| `vm-windows` | Windows 11 software; with `"windowsEdition": "server"`, a Windows Server |
| `desktop` | a Desktop App: several quick Linux desktops, one per task |

A `vm-ubuntu` runs Ubuntu 24.04 unless you ask for another system with `"os"`:
`"debian"` (Debian 13) or `"fedora"` (Fedora 44). Those two are servers only,
and the system can't change once the machine exists. A `vm-windows` is Windows
11 Pro by default; `"windowsEdition": "server"` is Windows Server 2025 Core — a
command line, no desktop.

## Create one

`machine.json`:

```json
{
  "id": "ci-box",
  "cpus": 4,
  "memory": "8Gi",
  "storageSize": "40Gi",
  "stopAfter": "4h",
  "credentials": {
    "username": "agent",
    "password": "GENERATE-A-STRONG-ONE",
    "sshKeys": ["ssh-ed25519 AAAA… agent"]
  }
}
```

```
python3 scripts/llc.py create vm-ubuntu --json machine.json --yes
python3 scripts/llc.py wait ci-box
```

The login is set once, at creation, and can never be read back. Generate the
password, use it, and give it to the user once so they can get in too. A first
boot takes a few minutes; `wait` covers it.

**Log in with a key, not a password.** Generate a keypair, put the public half
in `credentials.sshKeys`, keep the private half where you run — never on the
machine, never in a file the user might commit. The keys the workspace's owner
added are installed alongside yours, so they can get in without asking you, and
neither set removes the other.

**`stopAfter` stops the machine when you are done with it**: `"4h"`, `"90m"`,
up to 30 days, or `"off"` for a machine meant to keep running. Stopping keeps
the disk and everything on it — nothing is deleted — and it is the honest
default for a machine made for one job. `connect` shows when it will stop.
Stopping a machine, by hand or by its own time, clears the stop time: start it
again and it keeps running until you give it a new one.

## Run commands

```
python3 scripts/llc.py exec ci-box "uname -a"
python3 scripts/llc.py exec ci-box "cd app && make test" --session job --timeout 600
```

`exec` runs one bash command as the machine's own login and answers with
`exitCode`, `stdout`, `stderr`, `durationMs` and `truncated` (each stream is cut
at 1 MiB). Commands with the same `--session` share a working folder. The
timeout is in seconds, 60 by default, 600 at most. It works on Linux machines
(Ubuntu, Debian, Fedora) and Desktop Apps, not on Windows.

It needs the Run commands permission. Without it you get a 403: ask the user to
turn it on for this agent on the console's Agents page.

This is the simplest way for you to run commands: no keys, no open port. SSH
works as well, and is what the user uses.

## Reach it over SSH

```
python3 scripts/llc.py connect ci-box
```

`ssh` carries `host` and `port` (the script reads them from the machine's
status). Log in with the key you gave it, or the password from the create step:

```
ssh -i ./id_ed25519 -p PORT agent@HOST
```

The SSH port opens a little after the machine reports ready — retry for a
minute before calling it broken.

Then work as usual: copy files with `scp`, run the job, read the output. Keep
what you run in the user's own directory, and leave the machine as you found it
unless they asked you to change it.

## Desktops

A desktop machine adds a screen, and you can work on it. Windows Server Core's
screen is a command line with a menu, not a desktop.

```
python3 scripts/llc.py connect desk-1 --tool computer
```

That answers with an address, a token and the actions it takes. Each call does
one thing and hands the screen back:

```
curl -s -X POST "$URL" -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"action": "screenshot"}'

# {"action":"screenshot","image":"<base64 PNG>","width":1920,"height":1080}
```

The actions are the ones a computer-use tool already uses, with the same
fields: `screenshot`, `zoom`, `cursor_position`, `mouse_move`, `left_click`,
`right_click`, `middle_click`, `double_click`, `triple_click`,
`left_click_drag`, `left_mouse_down`, `left_mouse_up`, `type`, `key`,
`hold_key`, `scroll`, `wait`. Coordinates are the pixels of the screenshot you
were given, and `"screenshot": false` skips the picture when you don't need it.
To keep screenshots small, connect with `--screen-width 1280` (320 to 3840) and
`--format jpeg`; clicks are then in the pixels of the smaller picture.

Look before you act: take a screenshot, decide from what is on it, then act,
then look again. The screen is a person's desktop, not a terminal: prefer `exec`
for anything with a command line, and a browser for web work.

`connect desk-1 --tool view` returns a screen stream for a VNC client, not a
page to open in a tab. To show the user a screen, make a screen link (below).

## Only one agent per machine

When you work a machine's screen, run commands on it or share its screen, it
is held for you for
10 minutes, renewed as you work. Another agent trying it gets a 409 naming you
and until when; the user is never held back. Let it go when you are done:

```
python3 scripts/llc.py release desk-1
```

Desktop Apps and browsers are never held: the user decides which agent uses
which.

## Desktop Apps

A Desktop App is a set of Linux desktops that start in seconds. `desks.json`:

```json
{ "id": "desks", "replicas": 3, "cpu": "2", "memory": "4Gi", "resolution": "1280x800" }
```

```
python3 scripts/llc.py create desktop --json desks.json --yes
python3 scripts/llc.py wait desks
python3 scripts/llc.py connect desks --tool computer --desktop 1
python3 scripts/llc.py exec desks "ls ~" --desktop 1
```

`replicas` is how many desktops, 1 to 20; each is reached by its number, from 0,
with `--desktop N` on `connect`, `exec` and `share`. Every desktop starts clean
unless `"keepFiles": true` gives each its own home folder that survives restarts
(`storageSize` sets its size). `keepFiles` is set at creation and can't be
changed later. Use the desktop the user gave you.

## Screen links

To let the user watch a screen, or take it over, make a link that opens it in
any browser:

```
python3 scripts/llc.py share desk-1              # watch only
python3 scripts/llc.py share desk-1 --control    # watch and use
python3 scripts/llc.py share desks --desktop 2
```

The answer holds `url`: give it to the user. It is shown only this once. A link
you make lasts an hour at most, whatever `--for` says; the user can make longer ones in
the console. It needs the Use desktops permission. `shares desk-1` lists the
open links, and `unshare desk-1 SHARE_ID` closes one: whoever has it open loses
the screen within a minute. Close a control link once the user is done with it.
In the console, Share screen on the machine's page makes the same links, and
Windows machines also have Download RDP for Remote Desktop.

## Backups

A machine's disk can be backed up now or on a schedule, and put back later:

```
python3 scripts/llc.py backups ci-box                 # what is kept
python3 scripts/llc.py backup ci-box --name before-upgrade
python3 scripts/llc.py restore ci-box BACKUP --yes    # stop it first
```

A backup is taken while the machine runs; `--clean` takes it with the machine
stopped (stop it first). A restore puts the disk back in place and loses
everything written since, so do it only when the user asked, with the machine
stopped. A schedule goes in the machine's settings, keeping the last `keep`
backups (a count, 1 to 100):

```json
"vm": { "backup": { "schedule": "@daily", "keep": 7 } }
```

## Stopping and deleting

- A machine you created and no longer need: `rm ci-box --yes`. Its disk goes
  with it.
- A machine that should stay but cost less while idle: `stop ci-box --yes`
  (ask first if you didn't create it); a stopped machine keeps its disk, and
  `start ci-box` runs it again.
- Never delete a machine you did not create, even if it looks unused.

## When it goes wrong

- **Still `starting`.** Big disks and Windows take longer. `wait` with a longer
  timeout, then report what the status said.
- **SSH refuses the password.** It is the one set at creation. If the user lost
  it, they can change it in the console; it can't be read back.
- **A key the user just added doesn't work.** A workspace key reaches a running
  machine in a minute or two — but only one that started with at least one key
  of its own or of the workspace's. A machine that booted with none takes its
  first key after a restart, and one whose login the platform never recorded
  needs its owner to save that once in the console. The console's Keys page
  names both after a change. This is also why the machines you create should
  carry a key from the start.
- **A key vanished from a machine.** The platform owns that file: anything
  added by hand inside the machine is removed on the next change.
- **The machine stopped by itself.** It had a stop time. Say so, and start it
  with `start <id>` (or Start on its page in the console), and give it
  `"stopAfter": "off"` on a save if it should keep running.
- **The screen is black.** It is asleep. Send a `mouse_move` or a `key`, wait a
  moment, then take the screenshot again.
- **The screen asks for a password.** It is locked, and that is the user's to
  type. Make a control link (`share ID --control`) and let them type it rather
  than guessing.
- **409, reserved by another agent.** Wait until the time the message names, or
  use another machine.
- **`exec` on Windows is refused.** Windows doesn't take commands this way; use
  its screen.
- **The plan is full (402).** Stop and show usage. Suggest what could be removed
  and let the user decide.
