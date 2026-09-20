# Machines and desktops

A machine is a whole computer: Ubuntu for jobs and servers, Ubuntu desktop or
Windows when something needs a screen.

| Type | Use it for |
|---|---|
| `vm-ubuntu` | builds, tests, scripts, anything with a command line |
| `vm-ubuntu-desktop` | a Linux desktop a person will look at |
| `vm-windows` | Windows software |

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

## Reach it

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

A desktop machine adds a screen, and you can work on it.

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
were given — nothing is scaled — and `"screenshot": false` skips the picture
when you don't need it.

Look before you act: take a screenshot, decide from what is on it, then act,
then look again. The screen is a person's desktop, not a terminal — prefer SSH
for anything with a command line, and a browser for web work.

To let the user watch or take over, send them the machine's page in the LiveLLM
console: the screen opens there, and Windows machines also offer a remote
desktop file for their own client. `connect desk-1 --tool view` returns a
screen stream for a VNC client, not a page to open in a tab.

## Stopping and deleting

- A machine you created and no longer need: `rm ci-box --yes`. Its disk goes
  with it.
- A machine that should stay but cost less while idle: tell the user they can
  stop it in the console; a stopped machine keeps its disk.
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
  from the console or give it `"stopAfter": "off"` on a save.
- **The screen is black.** It is asleep. Send a `mouse_move` or a `key`, wait a
  moment, then take the screenshot again.
- **The screen asks for a password.** It is locked, and that is the user's to
  type. Send them the machine's page in the console rather than guessing.
- **The plan is full (402).** Stop and show usage. Suggest what could be removed
  and let the user decide.
