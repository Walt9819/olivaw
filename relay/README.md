# El punto de encuentro

Your computer is behind a router. Your phone, on mobile data, is behind the carrier's.
Neither can dial the other, so both dial out to a meeting point and it introduces them.

`relay.py` is that meeting point. About 200 lines, standard library only, no database, no
configuration:

```bash
python3 relay.py --port 8080
```

## It cannot read anything, and that is the design

It does not terminate TLS. The phone performs TLS against **your computer's** pinned
certificate, and those records pass through here as bytes nobody here has a key for.
Whoever runs this can see that a room is busy and count the bytes; they cannot see a
message, an agent's name, or a screen.

That is what makes "where do we host it" a question about cost and uptime instead of
trust — and it is why it does not need a certificate of its own. `ws://` is fine, because
what flows through is already encrypted end to end. Put it behind a reverse proxy with a
certificate if you want the metadata private too.

## Where to run it

It needs one thing: an address the internet can reach.

| | cost | notes |
|---|---|---|
| **Oracle Cloud Always Free** | free, indefinitely | an ARM VM with a public IP; the most "run it yourself" option |
| **A VPS** | a few euros a month | the least surprising |
| **A Raspberry Pi** | hardware you own | only if your home connection has a reachable address — many do not |
| **A box you already have** | free | if iGalenus has a server with a public IP, it is already the answer |

A relay serves many people: it forwards bytes and holds no state beyond a dictionary of
live rooms. One small machine is enough for everyone you would ever hand Olivaw to.

## Then, on the computer

**Tu teléfono → Fuera de casa**, paste `ws://your-relay:8080`, press Activar. The
computer dials out and stays attached, reconnecting on its own when the laptop sleeps or
the wifi drops.

The room id and its key are generated on the computer and never typed by anyone. A room
belongs to whoever registered it first, and only to them — a second machine presenting
the wrong key is refused, so a leaked room id costs you nothing except the ability to
annoy your own tunnel.

## What it is not

Not a proxy, not a VPN, not an account system, and not something anyone has to sign up
for. If it is down, the phone falls back to the local wifi and everything keeps working
at home.
