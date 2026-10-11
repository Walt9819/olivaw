// Tests for the parts of this app that can be wrong silently.
//
// The hand-written SHA-256 and the two-word code are the two places where a bug would not
// look like a bug: a wrong hash makes every computer look like an impostor, and a
// shortCode that disagrees with the Python one makes an honest computer fail the only
// check a person performs during pairing. Both are pinned here against fixed values that
// were produced by the OTHER implementation, so these tests fail if either side drifts.

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';
import 'package:olivaw/main.dart';
import 'package:olivaw/sha256.dart';

void main() {
  group('sha256', () {
    test('matches the published FIPS vectors', () {
      expect(sha256SelfTest(), isTrue);
    });

    test('hashes the empty string correctly', () {
      expect(hex(sha256(const <int>[])),
          'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    });

    test('handles a length that lands exactly on a block boundary', () {
      // 55 bytes is the largest message whose padding still fits in one block and 56 is
      // the first that needs a second - the classic place a hand-written SHA-256 breaks.
      final a = sha256(List<int>.filled(55, 0x61));
      final b = sha256(List<int>.filled(56, 0x61));
      expect(a.length, 32);
      expect(b.length, 32);
      expect(hex(a), isNot(hex(b)));
    });
  });

  group('the two words shown during pairing', () {
    // These four come from selfcert.short_code in Python. If either implementation
    // changes - the word list, the prefix, the byte order - this test goes red, and that
    // is the point: the whole check depends on the two sides agreeing.
    const cases = {
      '0000000000000000000000000000000000000000000000000000000000000000':
          'DUNA-ROBLE',
      'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff':
          'GRANATE-UVA',
      'a8c7894662e695250000000000000000000000000000000000000000000000ab':
          'BRÚJULA-QUESO',
      '': 'XILÓFONO-ESPIGA',
    };

    test('agree with the computer, digest for digest', () {
      cases.forEach((fingerprint, expected) {
        expect(shortCode(fingerprint), expected, reason: 'for $fingerprint');
      });
    });

    test('two different certificates give different words', () {
      expect(shortCode('a' * 64), isNot(shortCode('b' * 64)));
    });
  });

  group('Pairing', () {
    test('survives a round trip through storage', () {
      final p = Pairing(
        name: 'Mi ordenador',
        host: '192.168.1.40',
        port: 8791,
        certDer: Uint8List.fromList([1, 2, 3, 4]),
        token: 'secreto',
        relayUrl: 'wss://relay.example/attach',
        relayRoom: 'sala',
        caps: const ['ver', 'hablar'],
      );
      final back = Pairing.fromJson(p.toJson());
      expect(back.host, p.host);
      expect(back.port, p.port);
      expect(back.token, p.token);
      expect(back.certDer, p.certDer);
      expect(back.relayUrl, p.relayUrl);
      expect(back.caps, p.caps);
      expect(back.fingerprint, p.fingerprint);
    });
  });

  testWidgets('the app starts and asks for a computer to link', (tester) async {
    await tester.pumpWidget(const OlivawApp());
    await tester.pump();
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
