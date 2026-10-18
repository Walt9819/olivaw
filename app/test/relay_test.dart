// Changing network, from the transport's point of view.
//
// A phone that moves from wifi to mobile data, or from one mast to the next, does not
// get an error on the socket it was holding. The socket simply stops carrying anything
// in either direction, for ever: writes are accepted by a kernel with nowhere to send
// them, reads never return. Everything in here is about noticing that and recovering,
// because the symptom when nothing does is the one the owner reported - the app works,
// the network changes, and from then on it never talks to its computer again.
//
// A real rendezvous on loopback rather than a mock: the thing under test is socket
// behaviour, and a mock of a socket would be a mock of the answer.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/relay.dart';

/// A rendezvous that completes the handshake and then does as it is told.
class FakeRendezvous {
  FakeRendezvous({this.answerPings = true});

  final bool answerPings;
  late ServerSocket _server;
  final List<Socket> sockets = [];

  /// How many phones have connected since it started. The number that matters: a
  /// transport that recovered built a SECOND one.
  int get connections => sockets.length;

  /// Frames the rendezvous has been sent, by opcode.
  final List<int> seenOpcodes = [];

  String get url => 'ws://127.0.0.1:${_server.port}';

  Future<void> start() async {
    _server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((sock) {
      sockets.add(sock);
      var shook = false;
      sock.listen((data) {
        if (!shook) {
          shook = true;
          sock.add(utf8.encode('HTTP/1.1 101 Switching Protocols\r\n'
              'Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n'));
          return;
        }
        if (data.length >= 2) {
          final op = data[0] & 0x0f;
          seenOpcodes.add(op);
          // A pong, if this rendezvous is one that answers. The silent one is a
          // phone that has changed network: the bytes go nowhere and nothing
          // comes back.
          if (op == 0x9 && answerPings) {
            sock.add(Uint8List.fromList([0x8a, 0x00]));
          }
        }
      }, onError: (_) {}, cancelOnError: true);
    });
  }

  Future<void> stop() async {
    for (final s in sockets) {
      s.destroy();
    }
    await _server.close();
  }
}

void main() {
  late FakeRendezvous rv;

  setUp(() {
    // The same logic, in milliseconds instead of in half a minute.
    Relay.tick = const Duration(milliseconds: 30);
    Relay.quietBeforePing = const Duration(milliseconds: 60);
    Relay.quietBeforeDead = const Duration(milliseconds: 220);
    Relay.streamWait = const Duration(seconds: 5);
  });

  tearDown(() async {
    Relay.tick = const Duration(seconds: 5);
    Relay.quietBeforePing = const Duration(seconds: 20);
    Relay.quietBeforeDead = const Duration(seconds: 35);
    await rv.stop();
  });

  test('a connection that goes quiet is given up on, not kept', () async {
    // The rendezvous that never answers IS the old network: still a socket, still
    // writable, carrying nothing.
    rv = FakeRendezvous(answerPings: false);
    await rv.start();
    final relay = Relay(rv.url, 'sala');
    await relay.connect();
    expect(relay.connected, isTrue);

    // It is prodded first, and only then given up on.
    await Future<void>.delayed(const Duration(milliseconds: 150));
    expect(rv.seenOpcodes, contains(0x9), reason: 'nothing ever asked');
    expect(relay.connected, isTrue, reason: 'given up on before it was asked');

    await Future<void>.delayed(const Duration(milliseconds: 250));
    expect(relay.connected, isFalse,
        reason: 'a socket that answers nothing was kept');
    relay.close();
  });

  test('and the next request builds a new one', () async {
    rv = FakeRendezvous(answerPings: false);
    await rv.start();
    final relay = Relay(rv.url, 'sala');
    await relay.connect();
    expect(rv.connections, 1);

    await Future<void>.delayed(const Duration(milliseconds: 400));
    expect(relay.connected, isFalse);

    // The whole point of noticing: what comes next works.
    await relay.connect();
    expect(relay.connected, isTrue);
    expect(rv.connections, 2, reason: 'the dead socket was handed out again');
    relay.close();
  });

  test('a connection that answers is left alone', () async {
    // The other half of the rule, and the one that would hurt most if it were wrong:
    // a keepalive that tore down healthy connections would turn a working app into
    // this same bug, every thirty-five seconds.
    rv = FakeRendezvous();
    await rv.start();
    final relay = Relay(rv.url, 'sala');
    await relay.connect();

    await Future<void>.delayed(const Duration(milliseconds: 500));
    expect(relay.connected, isTrue, reason: 'a live connection was dropped');
    expect(rv.connections, 1);
    expect(rv.seenOpcodes.where((o) => o == 0x9).length, greaterThan(1),
        reason: 'it stopped asking');
    relay.close();
  });

  test('streams are not handed to a request that has already given up', () async {
    // The failure this prevents outlives the network change that caused it: one
    // abandoned request left its place in the queue, so the NEXT request's socket was
    // given to it, and that one waited for a socket already given away. Every request
    // after the first timeout was answered with somebody else's stream, until the app
    // was restarted.
    rv = FakeRendezvous();
    await rv.start();
    final relay = Relay(rv.url, 'sala');
    await relay.connect();

    final first = await relay.openStream();
    final second = await relay.openStream();
    expect(first.port, isNot(second.port),
        reason: 'two requests were given the same stream');
    first.destroy();
    second.destroy();
    relay.close();
  });

  test('a request that gives up takes the dead connection with it', () async {
    // The moment the owner's bug begins: one request gets no stream, because the
    // socket it would have travelled on belongs to a network this phone has left.
    // What happens NEXT is the whole question - the abandoned request must let go of
    // its place in the queue, and the connection must be dropped rather than handed
    // to everything that follows.
    rv = FakeRendezvous();
    await rv.start();
    final relay = Relay(rv.url, 'sala');
    await relay.connect();
    expect(rv.connections, 1);

    Relay.streamWait = Duration.zero;
    await expectLater(relay.openStream(), throwsA(anything));
    expect(relay.connected, isFalse,
        reason: 'the connection that failed a request was kept');

    // And the next request is served properly, on a connection built after it.
    Relay.streamWait = const Duration(seconds: 5);
    final after = await relay.openStream();
    expect(rv.connections, 2);
    expect(after.remoteAddress.address, '127.0.0.1');
    after.destroy();
    relay.close();
  });

  test('a rendezvous that is not there at all is an error, not a hang', () async {
    rv = FakeRendezvous();
    await rv.start();
    final dead = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final port = dead.port;
    await dead.close();   // nothing listens there now

    final relay = Relay('ws://127.0.0.1:$port', 'sala');
    await expectLater(relay.connect(), throwsA(isA<SocketException>()));
    expect(relay.connected, isFalse);
    relay.close();
  });
}
