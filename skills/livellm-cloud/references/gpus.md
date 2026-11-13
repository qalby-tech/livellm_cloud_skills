# GPUs

Where LiveLLM rents GPUs, a GPU is rented by the hour for the workspace, then
given to what should use it: one machine has a GPU rented for machines whole,
while apps and Desktop Apps share one rented for apps. Several GPUs rented
together on one server are a **cluster** (here a cluster always means GPUs
rented together, never the cloud itself): a machine gets every GPU of its
cluster, and apps share them.

## What it costs

Renting spends the user's money. Read the price first, every time:

```
python3 scripts/llc.py gpu-models
python3 scripts/llc.py gpu-models --from 2026-11-20T10:00:00Z --to 2026-11-20T14:00:00Z --use machine
```

Each model has `centsHour` (US cents an hour, for one GPU; a model with no
price can't be rented), how many are free now for a machine and for apps
(`freeNow`), and how long a rental starting now could last before a booking
needs the GPU (`freeUntil`, null for no limit). With `--from` and `--to` (whole
UTC hours, at most 14 days apart), `slots` counts the free GPUs in each hour.

- **A rental now** is charged by the minute until it ends or is released.
  Without an end it runs until it is released, or until a booking ahead needs
  the GPU (its `endsAt` then says when).
- **A booking** (`--start` and `--end`) is charged for every booked hour from
  its start, used or not. At most 168 booked hours may be ahead per account.
- **Cancelling a booking** more than 24 hours before its start is free. Later,
  it is charged in full: the cancel is refused (428) until the user agreed to
  that charge, and then goes again with `--confirm-charge`. Taking a booked GPU
  off a cluster then is charged the same way.
- **Time a GPU can't be used** through no fault of the user's isn't charged.
- GPUs are always **pay as you go**: it must be on, with a card where payments
  are taken, and the month's GPU charge counts against the pay-as-you-go limit.
  A 402 is the user's to settle on the console's Billing page (SKILL.md rule 4).

**Always ask before renting, booking, extending or a charged cancel.** Say the
model, how many, the hours, and the price from `gpu-models` (an hour, and in
all for a booking or an extension), and wait for the user's agreement. Then
run the command with `--yes`, and with `--confirm-charge` only when they agreed
to a cancel charged in full. Never rent again on your own because a rental
ended, and never release or cancel one you didn't rent unless the user asks.

## Who may rent

Reading the models, the rentals and what fits works for any key and agent.
Renting, extending, changing use, releasing and removing a GPU need the
workspace's owner: a person in the console, or an API key with the billing
permission (`LIVELLM_API_KEY`). **An agent sign-in can never rent**, whatever
its permissions: when it is refused, tell the user, and ask them to rent the
GPU in the console (then give you its id), or to give you an API key with the
billing permission. Never look for another way.

Where GPUs aren't rented, `gpu-rent` answers "GPU renting isn't offered on this
LiveLLM": tell the user, and stop. `gpus` may still list GPUs an administrator
set for the workspace (`noEnd`, for machines or for apps); `gpu` names them by
their `id` as it names a rental, and they are never rented, extended or
released.

## Rent

```
python3 scripts/llc.py gpu-fit --model rtx5090 --use machine
python3 scripts/llc.py gpu-rent --model rtx5090 --use machine --yes
python3 scripts/llc.py gpus
```

- `--use machine`: one machine has the GPU whole. `--use apps`: apps and
  Desktop Apps share it.
- One GPU: `--model`. Several on one server: `--cards rtx5090=2` (models may
  mix: `rtx5090=1,h100=1`). Several clusters at once: `--json clusters.json`,
  a list of `{"use", "cards", "location"?, "target"?}`; all are rented, or
  none. Where clusters aren't offered, a cluster of more than one GPU is 422
  `gpu_clusters_off`: tell the user.
- Where: left out, any server this workspace may use; `--region R` or `--host
  H` (`gpu-fit` lists them under `hosts` and `regions`).
- `gpu-fit` takes the same choice and rents nothing: whether each cluster
  fits, how many GPUs of each model it could have (`max`), and the servers and
  regions with their free GPUs. Run it before asking the user when the choice
  is more than one GPU.
- A booking: `--start 2026-11-20T10:00:00Z --end 2026-11-20T14:00:00Z` (whole
  UTC hours, at least 1 hour, at most 30 days, starting within 90 days). It may
  name what gets the GPU at its start: `--machine M` or `--apps a,b`.

`gpus` lists the workspace's rentals: booked, running, and ended in the last 30
days. Each has its `id` (what `gpu` names), `use`, `state`, `phase` (`ready`,
or getting ready), `endsAt`, `usedBy`, `hoursThisMonth`, `centsThisMonth`, and
`cards` (each GPU of the cluster, `answering` false while one isn't working).

## Give it to a machine, an app or a Desktop App

Once its phase is `ready`, name the rental's id in `gpu`, with the user's
agreement:

| Resource | Create file | Set file |
|---|---|---|
| Machine (rented for machines) | `"gpu": "g-3f9a12bc"` | `{"vm": {"gpu": "g-3f9a12bc"}}` |
| App (rented for apps) | `"gpu": "g-3f9a12bc"` | `{"pod": {"gpu": "g-3f9a12bc"}}` |
| Desktop App (rented for apps) | `"gpu": "g-3f9a12bc"` | `{"desktop": {"gpu": "g-3f9a12bc"}}` |

A set that leaves `gpu` out keeps it; `""` takes it off. A machine with a GPU
runs on its GPU's server (one machine at a time), so a placement elsewhere is
refused (422 `gpu_server`), and it starts only once every GPU of its cluster
is ready (409 `gpu_not_ready`: wait). A running machine that gets one may
need a restart (`restartHint`; ask the user first). Where GPUs are rented, a
GPU in a machine's `devices` is refused until it is rented (409
`gpu_not_rented`): rent one and use `gpu` instead.

## Change, extend, release

```
python3 scripts/llc.py gpu-extend g-3f9a12bc --end 2026-11-20T18:00:00Z --yes
python3 scripts/llc.py gpu-use g-3f9a12bc apps --yes
python3 scripts/llc.py gpu-release g-3f9a12bc --yes
python3 scripts/llc.py gpu-remove-card g-3f9a12bc g-7c01d2e4 --yes
```

- `gpu-extend`: a later end (a whole UTC hour) for a booking or a running
  rental, if its GPUs are free for the added hours. The added hours cost
  money: ask first.
- `gpu-use`: from machine to apps or back, at most once an hour, with nothing
  using it. The GPU then gets ready for its new use (`phase`
  `switching-to-…`): `gpus` until it is `ready`.
- `gpu-release`: charging stops now (a booking is cancelled, as above). Take
  the GPU off what uses it first (409 `gpu_in_use` names them); ask the user
  before stopping anything.
- `gpu-remove-card`: one GPU off a cluster of several (for instance one that
  isn't working: its machine waits until it is taken off), while nothing runs
  on the cluster; the rest stay and its charging stops. The last GPU stays:
  release the rental instead.

## When it goes wrong

- **403 to an agent sign-in, or to a key without billing.** Tell the user, and
  stop (Who may rent, above).
- **409 `gpu_none_free`.** No free GPU for those hours: `gpu-models` with
  hours, or `gpu-fit`, shows what is free; ask the user for another model,
  other hours or fewer.
- **402.** Pay as you go is off, a card is missing, a payment is overdue, or
  the limit is reached: the user's, on the Billing page.
- **409 `gpu_change_too_soon`.** Wait until the time it names.
- **A rental or an extension that got no answer** (a timeout, a 5xx): it may
  have been made. Run `gpus` before sending it again; never rent or extend twice.
- **A GPU that isn't working** (`answering` false): it isn't charged. Tell the
  user; take it off with `gpu-remove-card` only if they agree. The platform
  never takes one off by itself.
