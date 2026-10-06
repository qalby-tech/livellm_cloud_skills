# The workspace

Everything the user has lives in one workspace. The sign-in belongs to that
workspace and reaches nothing else.

## What is there

```
python3 scripts/llc.py whoami   # workspace, plan, access, usage
python3 scripts/llc.py ls       # every resource, its state, who made it
```

`ls` marks each resource with who created it: this agent, or a person. You may
change and delete the ones this agent created. For anything else, ask the user,
who can do it in the console or sign the agent in with full access.

## The plan

`whoami` shows what is used against the plan's cores, memory and disk. Creating
something that doesn't fit is refused with a 402. When that happens:

1. Stop. Don't delete anything to make room.
2. Show the user what is used and what they asked for.
3. Let them choose: a bigger plan, or removing something themselves.

Stopped machines still use their disk. Kept builds count too.

## SSH keys

The workspace's own SSH keys are installed on every machine, Linux and
Windows, including ones already running. Only the user can add or remove one
— a workspace key opens every machine they have, so an agent is refused. You
can read the list:

```
python3 scripts/llc.py ssh-keys
```

For a machine you create, put your own public key in its
`credentials.sshKeys`: it reaches that machine alone, which is the right scope
for work you were asked to do. To get the user's own key onto their machines,
send them to the console's Keys page.

## Is anything wrong

```
python3 scripts/llc.py monitoring           # every resource up or down, uptime, use, open alerts
python3 scripts/llc.py monitoring box       # one machine: its system, disks, network, use over time
python3 scripts/llc.py monitoring box --range 24h
```

The platform keeps each resource's up and down history and raises an alert
when one goes down or runs hot on processor, memory or disk for a while. When
a user asks "is anything down", read `monitoring` and say plainly what is up,
what is down and since when. An alert with no `resolvedAt` is open (still a
problem); the others are the last 7 days' history. A machine's memory is what
its own system has in use (cache counts as free); a machine that doesn't
report it shows no memory and gets no memory alert. Install discs and
read-only mounts are not watched.

The workspace owner gets an email when an alert opens and another when it is
over (at most one per resource and kind of alert every 30 minutes).
`emailAlerts` in `monitoring` says whether that is on. Only the user turns it
off or on, on the console's Monitoring page (`PUT /v1/monitoring/settings`
refuses an agent sign-in); if they ask, point them there.

## What happened

```
python3 scripts/llc.py activity --limit 20             # newest first
python3 scripts/llc.py activity --object web           # one resource
python3 scripts/llc.py activity --actor platform       # what the platform did: builds, alerts
```

Creates, changes, deletes, builds, alerts and new keys are there, with who did it
(a person, a key, an agent, or the platform). `--before EVENT` pages back
from an event's id. Read it when the user asks what changed or who did
something; don't guess from `ls`.

## Templates

A template keeps a resource's settings for making more like it, never a
password, a secret value or data. An app keeps its plain `env` values and the
names of its secrets; a machine's login and a database's password are left
out. Saving an app of a Composable App (one in a stack, or made with
databases) keeps the whole app: every service, the databases they use
(engine, version, size, backups) and the links between them.

```
python3 scripts/llc.py templates
python3 scripts/llc.py template show nextcloud         # what it holds, and each secret it needs
python3 scripts/llc.py template save nextcloud --from cloud --description "Nextcloud with its database"
ADMIN_PW=... python3 scripts/llc.py template use nextcloud files --secret-env NEXTCLOUD_ADMIN_PASSWORD=ADMIN_PW --yes
python3 scripts/llc.py template use small-box box-2 --secret credentials.username=agent \
    --secret-env credentials.password=BOX_PW --yes
```

`save` needs "Manage everything". `use` makes new resources from it:

- A Composable App's template makes each service as `<new>-<service>` in
  the stack `<new>` (one service that had no stack becomes `<new>`), and its
  databases as `<new>-<database>` with passwords the platform makes, already
  linked. Addresses in `env` values that named the old ones name the new ones.
- An app's template (kind `pod`) makes one app on its own, never one in a
  Composable App.
- The secrets it left out are required. Give each with `--secret-env
  PATH=VAR` (the value from an environment variable; keeps it off the
  command line) or `--secret PATH=VALUE`: a bare name is a secret env value
  (in a Composable App, of every service that has it); otherwise the path is
  `imagePassword`, `portPasswords.<port>.<user>`, `credentials.username`,
  `credentials.password`, or `services.<service>.` and one of those.
  `--json FILE` takes the same as an object (`secretEnv`, `imagePassword`,
  `gitToken`, `portPasswords`, `credentials`, `services`) and `placement`;
  nothing else, so change other settings after with `set`.
- A template keeps where its resources ran. `--host H` or `--region R` puts
  everything it makes there instead, and `--automatic` lets LiveLLM pick
  (left out, the template's own location stays). A `placement` in the file
  must say its `strategy` (`"auto"`, `"host"` or `"region"`).
- Missing ones are refused (422) with the list of paths, and `next` spells
  the flags. Generate passwords yourself; ask the user for keys and tokens
  that belong to them. Never invent one.
- It counts toward the plan like any other create; make one only when the
  user asked.

## Who is signed in

The console's Agents page lists every agent signed in to the workspace, with its
permissions and when it was last used, and signs any of them out in one click.
The permissions are Connect (browsers, apps, databases), Use desktops, Run
commands, Create, and Manage everything; the user turns each on or off there,
and it applies on the agent's next call. Changing a browser's proxies or
moving its profile needs no permission of its own, only the user's agreement
each time (rule 10). The page also shows which machines
agents are holding, and lets the user release them. Tell the user about it when
they wonder what an agent can still do. Signing out takes effect immediately,
including for this agent.

## Ending this agent's own sign-in

```
python3 scripts/llc.py logout
```

Use it when the user says they're done, when you finish on a machine that isn't
theirs, or before handing a computer to someone else.

## Housekeeping you should offer

- Machines and browsers you created for one task, once it is finished.
- Test databases and sample apps from an experiment.
- Anything the user says they no longer need.

Offer, wait for a yes, delete, then say what is gone.
