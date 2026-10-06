# Browser profiles

A browser's profile is everything it remembers: cookies and sign-ins, saved
site data, settings and extensions (a Camoufox browser's also holds its
fingerprint, so an imported or copied profile brings that along). It is kept between tasks. You can take
snapshots of it and switch back, export it to a file, import one, copy it to
another browser, or add cookies to it.

**A profile holds the user's sign-ins.** Before you export, import or copy
one, or add cookies, ask the user and wait for their agreement. Never upload
an exported file. Whoever has an exported file can be signed in as the user on
those sites.

## What it needs

`profile show` needs nothing extra. `export` and `cookies` need Connect
(whatever drives the browser can read its sign-ins anyway). `snapshot`,
`restore`, `rm`, `import` and `copy` change the browser: Create (on a browser
this agent made) or Manage everything for the browser that receives it; a
`copy` also needs Connect on the browser it copies from. No other permission
is involved: asking the user first (above) is what stands between you and a
profile.

A workspace can also keep exports to its own people: then a 403 "Profiles hold
sign-ins. Only the workspace's people can export them." refuses an export, or a
copy out of it, whatever permissions you have. Tell the user; asking for a
permission won't change it.

A browser made before profiles were offered answers 409 "Restart this browser
once to turn on profiles". Ask the user, then `llc.py restart ID --yes`: its
tabs close, the profile and sign-ins are kept. `proxy show ID` says
`"profilesReady": true` once it can, and `profile show` answers instead of the
409.

## Snapshots

```
python3 scripts/llc.py profile show shop
python3 scripts/llc.py profile snapshot shop --name before-checkout --yes
python3 scripts/llc.py profile restore shop --snapshot SNAPSHOT_ID --yes
python3 scripts/llc.py profile rm shop --snapshot SNAPSHOT_ID --yes
```

- **Taking a snapshot closes the browser's tabs for a few seconds.** The
  browser keeps its address; a Browser API session on it gets a new blank tab.
  Tell the user before you take one while something is open.
- `restore` puts the snapshot back in place of the current profile, which is
  gone unless you pass `--keep-current` (it becomes a snapshot of its own).
  Ask before restoring.
- A browser keeps at most `maxSnapshots` snapshots (`profile show` says how
  many; 10 unless the platform set another number). One more answers 409 "…snapshots.
  Delete one first.": ask the user which one to delete. Snapshots use
  the browser's storage: a 507 means it is full, and the user grows it or
  deletes a snapshot.
- Deleting the browser deletes its snapshots.

## Export to a file

```
export PROFILE_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
python3 scripts/llc.py profile export shop --out shop.llcprofile.age --password-env PROFILE_PASSWORD --yes
```

- With `--password-env`, the file is encrypted with the password in that
  environment variable (never on the command line). Prefer it: generate one, or
  use the user's, and show it to the user once with the file. Without, the file
  is plain and the answer says so. An encrypted file opens with `age -d` too.
- `--snapshot ID` exports that snapshot instead of the profile as it is now.
  Exporting the profile as it is now closes the tabs for a few seconds.
- The file is saved readable only by you and never overwrites one. Hand it to
  the user, then delete your copy.

## Import a file

```
python3 scripts/llc.py profile import shop --file shop.llcprofile.age --password-env PROFILE_PASSWORD --yes
```

- It replaces the browser's profile. Ask first; take a snapshot first if the
  user wants a way back.
- Only files exported from a LiveLLM browser are taken (422 otherwise). For
  sign-ins from another browser, import cookies instead.
- A file with a password needs it: a 422 "This file is password protected."
  (sent without one) or "The password doesn't open this file." (a wrong one)
  means ask the user for the file's password, put it in an environment
  variable and pass `--password-env VAR`. Never guess one, never type it on
  the command line.
- A profile from a newer Chrome (or Camoufox) answers 409 "Import anyway?":
  pass `--force` only if the user agrees.
- **Profiles move only between browsers of one engine.** A Chrome profile into
  a Camoufox browser is refused (422 `profile_engine`), a Camoufox profile
  into a Chrome browser too (422 `not_livellm_profile`, "Only profiles
  exported from LiveLLM Chrome browsers…"), and so is a copy across engines
  (422 `engine_mismatch`). Add the sign-ins as cookies instead (below).
- Language and time zone stay as the browser's settings say, whatever the
  file had.

A new browser from a file: create the browser, `wait` for it, then import.

## Copy to another browser

```
python3 scripts/llc.py profile copy shop-2 --from shop --yes
```

`shop-2`'s profile is replaced with `shop`'s (or with `--snapshot ID` of
`shop`). `shop` is left as it is. Both must be in this workspace and run the
same engine.

## Add cookies

`cookies.json` is a list, in the shape Playwright uses:

```json
[{"name": "sid", "value": "…", "domain": ".example.com", "path": "/",
  "secure": true, "httpOnly": true, "sameSite": "Lax", "expires": 1767225600}]
```

```
python3 scripts/llc.py cookies shop --json cookies.json --yes
rm cookies.json
```

They are added to the running browser at once; the answer gives only the
count. Up to 5000 cookies. This is how sign-ins move between a Chrome and a
Camoufox browser. A Camoufox browser leaves out cookies Firefox refuses (a
`"sameSite": "None"` one without `"secure": true`, for one) and counts them in
`dropped`. Cookies are sign-ins too: only ones the user gave
you, and delete the file afterwards.

## With the LiveLLM tools

The `browser_profile` tool does the same within the tools: `list` shows the
snapshots, `snapshot`, `restore` and `delete` change them, and `copy` copies
one browser's profile into another (ask the user first). Export and import are
files: use `scripts/llc.py`.
