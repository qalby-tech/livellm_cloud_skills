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

The workspace's own SSH keys are installed on every Linux machine, including
ones already running. Only the user can add or remove one — a workspace key
opens every machine they have, so an agent is refused. You can read the list:

```
python3 scripts/llc.py ssh-keys
```

For a machine you create, put your own public key in its
`credentials.sshKeys`: it reaches that machine alone, which is the right scope
for work you were asked to do. To get the user's own key onto their machines,
send them to the console's Keys page.

## Is anything wrong

```
python3 scripts/llc.py whoami          # plan and usage
```

The console's Monitoring page keeps each resource's up and down history and
warns when one runs hot on processor, memory or disk for a while. When a user
asks "is anything down", read the monitoring endpoint through the API and say
plainly what is up, what is down and since when. There are no notifications;
nobody is paged.

## Who is signed in

The console's Agents page lists every agent signed in to the workspace, with its
permissions and when it was last used, and signs any of them out in one click.
The permissions are Connect (browsers, apps, databases), Use desktops, Run
commands, Create, and Manage everything; the user turns each on or off there,
and it applies on the agent's next call. The page also shows which machines
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
