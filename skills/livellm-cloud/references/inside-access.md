# Inside the workspace

Resources in one workspace don't reach each other unless the user allows it.
A new app, machine, Desktop App, database, browser or Browser API is closed to
the rest of the workspace: no other resource there can connect to it until its
`reachableFrom` names them. In the console this is **Reachable from**, on the
resource's create form and at the top of its Network settings.

Its public addresses are separate: closing a resource inside closes nothing
public, and a port's password or allowed-address list keeps working as it did.

## Ask first

Resources in a workspace can't reach each other unless the user allows it (a
Composable App counts as one resource). Before you let a resource reach
another (reachableFrom, a database link, dependsOn, or a browser put in a
Browser API), ask the user and wait for their agreement, unless you created
both or the one reached already lets the whole workspace in. Letting the whole
workspace in always needs their agreement. An API key or an agent also needs
the Network permission for this, which only a person turns on.

A public address isn't a way around this: it is reachable from the workspace
too, so rule 5 applies. Whatever drives a browser or a desktop acts from its
place: it can open what that browser or desktop may reach.

Say plainly what would reach what, and why: "web needs to reach shop-db to
read its data; may I let it in?".

## The setting

| `reachableFrom` | Who reaches it inside the workspace |
|---|---|
| `[]` | nothing (what a new resource starts with) |
| `["web", "worker"]` | those resources |
| `["*"]` | every resource in the workspace, also ones made later; `"*"` goes alone |

- A name is a resource's id, or a Composable App's name. A service's id stands
  for its whole Composable App.
- A Composable App is one resource: its services always reach each other, and
  they share one `reachableFrom`. Set it on one service and every service of
  the app takes it; different values on the services in one change are
  refused (422).
- When a resource named in the list is deleted, it drops out of the list.
- Resources made before this setting existed kept reaching each other: they
  show `["*"]`.

## Reached without the setting

Whatever the setting says, a resource is reached by:

- its own parts: its copies, a database's standby instances, the services of
  its own Composable App;
- the apps that link it as a database (`pod.databases`) or wait for it
  (`pod.dependsOn`), with their whole Composable App;
- a browser: the Browser API that drives it, on the browser's own ports. The
  Browser API needs no key inside the workspace, so whatever reaches a Browser
  API drives every browser in it.

So a link, a `dependsOn`, putting a browser in a Browser API, `--all`, and
letting something reach a Browser API all let resources in, and rule 11
applies to each. A database's admin console is never reached from inside the
workspace: use its own address.

## Set it

When creating, in the create file (every type, and each entry of
`create apps`):

```json
{ "id": "shop-db", "engine": "postgres", "reachableFrom": ["shop-web"] }
```

On a resource that exists:

```
python3 scripts/llc.py reach shop-db                          # who reaches it now, and its inside addresses
python3 scripts/llc.py reach shop-db --from shop-web,worker --yes   # exactly these
python3 scripts/llc.py reach shop-db --add worker --yes       # one more, the rest kept
python3 scripts/llc.py reach shop-db --remove worker --yes    # one fewer
python3 scripts/llc.py reach shop-db --from '*' --yes         # the whole workspace
python3 scripts/llc.py reach shop-db --none --yes             # nothing
```

`set ID --json FILE --yes` with `{"reachableFrom": [...]}` does the same. Left
out of a `set`, it stays as it is.

Narrowing or closing needs no permission, but it can break what the user runs:
ask before you close a resource you didn't make.

## What needs Network

An API key or an agent needs the Network permission to let a resource reach
another it couldn't reach before, or to let the whole workspace in. A person
in the console never does. It needs no Network when:

- this agent or key made both resources, now or earlier: an app and the
  databases made with it, or a database made first and linked next;
- the one reached already lets the whole workspace in (but setting `["*"]`
  always needs it);
- a new service joins a Composable App and takes what that app already
  reached and was reached by;
- a database restored into a new one takes what the original was reached by;
- a new browser goes into a Browser API that already drives every browser.

Network is off for every agent and every key until a person turns it on: for
an agent on the console's Agents page, for an API key on the Keys page.

## Reading `reach`

```json
{
  "id": "shop-db",
  "reachableFrom": ["shop-web"],
  "alsoFrom": [{"id": "worker", "why": "links it"}],
  "addresses": [{"host": "…", "port": 5432}]
}
```

- `reachableFrom` is the setting.
- `alsoFrom` lists who reaches it without the setting: `links it`, `waits for
  it`, `drives it` (a Browser API; `through` is what can drive the browser
  through it), `same app`.
- `addresses` are the names that answer inside the workspace, for the
  resources allowed. `connect ID` shows the same under `inside`.

## When it is refused

| Answer | Means | Do |
|---|---|---|
| 403 "This agent can't let web reach shop-db inside the workspace. A person can turn on Network for it on the Agents page." (`network_permission`) | This agent (or key, "Keys page") lacks Network | Tell the user what would reach what and why. They turn on Network, or set Reachable from themselves in the console. Never work around it |
| 403 "This agent can add services only to an app it created." | Joining someone else's Composable App | Ask the user; a person allows more on the Agents page |
| 422 `there is no resource "x" in this workspace` | A name that isn't here | `ls`; fix the name |
| 422 `"*" (the whole workspace) goes alone` | `"*"` with names | One or the other |
| 422 `… always reaches itself` | The resource, or its own app, in its own list | Leave it out |
| 422 `… is the name of Composable App …` | An id that is another app's name | Pick another id, or add it to that app |
| 422 `the services of Composable App … share one reachableFrom` | Different values on its services | Send the same value on each |
