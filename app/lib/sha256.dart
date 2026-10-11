// SHA-256, because Dart's core libraries do not have it and this app does not take
// dependencies. FIPS 180-4, written out plainly; `selfTest` checks it against the
// published vectors at startup, which is the only honest way to ship your own hash.
//
// It is used for exactly one thing: turning the certificate this phone was handed into
// the two words the computer is showing on its screen, so a person can see that the two
// are the same certificate. See Link.shortCode.

import 'dart:typed_data';

const List<int> _k = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

int _rotr(int x, int n) => ((x >> n) | (x << (32 - n))) & 0xffffffff;

/// The digest of [data], 32 bytes.
Uint8List sha256(List<int> data) {
  final h = <int>[
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];

  // Pad: a 1 bit, then zeros, then the length in bits as a 64-bit big-endian number.
  final bitLen = data.length * 8;
  final padded = BytesBuilder()
    ..add(data)
    ..addByte(0x80);
  while ((padded.length % 64) != 56) {
    padded.addByte(0);
  }
  final tail = ByteData(8)..setUint64(0, bitLen);
  padded.add(tail.buffer.asUint8List());
  final msg = padded.toBytes();

  final w = Int32List(64);
  for (var i = 0; i < msg.length; i += 64) {
    for (var t = 0; t < 16; t++) {
      final o = i + t * 4;
      w[t] = (msg[o] << 24) | (msg[o + 1] << 16) | (msg[o + 2] << 8) | msg[o + 3];
    }
    for (var t = 16; t < 64; t++) {
      final a = w[t - 15] & 0xffffffff;
      final b = w[t - 2] & 0xffffffff;
      final s0 = _rotr(a, 7) ^ _rotr(a, 18) ^ (a >> 3);
      final s1 = _rotr(b, 17) ^ _rotr(b, 19) ^ (b >> 10);
      w[t] = ((w[t - 16] & 0xffffffff) + s0 + (w[t - 7] & 0xffffffff) + s1) & 0xffffffff;
    }
    var a = h[0], b = h[1], c = h[2], d = h[3];
    var e = h[4], f = h[5], g = h[6], hh = h[7];
    for (var t = 0; t < 64; t++) {
      final s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25);
      final ch = (e & f) ^ (~e & g);
      final t1 = (hh + s1 + ch + _k[t] + (w[t] & 0xffffffff)) & 0xffffffff;
      final s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22);
      final maj = (a & b) ^ (a & c) ^ (b & c);
      final t2 = (s0 + maj) & 0xffffffff;
      hh = g;
      g = f;
      f = e;
      e = (d + t1) & 0xffffffff;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) & 0xffffffff;
    }
    final next = [a, b, c, d, e, f, g, hh];
    for (var t = 0; t < 8; t++) {
      h[t] = (h[t] + next[t]) & 0xffffffff;
    }
  }

  final out = Uint8List(32);
  for (var i = 0; i < 8; i++) {
    out[i * 4] = (h[i] >> 24) & 0xff;
    out[i * 4 + 1] = (h[i] >> 16) & 0xff;
    out[i * 4 + 2] = (h[i] >> 8) & 0xff;
    out[i * 4 + 3] = h[i] & 0xff;
  }
  return out;
}

String hex(List<int> bytes) =>
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

/// The three vectors every SHA-256 implementation is checked against. Called once when
/// the app starts: a hash that is quietly wrong would make every certificate look like
/// an impostor's, which is a confusing way to find out.
bool sha256SelfTest() {
  const cases = {
    '': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'abc': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq':
        '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
  };
  for (final entry in cases.entries) {
    if (hex(sha256(entry.key.codeUnits)) != entry.value) return false;
  }
  // And one long one, where the padding crosses a block boundary.
  return hex(sha256(List<int>.filled(1000000, 0x61))) ==
      'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0';
}
