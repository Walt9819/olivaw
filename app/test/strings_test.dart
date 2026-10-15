// The three languages, and the one thing that must NOT be translated.

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';
import 'package:olivaw/strings.dart';

void main() {
  tearDown(() => setLanguage(Lang.es));

  test('Spanish is the identity', () {
    setLanguage(Lang.es);
    expect(tr('Tus agentes'), 'Tus agentes');
    // Even for a sentence nobody has translated.
    expect(tr('una frase que no está en la tabla'),
        'una frase que no está en la tabla');
  });

  test('English and German come back translated', () {
    setLanguage(Lang.en);
    expect(tr('Tus agentes'), 'Your agents');
    expect(tr('Hablar'), 'Talk');
    setLanguage(Lang.de);
    expect(tr('Tus agentes'), 'Deine Agenten');
    expect(tr('Hablar'), 'Sprechen');
  });

  test('a missing translation falls back to Spanish, never to a key name', () {
    const unknown = 'esto no está traducido todavía';
    for (final l in Lang.values) {
      setLanguage(l);
      expect(tr(unknown), unknown);
    }
  });

  test('placeholders are filled in every language', () {
    for (final l in Lang.values) {
      setLanguage(l);
      final out = trp('hace {0} min', [7]);
      expect(out, contains('7'));
      expect(out, isNot(contains('{0}')));
    }
  });

  test('a translation keeps every placeholder its Spanish has', () {
    // A translation that drops {0} silently loses the number it was carrying.
    for (final es in ['hace {0} min', 'hace {0} h', 'hace {0} d', '{0} mensajes',
                      'Guardado en {0}', 'Soltar el control ({0}s)']) {
      for (final l in [Lang.en, Lang.de]) {
        setLanguage(l);
        expect(tr(es), contains('{0}'), reason: '$l lost {0} in "$es"');
      }
    }
  });

  group('the pairing words are never translated', () {
    // They are compared against what the computer shows, and the computer's list is in
    // Spanish in selfcert.py. Translating one side would make every honest computer
    // look like an impostor.
    test('the same fingerprint gives the same two words in all three', () {
      const fp = 'a8c7894662e695250000000000000000000000000000000000000000000000ab';
      final es = () { setLanguage(Lang.es); return shortCode(fp); }();
      for (final l in Lang.values) {
        setLanguage(l);
        expect(shortCode(fp), es, reason: 'the code changed in $l');
      }
      expect(es, 'BRÚJULA-QUESO');
    });
  });
}
