// The connection to a computer running Olivaw.
//
// Two things make this different from an ordinary app talking to an ordinary API, and
// both of them are the point:
//
// 1. **The certificate is the identity.** There is no public domain and no certificate
//    authority: the computer signs its own, and this app accepts *that one certificate
//    and nothing else*, by remembering its exact bytes the first time and comparing them
//    on every connection afterwards. A CA vouches for a name and anybody who fools the CA
//    gets in; pinning vouches for a key, and there is nothing to fool.
//
// 2. **The same connection works from outside the house**, unchanged. Away from the wifi
//    the bytes travel through a rendezvous that has no key and cannot read them - the TLS
//    session is still between this phone and that computer. Which is why `_open` differs
//    between the two cases by one socket and nothing else.
//
// Written against dart:io alone - no HTTP package, no WebSocket package - because
// pinning a certificate and tunnelling through the rendezvous both need the socket
// itself, and because this app does not take dependencies.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'relay.dart';
import 'sha256.dart';

/// A saved computer.
class Pairing {
  Pairing({
    required this.name,
    required this.host,
    required this.port,
    required this.certDer,
    required this.token,
    this.relayUrl = '',
    this.relayRoom = '',
    this.caps = const [],
  });

  String name;
  String host;
  int port;
  Uint8List certDer;
  String token;
  String relayUrl;
  String relayRoom;
  List<String> caps;

  String get fingerprint => hex(sha256(certDer));

  Map<String, dynamic> toJson() => {
        'name': name,
        'host': host,
        'port': port,
        'cert': base64.encode(certDer),
        'token': token,
        'relayUrl': relayUrl,
        'relayRoom': relayRoom,
        'caps': caps,
      };

  static Pairing fromJson(Map<String, dynamic> j) => Pairing(
        name: j['name'] ?? 'Mi ordenador',
        host: j['host'] ?? '',
        port: j['port'] ?? 8791,
        certDer: base64.decode(j['cert'] ?? ''),
        token: j['token'] ?? '',
        relayUrl: j['relayUrl'] ?? '',
        relayRoom: j['relayRoom'] ?? '',
        caps: (j['caps'] as List?)?.cast<String>() ?? const [],
      );
}

/// Two words from a fingerprint. Must agree with selfcert.short_code on the computer -
/// that agreement is the whole mechanism, so the list below is copied, not re-derived.
const List<String> _words = [
  'ÁGUILA', 'BOSQUE', 'CANELA', 'DELFÍN', 'ESTAÑO', 'FAROL', 'GAVIOTA',
  'HIEDRA', 'ÍNDIGO', 'JAZMÍN', 'LADRILLO', 'MANZANA', 'NUBE', 'OLIVO',
  'PÁJARO', 'QUESO', 'ROBLE', 'SALMÓN', 'TIGRE', 'UVA', 'VIOLETA',
  'XILÓFONO', 'YUNQUE', 'ZORRO', 'ÁMBAR', 'BRÚJULA', 'CEDRO', 'DUNA',
  'ESPIGA', 'FÓSFORO', 'GRANATE', 'HORNO',
];

String shortCode(String fingerprint) {
  final raw = sha256(utf8.encode('olivaw-pair:$fingerprint'));
  final n = (raw[0] << 24) | (raw[1] << 16) | (raw[2] << 8) | raw[3];
  return '${_words[n % _words.length]}-${_words[(n ~/ _words.length) % _words.length]}';
}

class LinkError implements Exception {
  LinkError(this.message);
  final String message;
  @override
  String toString() => message;
}

/// One request/response, or one upgraded stream, against a paired computer.
class Link {
  Link(this.pairing);

  final Pairing pairing;

  /// Opens a TLS connection, refusing anything but the pinned certificate.
  ///
  /// [expectDer] is null only during pairing, when there is nothing to compare against
  /// yet; the caller then shows the two words and asks the person.
  Future<SecureSocket> _open({Uint8List? expectDer, bool viaRelay = false}) async {
    X509Certificate? offered;
    bool accept(X509Certificate cert) {
      offered = cert;
      if (expectDer == null) return true; // pairing: the person checks the words
      final a = cert.der, b = expectDer;
      if (a.length != b.length) return false;
      var same = 0;
      for (var i = 0; i < a.length; i++) {
        same |= a[i] ^ b[i];
      }
      return same == 0;
    }

    if (!viaRelay) {
      final s = await SecureSocket.connect(
        pairing.host,
        pairing.port,
        onBadCertificate: accept,
        timeout: const Duration(seconds: 12),
      );
      _lastCert = offered;
      return s;
    }
    // Away from home: the same TLS, over a stream the rendezvous only forwards.
    final raw = await _relayStream();
    final s = await SecureSocket.secure(raw, onBadCertificate: accept);
    _lastCert = offered;
    return s;
  }

  X509Certificate? _lastCert;
  X509Certificate? get lastCertificate => _lastCert;

  bool get canReachDirectly => pairing.host.isNotEmpty;

  /// A JSON call. [auth] false only for /ping and /pair.
  Future<Map<String, dynamic>> call(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
    Uint8List? expectDer,
    bool viaRelay = false,
  }) async {
    final socket = await _open(
      expectDer: expectDer ?? (auth ? pairing.certDer : null),
      viaRelay: viaRelay,
    );
    try {
      final payload = utf8.encode(jsonEncode(body ?? {}));
      final head = <String>[
        '$method $path HTTP/1.1',
        'Host: olivaw',
        'Content-Type: application/json',
        'Content-Length: ${payload.length}',
        'Connection: close',
        if (auth) 'Authorization: Bearer ${pairing.token}',
      ];
      socket.add(utf8.encode('${head.join('\r\n')}\r\n\r\n'));
      socket.add(payload);
      await socket.flush();
      final buf = BytesBuilder();
      await for (final chunk in socket) {
        buf.add(chunk);
      }
      return _parse(buf.toBytes());
    } finally {
      socket.destroy();
    }
  }

  Map<String, dynamic> _parse(Uint8List raw) {
    final sep = _find(raw, utf8.encode('\r\n\r\n'));
    if (sep < 0) throw LinkError('Respuesta incompleta del ordenador.');
    final headText = utf8.decode(raw.sublist(0, sep), allowMalformed: true);
    final status = int.tryParse(headText.split(' ').elementAt(1)) ?? 0;
    final bodyText = utf8.decode(raw.sublist(sep + 4), allowMalformed: true);
    Map<String, dynamic> out;
    try {
      out = jsonDecode(bodyText) as Map<String, dynamic>;
    } catch (_) {
      out = {'ok': false, 'detail': 'Respuesta ilegible.'};
    }
    out['_status'] = status;
    if (status == 401) {
      out['detail'] = 'Este teléfono ya no está enlazado con ese ordenador.';
    } else if (status == 403 && out['detail'] == null) {
      out['detail'] = 'Ese permiso no está activado para este teléfono.';
    }
    return out;
  }

  static int _find(List<int> hay, List<int> needle) {
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

  // ── the rendezvous ──────────────────────────────────
  // Away from the wifi the only difference is which socket the TLS runs on. Everything
  // above this line - the pinning, the token, the permissions - is unchanged, which is
  // the property that makes "outside the house" not a second security model.

  Relay? _relay;

  bool get hasRelay => pairing.relayUrl.isNotEmpty && pairing.relayRoom.isNotEmpty;

  Future<Socket> _relayStream() async {
    if (!hasRelay) {
      throw LinkError('Este ordenador no tiene punto de encuentro configurado.');
    }
    _relay ??= Relay(pairing.relayUrl, pairing.relayRoom);
    return _relay!.openStream();
  }

  /// Try the wifi first, fall back to the rendezvous. At home the first one wins in a
  /// few milliseconds; away from home it fails fast and the second takes over.
  Future<Map<String, dynamic>> reach(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
  }) async {
    if (canReachDirectly) {
      try {
        return await call(method, path, body: body, auth: auth);
      } on SocketException {
        if (!hasRelay) rethrow;
      } on HandshakeException {
        rethrow; // a wrong certificate is never something to retry around
      }
    }
    return call(method, path, body: body, auth: auth, viaRelay: true);
  }

  void dispose() {
    _relay?.close();
    _relay = null;
  }

  // ── the browser stream ───────────────────────────────────────────────────────────
  /// Opens the screencast socket. Yields JPEG frames and JSON events.
  Future<BrowserStream> watchBrowser(String agent, {String quality = 'normal'}) async {
    final socket = await _open(expectDer: pairing.certDer);
    final key = base64.encode(
      List<int>.generate(16, (_) => Random.secure().nextInt(256)),
    );
    final head = <String>[
      'GET /ws/browser?agent=${Uri.encodeComponent(agent)}&q=$quality HTTP/1.1',
      'Host: olivaw',
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Key: $key',
      'Sec-WebSocket-Version: 13',
      'Authorization: Bearer ${pairing.token}',
    ];
    socket.add(utf8.encode('${head.join('\r\n')}\r\n\r\n'));
    await socket.flush();
    return BrowserStream(socket);
  }
}

/// The live view of an agent's Chrome: pictures out, taps in.
class BrowserStream {
  BrowserStream(this._socket) {
    _sub = _socket.listen(_onData, onError: (e) => _close('$e'), onDone: () => _close(''));
  }

  final SecureSocket _socket;
  late final StreamSubscription<Uint8List> _sub;
  final _frames = StreamController<Uint8List>.broadcast();
  final _events = StreamController<Map<String, dynamic>>.broadcast();
  final _buf = BytesBuilder();
  bool _upgraded = false;
  bool _closed = false;

  Stream<Uint8List> get frames => _frames.stream;
  Stream<Map<String, dynamic>> get events => _events.stream;

  void _onData(Uint8List chunk) {
    _buf.add(chunk);
    var data = _buf.toBytes();
    if (!_upgraded) {
      final sep = Link._find(data, utf8.encode('\r\n\r\n'));
      if (sep < 0) return;
      final head = utf8.decode(data.sublist(0, sep), allowMalformed: true);
      if (!head.contains(' 101 ')) {
        _close('El ordenador no abrió el vídeo: ${head.split('\r\n').first}');
        return;
      }
      _upgraded = true;
      data = data.sublist(sep + 4);
      _buf.clear();
      _buf.add(data);
    }
    // Frames, as many as are complete. The server never masks, so there is no mask key.
    while (true) {
      data = _buf.toBytes();
      if (data.length < 2) return;
      final op = data[0] & 0x0f;
      var len = data[1] & 0x7f;
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
      if (data.length < at + len) return;
      final payload = data.sublist(at, at + len);
      _buf.clear();
      _buf.add(data.sublist(at + len));
      if (op == 0x2) {
        _frames.add(Uint8List.fromList(payload));
      } else if (op == 0x1) {
        try {
          _events.add(jsonDecode(utf8.decode(payload)) as Map<String, dynamic>);
        } catch (_) {}
      } else if (op == 0x8) {
        _close('');
        return;
      } else if (op == 0x9) {
        _send(0xa, payload);
      }
    }
  }

  void _send(int op, List<int> payload) {
    if (_closed) return;
    // A client always masks: RFC 6455, and the server rejects anything that does not.
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
      _socket.add(out.toBytes());
    } catch (e) {
      _close('$e');
    }
  }

  void command(Map<String, dynamic> cmd) => _send(0x1, utf8.encode(jsonEncode(cmd)));

  /// A tap, as a fraction of the picture - the phone has a scaled JPEG and cannot know
  /// how big the page is, so the computer does that conversion against the frame this
  /// phone is actually looking at.
  void tap(double nx, double ny) => command({'c': 'tap', 'x': nx, 'y': ny});
  void scroll(double nx, double ny, double dy) =>
      command({'c': 'tap', 'kind': 'scroll', 'x': nx, 'y': ny, 'dy': dy});
  void type(String text) => command({'c': 'type', 'text': text});
  void key(String name) => command({'c': 'key', 'key': name});
  void navigate(String url) => command({'c': 'nav', 'url': url});
  void arm({int seconds = 180}) => command({'c': 'arm', 'seconds': seconds});
  void disarm() => command({'c': 'disarm'});
  void refresh() => command({'c': 'refresh'});
  void quality(String q) => command({'c': 'quality', 'q': q});

  void _close(String why) {
    if (_closed) return;
    _closed = true;
    if (why.isNotEmpty) _events.add({'t': 'error', 'detail': why});
    _events.add({'t': 'closed'});
    _sub.cancel();
    _socket.destroy();
    _frames.close();
    _events.close();
  }

  void close() {
    disarm();
    _close('');
  }
}
