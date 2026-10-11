# Reaching your computer from any network

On your own wifi the phone finds the computer by itself and none of this applies. This
is about the other case: a phone on mobile data, or on somebody else's wifi, and a
computer behind a home router. Neither has a public address, so something with one has
to introduce them.

## The thing to understand before choosing

There are two shapes of answer, and they are not equally safe.

**A byte pipe.** Two sides dial in, it copies bytes between them, it cannot read any of
them. The phone does TLS against *your computer's* certificate, and those records pass
through the middle as noise. This is what `relay.py` and the Cloudflare Worker in
`worker/` do, and it is why hosting one is a question of cost rather than trust.

**An HTTP tunnel.** `ngrok http`, Cloudflare Tunnel, and most "expose your localhost"
services terminate TLS at *their* edge, decrypt, and re-encrypt to you. That is not a
flaw in them — it is what lets them do caching, WAF, and a nice dashboard. But it means
the provider can read every message, every agent name, and every byte of the browser
stream. **Olivaw's whole design assumes the middle cannot read anything, so an HTTP
tunnel throws away the property the rest of this was built around.**

Where a service offers a raw TCP tunnel (`ngrok tcp`, for instance) it is a byte pipe
again, and fine.

## The options

| | Free? | Byte pipe? | Catch |
|---|---|---|---|
| **Cloudflare Worker + Durable Object** (`worker/`) | yes, no card | **yes** | ~37 h of browser streaming/day |
| **Oracle Cloud Always Free VM** (`relay.py`) | yes, card to sign up | **yes** | reclaims "idle" instances; A1 capacity often unavailable |
| **Tailscale** (Personal) | yes | **yes** (direct WireGuard) | an app on both ends; non-commercial use only |
| **A VPS you already pay for** (`relay.py`) | — | **yes** | ~200 lines of stdlib, nothing to install |
| **ngrok**, TCP endpoint | 1 GB/month | yes | 1 GB is ≈3.7 h of streaming, then it stops |
| **ngrok**, HTTP endpoint | 1 GB/month | **no** | reads everything |
| **Cloudflare Tunnel** | yes | **no** | reads everything; needs a domain on Cloudflare |

### Recommended: the Cloudflare Worker

No card, no domain, no machine to keep alive, and nothing to reclaim. A Durable Object
sleeps while nobody is connected and wakes when a byte arrives, which is exactly the
shape of this problem — a house that is attached but idle for twenty-three hours a day.

```
cd relay/worker
npm create cloudflare@latest -- olivaw-relay   # then replace src/index.js with worker.js
npx wrangler deploy
```

It lands on `https://olivaw-relay.<your-subdomain>.workers.dev`. The address Olivaw
wants is the `wss://` form of that, in the console under **Equipo → Tu teléfono**.

The free plan is 100,000 requests a day with WebSocket messages counted at 20:1, and
13,000 GB-s of wake time. Screen streaming at 15fps is the heaviest thing here and works
out at about 2,700 billed requests an hour — roughly **37 hours a day** of watching a
browser, which is more than there is. Chat and routines are rounding errors beside it.

### If you would rather run a real machine

`relay.py` needs Python 3 and nothing else — no pip install, no systemd unit if you do
not want one. Oracle's Always Free tier is the only one left in 2026 that will keep a
small VM indefinitely, but read the catch: Oracle reclaims instances whose CPU **and**
network sit below 20% over seven days, and a rendezvous that is mostly idle is exactly
that shape. A cron job that does something every few hours is usually enough to avoid
it. Google's free e2-micro has 1 GB/month of egress on the Premium tier, which the
browser stream eats in an evening. fly.io and Koyeb have both closed their free tiers.

### Tailscale, if it is only ever your own devices

Different in kind: it puts the phone and the computer on the same private network, so
Olivaw needs no rendezvous at all and no code here changes — the phone just dials the
computer's `100.x.y.z` address as if it were on the wifi. Traffic goes directly between
the two devices; Tailscale's own relays are used only when that fails. The costs are
that it is an app on both ends, an account, and a free tier for non-commercial use.
For one person and their own phone it is the least work of anything on this list.
