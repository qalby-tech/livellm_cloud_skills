# Apps

An app runs a container and gets a public HTTPS address (or a raw TCP/UDP
address, see below). It comes either from an image you name, or from a Git
repository the platform builds for you. In the
console this is a **Composable App** (New resource → Apps): one service or
several. Each service is one `pod` body here.

## From an image

`app.json`:

```json
{
  "id": "web",
  "image": "nginx:1.27",
  "cpu": "500m",
  "memory": "512Mi",
  "ports": [{ "name": "http", "port": 80 }]
}
```

```
python3 scripts/llc.py create pod --json app.json --yes
python3 scripts/llc.py wait web
python3 scripts/llc.py connect web
```

`connect` lists each port's address. A private image needs a login:
`"imageAuth": {"username": "...", "password": "..."}`. The password is stored
write-only and never comes back.

## From a repository

```json
{
  "id": "web",
  "source": { "git": { "url": "https://github.com/acme/app", "ref": "main" } },
  "ports": [{ "name": "http", "port": 8080 }]
}
```

The repository needs a Dockerfile. A private repository takes a token in the
same block: `"source": {"git": {...}, "gitAuth": {"token": "..."}}`, write-only
like every other secret here.

Creating it starts the first build. Follow it:

```
python3 scripts/llc.py progress web
```

A build that works goes live by itself. Later:

```
python3 scripts/llc.py build web      # build the current code again
python3 scripts/llc.py builds web     # what has been built, with commits
python3 scripts/llc.py deploy web 0a22de73 --yes   # run an earlier build again
```

Rolling back is just running an older build again. Say which one you picked and
why. The app keeps its recent builds; they count against the plan's disk.

## Apps that work together

Most real software is several services: a site, a worker, a database, a cache.
Put them in one **stack** — the services of one Composable App — and they find
each other by plain name:

```json
{ "id": "shop-db",  "stack": "shop", "hostname": "db",  "image": "postgres:17",
  "ports": [{ "name": "pg", "port": 5432, "internal": true }],
  "volumes": [{ "name": "pgdata", "size": "10Gi", "mountPath": "/var/lib/postgresql/data" }] }
{ "id": "shop-web", "stack": "shop", "hostname": "web", "image": "ghcr.io/acme/shop:1.4",
  "dependsOn": ["shop-db"],
  "env": [{ "name": "DATABASE_HOST", "value": "db" }],
  "ports": [{ "name": "http", "port": 3000 }] }
```

- `stack` groups them; `hostname` is the name the others use (`db:5432`). Names
  belong to the stack, so two stacks can both have a `db`. Any port works
  between them, declared or not.
- `"internal": true` on a port means no public address: it answers inside the
  workspace only, and may be any TCP protocol. Give every database, cache and
  queue an internal port — never a public one. `connect` shows where it answers.
- `dependsOn` lists what an app needs first. It starts once each one's first
  port accepts a connection, and none of them can be deleted while it is listed
  (delete the dependent app first). Apps that wait for each other in a loop are
  refused.
- To create several at once, all or nothing, put their settings in one JSON
  list and run `python3 scripts/llc.py create apps --json stack.json --yes` —
  the same bodies `create pod` takes, one per service.

For a database the user cares about, prefer a managed one
(`references/databases.md`) over a `postgres` image in a stack: it has backups.

## Settings the app reads

```json
"env": [{ "name": "LOG_LEVEL", "value": "info" }],
"secretEnv": [{ "name": "DATABASE_URL", "value": "postgres://..." }]
```

`env` is plain and visible. `secretEnv` is write-only: the value is kept out of
sight and can't be read back, so put passwords and connection strings there.
Send a new value to change it; leave the value out to keep the stored one.

## Who can reach it

An HTTP port becomes a public HTTPS address. Unless the user wants a public
site, protect it when you create it:

```json
"ports": [{
  "name": "http", "port": 8080,
  "access": {
    "allowCIDRs": ["203.0.113.0/24"],
    "auth": "basic",
    "basicUsers": [{ "username": "acme", "password": "GENERATE-ONE" }]
  }
}]
```

Passwords here are write-only too. When you change who may enter, send every
user's password again; sending some and not others is refused.

## Raw TCP and UDP ports

For anything that isn't HTTP — a game server, a mail server, a VPN, DNS — mark
the port `"tcp": true` or `"udp": true` (one of them, never with `internal`):

```json
"ports": [
  { "name": "game", "port": 25565, "tcp": true,
    "access": { "allowCIDRs": ["203.0.113.0/24"] } },
  { "name": "voice", "port": 9987, "udp": true }
]
```

Such a port gets a public `host:port` instead of an HTTPS address. `connect`
lists it with `"raw": true`, its `protocol` and its `address`; `ls` shows it in
`endpoints` as `{"name": "game", "tcp": true, "addr": "host:port"}` (or
`"udp": true`). Hand the user that `host:port`. The platform picks
the outside port number, so it differs from `port`. A raw port takes no
password (`"auth": "basic"` is refused): `allowCIDRs` is its only protection,
so set it unless the user wants the port open to everyone. A protocol spoken
only between apps needs no raw port — use `"internal": true`.

## Keeping data

`volumes` give the app disks that survive restarts, redeploys and stops:

```json
"volumes": [
  { "name": "data", "size": "10Gi", "mountPath": "/data" },
  { "name": "uploads", "size": "20Gi", "mountPath": "/srv/uploads" }
]
```

- Up to 8. A name is lowercase letters, digits and hyphens, at most 15
  characters, and unique. Each `mountPath` is an absolute folder (letters,
  digits and `. _ @ + -`, no trailing slash), not `/`, not in `/proc`, `/sys` or
  `/dev`, and not inside another volume's path.
- A volume can grow (send a bigger `size`) but never shrink. A new name is a
  new, empty disk.
- **Removing a volume from the list deletes its data.** Ask the user first.
  A save that leaves `volumes` out keeps them all; `"volumes": []` removes
  them all.
- An app with a volume runs one copy: `replicas` above 1 is refused.
- The older `"storage": {"size", "mountPath"}` still works and is the volume
  named `data`; send `storage` or `volumes`, never both.

Databases belong in a database, not in an app's disk: see
`references/databases.md`.

## Stopping and starting

```
python3 scripts/llc.py stop web --yes   # runs nothing; volumes are kept
python3 scripts/llc.py start web        # runs it again
python3 scripts/llc.py wait web
```

A stopped app shows the state `Stopped`, is not counted as down, and costs only
its disks. Stop an app to save money while it isn't needed; delete it (`rm`)
only when the user wants it and its data gone. The same works for machines.
Browsers and databases can't be stopped; `stop` refuses them.

## When it goes wrong

- **The build failed.** `progress web` shows the stage and the error. Fix the
  repository, then `build web`. Don't change unrelated settings to force it.
- **The address answers 404 or 502.** The app may still be starting: `wait web`.
  Check the port number matches what the program listens on.
- **It built and started but doesn't work.** `logs web` prints the last log
  lines with each container's state, restarts, processor and memory use. Read
  it before guessing; quote the line that explains it when you report back.
- **It keeps restarting.** `logs web` shows the restart count — usually a crash
  on boot, and the reason is in the last lines before each restart.
- **A value was refused (422).** The message names the field. Fix that one.
- **The app needs a password you generated.** Show it to the user once; it can't
  be read back.
