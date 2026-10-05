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
python3 scripts/llc.py build web --wait   # ...and wait until it is live (or print why it failed)
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
- `"internal": true` on a port means no public address: it answers only
  inside the workspace, to its own stack, the apps that link it or wait for
  it (with their whole Composable App) and the resources the app's
  `reachableFrom` names, and may be any TCP protocol. Give every database,
  cache and queue an internal port — never a public one. `connect` shows where
  it answers.
- `dependsOn` lists what an app needs first. It starts once each one's first
  port accepts a connection, and none of them can be deleted while it is listed
  (delete the dependent app first). Apps that wait for each other in a loop are
  refused.
- To create several at once, all or nothing, put their settings in one JSON
  list and run `python3 scripts/llc.py create apps --json stack.json --yes` —
  the same bodies `create pod` takes, one per service.
- To add a service to an app that is already there, add `--join <app>`: the
  new services take its stack, and an app on its own gets a stack named after
  itself (its name inside stays its id, and it restarts once as it joins —
  tell the user first). The new services take the app's `reachableFrom` and
  reach what it reaches. Adding a service to a Composable App is letting it
  in: it and every service of the app reach each other. Ask the user first
  unless you made the app and every service already in it (rule 11);
  otherwise an agent or key also needs the Network permission (403
  `network_permission`), and an agent may add services only to an app it created unless a person allowed
  more (403 otherwise: ask the user).
- A Composable App is one resource to the rest of the workspace: other
  resources reach none of its services until its `reachableFrom` names them,
  and all its services share that one setting
  (`references/inside-access.md`).

For a database the user cares about, prefer a managed one over a `postgres`
image in a stack: it has backups. Make it with the app, as below.

## An app with its databases

Most software needs a database, often a cache too. Make the app and its
managed databases in one step, all of it or none, with the app linked to them
(`references/databases.md`, "Link it to an app"). Leave the databases'
passwords out: the platform makes them and gives them to the app through the
links, so no password passes through you. A link also lets the app reach the
database, and nothing else does: a database is reached only by what links it.
A link with no variables (`{"id": "cache"}`) only lets the app reach it.

A Nextcloud, for example. `cloud.json`:

```json
{
  "apps": [{
    "id": "cloud",
    "image": "nextcloud:stable-apache",
    "cpu": "1", "memory": "2Gi",
    "ports": [{ "name": "http", "port": 80 }],
    "volumes": [{ "name": "html", "size": "20Gi", "mountPath": "/var/www/html" }],
    "env": [
      { "name": "NEXTCLOUD_ADMIN_USER", "value": "admin" },
      { "name": "NEXTCLOUD_TRUSTED_DOMAINS", "value": "cloud-http-WORKSPACE.cloud.live-llm.com" },
      { "name": "OVERWRITEPROTOCOL", "value": "https" },
      { "name": "TRUSTED_PROXIES", "value": "10.0.0.0/8" }
    ],
    "secretEnv": [{ "name": "NEXTCLOUD_ADMIN_PASSWORD", "value": "GENERATE-ONE" }],
    "databases": [
      { "id": "cloud-db", "env": { "POSTGRES_HOST": "host", "POSTGRES_DB": "database",
                                   "POSTGRES_USER": "username", "POSTGRES_PASSWORD": "password" } },
      { "id": "cloud-cache", "env": { "REDIS_HOST": "host", "REDIS_HOST_PORT": "port",
                                      "REDIS_HOST_PASSWORD": "password" } }
    ]
  }],
  "databases": [
    { "id": "cloud-db", "engine": "postgres", "version": "17", "storageSize": "10Gi",
      "backup": { "enabled": true, "mode": "daily", "keepDays": 7 } },
    { "id": "cloud-cache", "engine": "redis", "storageSize": "1Gi", "cpu": "250m", "memory": "256Mi" }
  ]
}
```

```
python3 scripts/llc.py create apps --json cloud.json --yes
python3 scripts/llc.py wait cloud
python3 scripts/llc.py connect cloud
```

- Each database is what `create storage` takes; `credentials` may be left
  out here (not with `"adminConsole": true`, which signs in with a password
  you send). The databases remember the app they were made with.
- The app waits for its databases, then starts. Hand the user the address
  and the admin password you generated, once.
- An HTTP port's address is `<id>-<port name>-<workspace>.cloud.live-llm.com`
  (`whoami` names the workspace); Nextcloud trusts only the domains it was
  installed with, so put that address in before the first start. `connect`
  shows the address, and the port's `proxy.trust` range for
  `TRUSTED_PROXIES` (see "Who can reach it"; change it with `set` if it
  differs, Nextcloud reads it on every request).
- Several services work the same way: each one in `apps` with its `stack`
  and `hostname`, each linking the databases it uses.
- `rm cloud --with-databases --yes` deletes the app and the databases made
  with it that no other app uses, with their data; it prints which went and
  which stayed. Without the flag the databases stay. Ask first.
- Save the whole thing for next time: `template save nextcloud --from cloud`
  keeps every service, its databases and the links, never a password or a
  secret value (`references/workspace.md`, "Templates").

## Settings the app reads

```json
"env": [{ "name": "LOG_LEVEL", "value": "info" }],
"secretEnv": [{ "name": "STRIPE_KEY", "value": "sk_live_..." }]
```

`env` is plain and visible. `secretEnv` is write-only: the value is kept out of
sight and can't be read back, so put passwords, keys and tokens there. Send a
new value to change it; leave the value out to keep the stored one. A managed
database's details come from a link (`"databases"`, above), not from either.

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

The platform's HTTPS proxy connects to the app from the range in the port's
`proxy.trust` (status `endpoints[]`, connect `urls[]`) and passes the visitor's
address in `X-Forwarded-For` and `https` in `X-Forwarded-Proto`: have the app
trust that range for them (Nextcloud `TRUSTED_PROXIES` + `OVERWRITEPROTOCOL=https`,
Django `SECURE_PROXY_SSL_HEADER`, Express `app.set("trust proxy", range)`).

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

## Where it runs

Leave `placement` out and LiveLLM picks the host. To choose, put
`"placement": {"strategy": "region", "region": "<r>"}` or
`{"strategy": "host", "host": "<id>"}` in the file (each service has its own); `llc.py hosts`
lists ids and regions. Changing it (`set` with `{"pod": {"placement": …}}`, `null` for
automatic) restarts the app there; an app with volumes and a location goes briefly offline
on each change, and one pinned to a host waits while that host is down.

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
