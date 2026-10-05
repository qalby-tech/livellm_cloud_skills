# Inside the workspace

Resources in one workspace don't reach each other unless the user allows it.
An app, machine, Desktop App, browser or Browser API is closed to the rest of
the workspace, new or made before this setting existed: no other resource there
can connect to it until its `reachableFrom` names them. In the console this is
**Reachable from**, on the resource's create form and at the top of its Network
settings.

A database has no `reachableFrom`: it is reached only by what links it, an
app, a machine or a Desktop App (see "A database: link it").

Its public addresses are separate: closing a resource inside closes nothing
public, and a port's password or allowed-address list keeps working as it did.

## Ask first

Resources in a workspace can't reach each other unless the user allows it. A
Composable App counts as one resource, and a database is reached only by what
links it. Before you let a resource reach another (reachableFrom, a database
link, dependsOn, a service added to a Composable App, or a browser put in a
Browser API), ask the user and wait for their agreement, unless you created
both or the one reached already lets the whole workspace in. Letting the whole
workspace in always needs their agreement. An API key or an agent also needs
the Network permission for this, which only a person turns on.

A public address isn't a way around this: it is reachable from the workspace
too. Giving a resource a public address so that another resource here can
reach it is letting it in: ask the user first (rule 11), not only rule 5. Whatever drives a browser or a desktop acts from its
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
- Resources made before this setting existed are closed too: the platform
  gave them `[]`. What should still reach one must be let in again, with the
  user's agreement.
- Not on a database: a name or `"*"` there is refused (422), and `[]` or
  `null` is dropped. Link the database from what uses it instead.

## Reached without the setting

Whatever the setting says, a resource is reached by:

- its own parts: its copies, a database's standby instances, the services of
  its own Composable App;
- the apps that link it as a database (`pod.databases`) or wait for it
  (`pod.dependsOn`), or for any service of its Composable App, with their
  whole Composable App (`via` names the service that links);
- a database: the machines and Desktop Apps that link it (`vm.databases`,
  `desktop.databases`);
- a browser: the Browser API that drives it, on the browser's own ports. The
  Browser API needs no key inside the workspace, so whatever reaches a Browser
  API drives every browser in it.

So a link, a `dependsOn`, a service added to a Composable App, putting a
browser in a Browser API, `--all`, and letting something reach a Browser API
all let resources in, and rule 11 applies to each. A service added to a
Composable App reaches, and is reached by, every service of it, and takes
what the app reaches and what reaches it. A database's admin console is never
reached from inside the workspace: use its own address.

## A database: link it

```
python3 scripts/llc.py link web shop-db --yes             # web, with its whole Composable App, reaches shop-db
python3 scripts/llc.py link runner shop-db cache --yes    # a machine or a Desktop App links the same way
python3 scripts/llc.py link web shop-db --remove --yes    # the link is gone; the note says if web still reaches it
```

- `link` makes a link with no variables: it only lets the resource reach the
  database. Nothing is added to the app, and adding or taking out such a link
  never restarts anything. Links already there are kept as they are.
- A link with variables (`env`) also hands the app the database's connection
  details and makes it wait for the database when it starts
  (`references/databases.md`, "Link it to an app"). `link --remove` takes
  such a link out too: its variables leave the app and it restarts once, so
  tell the user that when you ask.
- After `link --remove` the resource may still reach the database: through
  its own `dependsOn`, or because another service of its Composable App links
  it or waits for it (the whole app reaches it). The answer's note says so
  (`stillReaches`).
- A machine or a Desktop App takes no variables from a link (422): it only
  reaches the database. In a create file, `"databases": [{"id": "shop-db"}]`.
- At most 8 links per resource. A database something links can't be deleted
  until the link is taken out.
- `link` needs `--yes`: ask the user first (rule 11), and before taking a link
  out of something you didn't make.

## Set it

When creating, in the create file (every type, and each entry of
`create apps`):

```json
{ "id": "search", "image": "getmeili/meilisearch:v1.12", "reachableFrom": ["shop-web"] }
```

On a resource that exists:

```
python3 scripts/llc.py reach search                          # who reaches it now, and its inside addresses
python3 scripts/llc.py reach search --from shop-web,worker --yes   # exactly these
python3 scripts/llc.py reach search --add worker --yes       # one more, the rest kept
python3 scripts/llc.py reach search --remove worker --yes    # one fewer
python3 scripts/llc.py reach search --from '*' --yes         # the whole workspace
python3 scripts/llc.py reach search --none --yes             # nothing
```

`reach DB` on a database shows what links it; every change there is refused:
use `link`.

`set ID --json FILE --yes` with `{"reachableFrom": [...]}` does the same. Left
out of a `set`, it stays as it is.

Narrowing or closing needs no permission, but it can break what the user runs:
ask before you close a resource you didn't make.

## What needs Network

An API key or an agent needs the Network permission to let a resource reach
another it couldn't reach before, or to let the whole workspace in. A person
in the console never does. It needs no Network when:

- this agent or key made both resources, now or earlier: an app and the
  databases made with it, a database made first and linked next, or a service
  added to a Composable App whose services it made;
- the one reached already lets the whole workspace in, with `["*"]` set on it
  (setting `["*"]` always needs Network; a resource with no setting never
  counts, and a database never does: it has no setting);
- a new browser goes into a Browser API that already drives every browser.

So it needs Network to link a database it didn't make, to link a database it
made from an app, machine or Desktop App it didn't make, and to add a service
to a Composable App it didn't make (`join`, or a `stack` naming that app): the
new service and every service of the app then reach each other, and what the
app reaches and what reaches it go with that one refusal. A database restored
into a new one is reached by nothing until it is linked, and linking it is
like any link.

Network is off for every agent and every key until a person turns it on: for
an agent on the console's Agents page, for an API key on the Keys page.

## Reading `reach`

```json
{
  "id": "search",
  "reachableFrom": ["shop-web"],
  "alsoFrom": [{"id": "worker", "why": "waits for it"}],
  "addresses": [{"host": "…", "port": 7700}]
}
```

- `reachableFrom` is the setting.
- `alsoFrom` lists who reaches it without the setting: `links it`, `waits for
  it`, `drives it` (a Browser API; `through` is what can drive the browser
  through it), `same app`.
- `addresses` are the names that answer inside the workspace, for the
  resources allowed. They are left out when nothing may reach it.
- `reachableFrom: null` with a `note`: a resource that has no setting yet. Set
  the whole list with `--from` or `--none`; `--add` and `--remove` are refused
  on it.
- A database has no `reachableFrom` at all: its `note` says it is reached only
  by what links it, `alsoFrom` lists the apps, machines and Desktop Apps that
  link it or wait for it, and `addresses` appear only when something does.
- A Composable App's name works in place of an id: `reach shop` shows and
  changes the whole app.

`reach` only reads the workspace's settings: it holds no machine and hands out
no token. (`connect ID` holds a machine for this agent and makes a link, so use
it to connect, not to look.)

## When it is refused

| Answer | Means | Do |
|---|---|---|
| 403 "This agent can't let web reach shop-db inside the workspace. A person can turn on Network for it on the Agents page." (`network_permission`) | This agent (or key, "Keys page") lacks Network | Tell the user what would reach what and why. They turn on Network, or make the opening themselves in the console (Reachable from on the resource; for a database, a link on what uses it: a database has no Reachable from). Never work around it |
| 403 "This API key can't add worker to Composable App shop inside the workspace. A person can turn on Network for it on the Keys page." (`network_permission`) | Adding a service to a Composable App this key or agent didn't make | As above: ask the user, saying which app it joins and why |
| 403 "This agent can add services only to an app it created." | Joining someone else's Composable App | Ask the user; a person allows more on the Agents page |
| 422 `reachableFrom: a database is reached only by what links it: …` | `reachableFrom` sent for a database | Leave it out; with the user's agreement, `link APP DB --yes` |
| 422 `… a machine gets no variables from a link: it only lets it reach the database` | `env` on a machine's or Desktop App's link | Send `{"id": "<database>"}` alone |
| 422 `there is no resource "x" in this workspace` | A name that isn't here | `ls`; fix the name |
| 422 `"*" (the whole workspace) goes alone` | `"*"` with names | One or the other |
| 422 `… always reaches itself` | The resource, or its own app, in its own list | Leave it out |
| 422 `… is the name of Composable App …` | An id that is another app's name | Pick another id, or add it to that app |
| 422 `the services of Composable App … share one reachableFrom` | Different values on its services | Send the same value on each |
