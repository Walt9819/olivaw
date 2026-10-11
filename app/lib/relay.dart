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
  int _next = 1;
  final BytesBuilder _buf = BytesBuilder();
  bool _handshook = false;
  Future<void>? _connecting;

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
    final path = '${u.path.isEmpty ? '' : u.path}/attach'
        '?role=guest&room=${Uri.encodeComponent(room)}';
    sock.add(utf8.encode([
      'GET $path HTTP/1.1',
      'Host: ${u.host}',
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Key: $key',
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
    _loop ??= await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    _loop!.listen((peer) {
      if (_waiting.isEmpty) {
        peer.destroy();
        return;
      }
      _waiting.removeAt(0).complete(peer);
    });
    await ready.future.timeout(
      const Duration(seconds: 15),
      onTimeout: () => throw const SocketException('El punto de encuentro no respondió.'),
    );
  }

  void _onData(Uint8List chunk, Completer<void> ready) {
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
    final local = await Socket.connect(
      InternetAddress.loopbackIPv4,
      _loop!.port,
      timeout: const Duration(seconds: 5),
    );
    final peer = await wait.future.timeout(const Duration(seconds: 5));
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
    for (final s in _streams.values) {
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
