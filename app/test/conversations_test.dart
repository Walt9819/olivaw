// What a conversation is called in the list, and when it happened.
//
// Neither is decoration: an agent has hundreds of these and Hermes names almost none of
// them, so the line a person actually reads is built here out of the first thing that
// was said. Getting it wrong means a sheet of rows that all look the same.

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/conversations.dart';
import 'package:olivaw/strings.dart';

void main() {
  setUp(() => setLanguage(Lang.es));

  group('what a conversation is called', () {
    test('its own name, when it has one', () {
      expect(conversationTitle({'title': 'Informe de ventas', 'preview': 'hola'}),
          'Informe de ventas');
    });

    test('otherwise the first thing said in it', () {
      expect(conversationTitle({'title': null, 'preview': 'Te paso el informe'}),
          'Te paso el informe');
    });

    test('on one line, however it was typed', () {
      // A preview comes out of the transcript with its newlines and its indentation
      // still in it, and a ListTile given that draws one line of it and hides the rest.
      expect(conversationTitle({'preview': 'Hola,\n\n   ¿me pasas   el informe?'}),
          'Hola, ¿me pasas el informe?');
    });

    test('cut where it stops being a title', () {
      final long = conversationTitle({'preview': 'a' * 200});
      expect(long.length, 71); // 70 and the ellipsis
      expect(long.endsWith('…'), isTrue);
    });

    test('and named when there is nothing to name it with', () {
      expect(conversationTitle({'title': '  ', 'preview': ''}), 'Sin título');
    });
  });

  group('when it happened', () {
    int agoSecs(Duration d) =>
        (DateTime.now().subtract(d).millisecondsSinceEpoch / 1000).round();

    test('minutes, while it is still minutes', () {
      // Plus a few seconds: inMinutes truncates, and "exactly 7 minutes ago" is
      // 6.999 by the time the code reads the clock.
      final s = conversationWhen(
          {'last_active': agoSecs(const Duration(minutes: 7, seconds: 5))});
      expect(s, contains('hace 7 min'));
    });

    test('a date once it is old, not "hace 400 d"', () {
      final s = conversationWhen({'last_active': agoSecs(const Duration(days: 400))});
      expect(s, contains('/'));
      expect(s, isNot(contains('hace')));
    });

    test('says where it happened, because the same agent is in both places', () {
      final here = conversationWhen({'last_active': agoSecs(const Duration(hours: 2))});
      final there = conversationWhen({
        'last_active': agoSecs(const Duration(hours: 2)),
        'source': 'telegram',
      });
      expect(here, contains('Olivaw'));
      expect(there, contains('Telegram'));
    });

    test('and how much was said, when anything was', () {
      final s = conversationWhen(
          {'last_active': agoSecs(const Duration(hours: 2)), 'message_count': 14});
      expect(s, contains('14 mensajes'));
      // A conversation with no count is not a conversation with zero messages.
      expect(conversationWhen({'last_active': agoSecs(const Duration(hours: 2))}),
          isNot(contains('0 mensajes')));
    });

    test('a row with no time at all still says something', () {
      expect(conversationWhen({'source': 'telegram'}), 'Telegram');
    });
  });
}
