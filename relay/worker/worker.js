// The same rendezvous as relay.py, as a Cloudflare Worker + Durable Object.
//
// Why this version exists: relay.py needs a machine that stays up, and every "always
// free" VM in 2026 has a catch - Oracle reclaims instances it decides are idle, Google's
// free egress is 1 GB/month, fly.io and Koyeb have closed their free tiers. A Durable
// Object has no machine to reclaim. It sleeps while nothing is happening, wakes when a
// byte arrives, and the free plan is 100,000 requests a day with WebSocket messages
// counted at 20:1 - which is about 37 hours of screen streaming per day.
//
// It does exactly what relay.py does and no more: two sides dial in, it forwards frames
// between them. It cannot read any of it. The phone does TLS against the *computer's*
// pinned certificate and those records pass through here as opaque bytes, so this code
// never sees a key, a message or an agent's name. That is the whole reason it is safe to
// run this on somebody else's platform.
//
// Deploy:
//   npm create cloudflare@latest -- olivaw-relay
//   (replace src/index.js with this file and wrangler.toml with the one beside it)
//   npx wrangler deploy
// It lands on https://olivaw-relay.<your-subdomain>.workers.dev - no domain, no card.
// The URL Olivaw wants is the wss:// form of that.

const ROOM_MAX = 64;

// How many phones may be in one room at once. The room belongs to the computer, so
// every phone paired to it meets here, and a household with three is ordinary. The
// cap is there because the room id is the only thing standing between a stranger and
// an attachment: they still meet a TLS endpoint that will not talk to them, but with
// no cap they could sit here in numbers and have every frame the house sends copied
// to each of them, which is somebody else's quota. The NEWCOMER is refused rather
// than the oldest evicted - evicting would let whoever guessed the id push the
// owner's own phone out of its room.
const GUEST_MAX = 8;

// Where role, room and key travel.
//
// They used to be query parameters, which is the worst place to put a secret: the URL
// is the part of a request that everything logs. Cloudflare's own handler span records
// `url.full`, so enabling traces would have written every house's room key into the
// dashboard - see the note in wrangler.toml. They are headers now.
//
// The query string is still read, because an install that has not updated yet is still
// an install that should work. That fallback is exactly why invocation logs stay off.
function pick(request, url, name) {
  return (request.headers.get("x-olivaw-" + name)
          || url.searchParams.get(name) || "").trim();
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return Response.json({ ok: true, app: "olivaw-relay" });
    }
    if (url.pathname !== "/attach") {
      return new Response("no", { status: 404 });
    }
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("websocket only", { status: 400 });
    }

    const role = pick(request, url, "role");
    const room = pick(request, url, "room");
    if ((role !== "host" && role !== "guest") || !room) {
      return new Response("bad request", { status: 400 });
    }
    // Refused, not shortened. This used to be .slice(0, ROOM_MAX), which silently
    // merges two rooms that happen to share a prefix - and the thing being truncated
    // is the identifier that decides which computer you reach. Ids are about twenty
    // characters, so nothing legitimate is anywhere near this.
    if (room.length > ROOM_MAX) {
      return new Response("room id too long", { status: 400 });
    }
    // One Durable Object per room: the room id IS the name, so both ends of the same
    // pairing land in the same object wherever in the world they dial from.
    const id = env.ROOM.idFromName(room);
    return env.ROOM.get(id).fetch(request);
  },
};

export class Room {
  constructor(state) {
    this.state = state;
    // Hibernation - the reason acceptWebSocket is used below rather than ws.accept():
    // the sockets survive this object going to sleep, so an attached house that nobody
    // is using costs nothing. The live sockets are asked for where they are needed
    // (getWebSockets) rather than cached here, because a woken object runs this
    // constructor again and anything kept here would just be a stale copy.
  }

  async fetch(request) {
    const url = new URL(request.url);
    const role = pick(request, url, "role");
    const key = pick(request, url, "key");

    // Hashed BEFORE the room is read, which is the whole point of the order.
    // Cloudflare's input gates stop other events interleaving *during a storage
    // call*; an await on anything else - crypto.subtle here - is an ordinary gap.
    // With the hash computed in that gap, two hosts racing a fresh room could both
    // read `undefined`, both hash, and both write: the second would take a room the
    // first already owned. Hash first and there is no await left between the read
    // and the write, so the gate holds and the first claim stands.
    const hash = key ? await sha256hex(key) : "";
    const claimed = await this.state.storage.get("key");
    if (role === "host") {
      if (!key) return new Response("a host needs a key", { status: 403 });
      if (claimed === undefined) {
        await this.state.storage.put("key", hash);
      } else if (!timingSafeEqual(claimed, hash)) {
        // A room belongs to whoever registered it first, and only to them.
        say("refused", { role, reason: "wrong-key" });
        return new Response("room refused", { status: 403 });
      }
      // A reconnecting house replaces the old one: a laptop that slept leaves a socket
      // the other side has not noticed is dead yet.
      for (const ws of this.state.getWebSockets("host")) {
        try { ws.close(1012, "replaced"); } catch (e) { /* already gone */ }
      }
    } else {
      // A guest needs no key - the room id is the secret, and a guest who guesses one
      // still meets a TLS endpoint that will not talk to it.
      if (claimed === undefined) {
        say("refused", { role, reason: "no-such-room" });
        return new Response("no host here", { status: 403 });
      }
      if (this.state.getWebSockets("host").length === 0) {
        say("refused", { role, reason: "computer-not-attached" });
        return new Response("the computer is not attached", { status: 409 });
      }
      if (this.state.getWebSockets("guest").length >= GUEST_MAX) {
        say("refused", { role, reason: "room-full", guests: GUEST_MAX });
        return new Response("too many phones in this room", { status: 429 });
      }
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.state.acceptWebSocket(server, [role]);
    say("attached", { role, guests: this.state.getWebSockets("guest").length });
    return new Response(null, { status: 101, webSocket: client });
  }

  // Forwarding, and nothing else. Frames are opaque: [kind][stream][payload] is the
  // phone's and the computer's business, not ours.
  webSocketMessage(ws, message) {
    const tags = this.state.getTags(ws);
    const fromHost = tags.includes("host");
    for (const peer of this.state.getWebSockets(fromHost ? "guest" : "host")) {
      try {
        peer.send(message);
      } catch (e) {
        try { peer.close(1011, "send failed"); } catch (e2) { /* gone */ }
      }
    }
  }

  webSocketClose(ws) {
    // The house going away takes its guests with it: a phone still holding a socket to a
    // room with nobody home would sit there waiting for frames that cannot come.
    if (this.state.getTags(ws).includes("host")) {
      for (const g of this.state.getWebSockets("guest")) {
        try { g.close(1001, "host gone"); } catch (e) { /* gone */ }
      }
    }
  }

  webSocketError(ws) {
    try { ws.close(1011, "error"); } catch (e) { /* gone */ }
  }
}

// What this is allowed to write down.
//
// Structured so it can be queried, and deliberately thin: a role, an outcome, a
// count. No room id, no key, no address. The point of this rendezvous is that it
// cannot tell you who met here, and a log that answered that question would be the
// same leak as reading the traffic, arriving by a more respectable route.
function say(event, fields) {
  try {
    console.log(JSON.stringify({ event, ...fields }));
  } catch (e) { /* a log must never be the reason a phone cannot connect */ }
}

async function sha256hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Same care as the Python: the time this takes must not say how much of the key was
// right. Both inputs here are fixed-length hex digests, so length never leaks either.
function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
