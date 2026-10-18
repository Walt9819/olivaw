// Reaching the computer when this phone is not on its wifi.
//
// The computer cannot be dialled - it is behind a router, like this phone is behind the
// carrier's. So both dial out to a rendezvous, which introduces them and forwards bytes
// it cannot read: the TLS session is still between this phone and that computer, against
// the same pinned certificate as at home. The rendezvous has no key and never sees one.
//
// Framing matches relay_client.py exactly: [1 byte kind][4 bytes stream][payload],
// kinds 1 open, 2 data, 3 close.
//
// One wrinkle worth knowing about: Dart can only start TLS on a real Socket, and there is
// no way to hand `SecureSocket.secure` an arbitrary pair of streams. So this opens a
// listener on 127.0.0.1 inside the app and pipes through it. The loopback hop never
// leaves the phone, and what travels over it is already the encrypted session.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

const int _kOpen = 1, _kData = 2, _kClose = 3;

class Relay {
  Relay(this.url, this.room);

  final String url;
  final String room;

  Socket? _sock;
  ServerSocket? _loop;
  final Map<int, Socket> _streams = {};
  final List<Completer<Socket>> _waiting = [];
  // Not 1. The room belongs to the COMPUTER, and every phone paired to it meets
  // there - so a second phone, or this one reconnecting before the rendezvous has
  // noticed the old socket is dead, would be opening stream 1 while stream 1 is
  // already somebody else's. The house keys its streams by this number alone and
  // would hand the second one the first one's socket. Starting at a random point
  // in a 32-bit space makes two live phones collide about as often as they collide
  // on the pairing words, and costs nothing.
  int _next = 1 + Random.secure().nextInt(0x3fffffff);
  final BytesBuilder _buf = BytesBuilder();
  bool _handshook = false;
  Future<void>? _connecting;

  /// When something last arrived from the rendezvous, and the timer that cares.
  ///
  /// A phone that changes network - wifi to mobile data, or one mast to the next -
  /// does not get an error on the socket it was holding. The old one simply stops
  /// carrying anything, in both directions, for ever: writes are accepted by a kernel
  /// that has nowhere to send them, and reads never return. Nothing here would have
  /// noticed, so the connection was kept and every later request was posted into it.
  ///
  /// The rendezvous answers a WebSocket ping with a pong in about 40ms (measured
  /// against the live one), so silence after a ping is a real answer: this connection
  /// is gone, drop it and let the next request build a new one.
  DateTime _heard = DateTime.now();
  Timer? _beat;

  /// How long a quiet connection goes before it is prodded, and before it is given up
  /// on. Fields rather than constants so a test can run the same logic in milliseconds
  /// instead of in half a minute.
  static Duration tick = const Duration(seconds: 5);
  static Duration quietBeforePing = const Duration(seconds: 20);
  static Duration quietBeforeDead = const Duration(seconds: 35);

  /// How long one request waits for its own stream to be handed to it. Short, because
  /// everything it waits on is inside this phone; a field for the same reason as the
  /// three above.
  static Duration streamWait = const Duration(seconds: 5);

  bool get connected => _sock != null && _handshook;

  Future<void> connect() => _connecting ??= _connect();

  Future<void> _connect() async {
    final u = Uri.parse(url);
    final secure = u.scheme == 'wss';
    final port = u.hasPort ? u.port : (secure ? 443 : 80);
    final sock = secure
        ? await SecureSocket.connect(u.host, port,
            timeout: const Duration(seconds: 15))
        : await Socket.connect(u.host, port, timeout: const Duration(seconds: 15));
    final key = base64.encode(
      List<int>.generate(16, (_) => Random.secure().nextInt(256)),
    );
    final path = '${u.path.isEmpty ? '' : u.path}/attach';
    sock.add(utf8.encode([
      'GET $path HTTP/1.1',
      'Host: ${u.host}',
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Key: $key',
      // In headers rather than the query string. A URL is the part of a request
      // that everything logs, and the room id is the one thing that says which
      // computer this is. The rendezvous still reads the old query form, so a
      // phone older than this one keeps working.
      'X-Olivaw-Role: guest',
      'X-Olivaw-Room: $room',
      // A CDN in front of the rendezvous may refuse a handshake that carries no
      // User-Agent. Cloudflare answers one with 530 and a body of "error code: 1010",
      // and the only symptom at this end is a rendezvous that will not let the phone
      // in, with nothing said about why. Named rather than browser-shaped: the point is
      // to have one, not to pretend to be Chrome.
      'User-Agent: Olivaw/1.0 (Android)',
      'Sec-WebSocket-Version: 13',
      '',
      '',
    ].join('\r\n')));
    await sock.flush();
    _sock = sock;
    final ready = Completer<void>();
    sock.listen(
      (chunk) => _onData(chunk, ready),
      onError: (e) => _down('$e'),
      onDone: () => _down(''),
      cancelOnError: true,
    );
    // Bound AND listened to together, and only when there is not one already.
    //
    // `_loop` outlives a connection on purpose - it is this app's own doorway and the
    // streams piped through it are what make TLS possible over a websocket - but
    // listening to it a second time throws "Stream was already listened to". Which
    // meant that the FIRST time this connection was rebuilt, rebuilding it threw: a
    // phone whose rendezvous socket had dropped for any reason at all - changed
    // network, a mast handover, the rendezvous closing an idle room - could not make
    // another one for as long as the app stayed open.
    if (_loop == null) {
      _loop = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      _loop!.listen((peer) {
        // Skipping the ones that gave up while waiting: a completer is removed by
        // the request that abandoned it, and this is the other side of that race.
        while (_waiting.isNotEmpty) {
          final next = _waiting.removeAt(0);
          if (next.isCompleted) continue;
          next.complete(peer);
          return;
        }
        peer.destroy();
      });
    }
    await ready.future.timeout(
      const Duration(seconds: 15),
      onTimeout: () => throw const SocketException('El punto de encuentro no respondió.'),
    );
  }

  void _beats() {
    _beat?.cancel();
    _beat = Timer.periodic(tick, (_) {
      if (_sock == null) return;
      final quiet = DateTime.now().difference(_heard);
      if (quiet > quietBeforeDead) {
        // Asked, and nothing came back. This is the network the phone used to be on.
        _down('sin respuesta');
        return;
      }
      if (quiet > quietBeforePing) _frame(0x9, const []);
    });
  }

  void _onData(Uint8List chunk, Completer<void> ready) {
    _heard = DateTime.now();
    _buf.add(chunk);
    if (!_handshook) {
      final data = _buf.toBytes();
      final sep = _indexOf(data, utf8.encode('\r\n\r\n'));
      if (sep < 0) return;
      final head = utf8.decode(data.sublist(0, sep), allowMalformed: true);
      if (!head.contains(' 101 ')) {
        _down('El punto de encuentro no aceptó la conexión.');
        if (!ready.isCompleted) {
          ready.completeError(
            SocketException(head.split('\r\n').first),
          );
        }
        return;
      }
      _handshook = true;
      _beats();
      _buf.clear();
      _buf.add(data.sublist(sep + 4));
      if (!ready.isCompleted) ready.complete();
    }
    while (true) {
      final data = _buf.toBytes();
      if (data.length < 2) return;
      final op = data[0] & 0x0f;
      var len = data[1] & 0x7f;
      final masked = (data[1] & 0x80) != 0;
      var at = 2;
      if (len == 126) {
        if (data.length < 4) return;
        len = (data[2] << 8) | data[3];
        at = 4;
      } else if (len == 127) {
        if (data.length < 10) return;
        len = 0;
        for (var i = 2; i < 10; i++) {
          len = (len << 8) | data[i];
        }
        at = 10;
      }
      final maskKey = masked ? data.sublist(at, at + 4) : const <int>[];
      if (masked) at += 4;
      if (data.length < at + len) return;
      var payload = data.sublist(at, at + len);
      _buf.clear();
      _buf.add(data.sublist(at + len));
      if (masked) {
        payload = Uint8List.fromList([
          for (var i = 0; i < payload.length; i++) payload[i] ^ maskKey[i % 4],
        ]);
      }
      if (op == 0x8) {
        _down('');
        return;
      }
      if (op == 0x9) {
        _frame(0xa, payload);
        continue;
      }
      if (op != 0x2 || payload.length < 5) continue;
      final kind = payload[0];
      final sid = (payload[1] << 24) | (payload[2] << 16) | (payload[3] << 8) | payload[4];
      final peer = _streams[sid];
      if (peer == null) continue;
      if (kind == _kData) {
        try {
          peer.add(payload.sublist(5));
        } catch (_) {}
      } else if (kind == _kClose) {
        _streams.remove(sid);
        peer.destroy();
      }
    }
  }

  void _frame(int op, List<int> payload) {
    final sock = _sock;
    if (sock == null) return;
    final mask = List<int>.generate(4, (_) => Random.secure().nextInt(256));
    final out = BytesBuilder()..addByte(0x80 | op);
    final n = payload.length;
    if (n < 126) {
      out.addByte(0x80 | n);
    } else if (n < 65536) {
      out
        ..addByte(0x80 | 126)
        ..addByte((n >> 8) & 0xff)
        ..addByte(n & 0xff);
    } else {
      out.addByte(0x80 | 127);
      for (var i = 7; i >= 0; i--) {
        out.addByte((n >> (8 * i)) & 0xff);
      }
    }
    out.add(mask);
    for (var i = 0; i < n; i++) {
      out.addByte(payload[i] ^ mask[i % 4]);
    }
    try {
      sock.add(out.toBytes());
    } catch (e) {
      _down('$e');
    }
  }

  static Uint8List _head(int kind, int sid) => Uint8List.fromList([
        kind,
        (sid >> 24) & 0xff,
        (sid >> 16) & 0xff,
        (sid >> 8) & 0xff,
        sid & 0xff,
      ]);

  /// A socket whose bytes come out beside the computer. Wrap it in TLS as usual.
  Future<Socket> openStream() async {
    await connect();
    final sid = _next++;
    final wait = Completer<Socket>();
    _waiting.add(wait);
    Socket local;
    Socket peer;
    try {
      local = await Socket.connect(
        InternetAddress.loopbackIPv4,
        _loop!.port,
        timeout: streamWait,
      );
      peer = await wait.future.timeout(streamWait);
    } catch (e) {
      // Two things have to happen here, and neither used to.
      //
      // The half-opened request has to let go of its place in the queue: the next
      // request's loopback connection is handed to whoever is at the front of it, so
      // a completer left behind after a timeout was given the NEXT request's socket,
      // and that one waited for a socket that had already been given away. One
      // timeout and every request after it was answered with somebody else's stream,
      // for as long as the app stayed open.
      //
      // And the connection itself has to go. A request that got no answer from the
      // rendezvous is the evidence that this socket no longer leads anywhere - which
      // after a change of network is exactly what has happened.
      _waiting.remove(wait);
      _down('$e');
      rethrow;
    }
    _streams[sid] = peer;
    _frame(0x2, _head(_kOpen, sid));
    peer.listen(
      (data) => _frame(0x2, [..._head(_kData, sid), ...data]),
      onDone: () {
        _streams.remove(sid);
        _frame(0x2, _head(_kClose, sid));
      },
      onError: (_) {
        _streams.remove(sid);
        _frame(0x2, _head(_kClose, sid));
      },
    );
    return local;
  }

  void _down(String why) {
    _handshook = false;
    _connecting = null;
    _beat?.cancel();
    _beat = null;
    // Over a copy: destroying a stream runs its own onDone, which removes it from
    // this very map, and iterating it while that happens throws "Concurrent
    // modification during iteration" - out of the one function whose whole job is to
    // leave the transport in a state something else can rebuild from.
    for (final s in _streams.values.toList()) {
      s.destroy();
    }
    _streams.clear();
    for (final w in _waiting) {
      if (!w.isCompleted) w.completeError(SocketException(why.isEmpty ? 'cerrado' : why));
    }
    _waiting.clear();
    _sock?.destroy();
    _sock = null;
    _buf.clear();
  }

  void close() {
    _down('');
    _loop?.close();
    _loop = null;
  }

  static int _indexOf(List<int> hay, List<int> needle) {
    for (var i = 0; i + needle.length <= hay.length; i++) {
      var hit = true;
      for (var j = 0; j < needle.length; j++) {
        if (hay[i + j] != needle[j]) {
          hit = false;
          break;
        }
      }
      if (hit) return i;
    }
    return -1;
  }
}
