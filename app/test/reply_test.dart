// Reading a reply back out of a message.
//
// There is no "in reply to" field in the protocol - the quote IS the protocol, which is
// why the agent understands one without being taught anything. That makes this a parser
// whose input is written by an agent as often as by a person, so the ways it must NOT
// fire matter as much as the ways it must: a message that merely opens with a quotation
// is not a reply, and drawing it as one would attach an answer to something nobody said.

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/main.dart';

void main() {
  test('a quoted message is a reply, and the answer is what is left', () {
    final r = splitReply('> **Yo:** ¿cuándo sale el tren?\n\nA las ocho.');
    expect(r, isNotNull);
    expect(r!.$1, 'Yo');
    expect(r.$2, '¿cuándo sale el tren?');
    expect(r.$3, 'A las ocho.');
  });

  test('the agent replying to itself reads the same way', () {
    final r = splitReply('> **Daneel:** lo dejé a medias\n\nYa está terminado.');
    expect(r!.$1, 'Daneel');
    expect(r.$3, 'Ya está terminado.');
  });

  test('a long quote that was cut still points at the message', () {
    final r = splitReply('> **Yo:** esto es muy largo y fue recortado…\n\nvale');
    expect(r!.$2.endsWith('…'), isTrue);
  });

  test('a reply with nothing after it is still a reply', () {
    final r = splitReply('> **Yo:** ¿sí o no?');
    expect(r, isNotNull);
    expect(r!.$3, '');
  });

  group('the ways it must not fire', () {
    test('an ordinary quotation is not a reply', () {
      expect(splitReply('> como dijo Borges, el tiempo es un río'), isNull);
    });

    test('a quote with no name is not a reply', () {
      expect(splitReply('> **negrita** al principio\n\ny luego texto'), isNull);
    });

    test('an empty quote is not a reply', () {
      expect(splitReply('> **Yo:**\n\nnada'), isNull);
    });

    test('a bold opening is not a quote at all', () {
      expect(splitReply('**Yo:** no empecé con mayor que'), isNull);
    });

    test('a quote in the middle is left alone', () {
      expect(splitReply('mira esto\n\n> **Yo:** algo'), isNull);
    });

    test('plain text is plain text', () {
      expect(splitReply('hola, ¿qué tal?'), isNull);
    });

    test('empty input does not throw', () {
      expect(splitReply(''), isNull);
    });
  });
}
