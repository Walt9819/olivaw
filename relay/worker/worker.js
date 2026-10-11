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

    const role = url.searchParams.get("role") || "";
    const room = (url.searchParams.get("room") || "").slice(0, ROOM_MAX);
    const key = url.searchParams.get("key") || "";
    if ((role !== "host" && role !== "guest") || !room) {
      return new Response("bad request", { status: 400 });
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
    // Hibernation: the sockets survive the object going to sleep, so an attached house
    // that nobody is using costs nothing. Without it, an idle connection would be billed
    // for every second it stayed open.
    this.sessions = state.getWebSockets();
  }

  async fetch(request) {
    const url = new URL(request.url);
    const role = url.searchParams.get("role");
    const key = url.searchParams.get("key") || "";

    const claimed = await this.state.storage.get("key");
    if (role === "host") {
      if (!key) return new Response("a host needs a key", { status: 403 });
      const hash = await sha256hex(key);
      if (claimed === undefined) {
        await this.state.storage.put("key", hash);
      } else if (!timingSafeEqual(claimed, hash)) {
        // A room belongs to whoever registered it first, and only to them.
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
      if (claimed === undefined) return new Response("no host here", { status: 403 });
      if (this.state.getWebSockets("host").length === 0) {
        return new Response("the computer is not attached", { status: 409 });
      }
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.state.acceptWebSocket(server, [role]);
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
