// A message that did not get through, and the conversation waiting to be picked up.
//
// Two things that were missing from the same screen. A send that failed left the
// bubble sitting there looking exactly like one that had arrived - the words cleared
// out of the box underneath, nothing to press, and nothing to say that the agent had
// never heard it. And the offer to carry on the last conversation named no
// conversation: the only way to find out which one it was, was to open it, which is
// the one thing the offer exists to make optional.
//
// What is checked here is the part of both that can be wrong silently: whether the
// same message would be said twice, whether a hint reads as a sentence, and whether an
// unsent message blocks the offer it should be sitting underneath.

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/main.dart';
import 'package:olivaw/strings.dart';

Map<String, dynamic> said(String text, {String role = 'user'}) =>
    {'role': role, 'text': text};

void main() {
  setUp(() => setLanguage(Lang.es));

  group('saying the same thing twice', () {
    // The failure this guards is not a cosmetic duplicate. These agents send email,
    // move files and talk to people; a second copy of "manda ese correo" is a second
    // email. /api/talk/send is never repeated by the link itself for that reason, so
    // a retry is the owner's decision - and this is what keeps that decision from
    // costing them twice.

    test('a message already at the end of the conversation is not sent again', () {
      final tail = [
        said('¿Puedes revisar el correo?'),
        said('Listo, revisado.', role: 'assistant'),
      ];
      expect(saidAlready(tail, '¿Puedes revisar el correo?'), isTrue);
    });

    test('a message that is not there is sent', () {
      final tail = [said('otra cosa'), said('vale', role: 'assistant')];
      expect(saidAlready(tail, '¿Puedes revisar el correo?'), isFalse);
    });

    test('the agent saying it back is not the owner having said it', () {
      // An agent quoting the question it was asked is the ordinary shape of an
      // answer. Counting that as "already sent" would swallow the message.
      final tail = [said('manda ese correo', role: 'assistant')];
      expect(saidAlready(tail, 'manda ese correo'), isFalse);
    });

    test('a line break typed differently is still the same message', () {
      // The computer stores what it was given and hands it back dressed; trailing
      // space and the shape of a newline are not what makes two messages different.
      final tail = [said('una cosa\r\n\r\ny otra  ')];
      expect(saidAlready(tail, 'una cosa\n\ny otra'), isTrue);
    });

    test('an empty message is never recognised as already there', () {
      // Otherwise a conversation holding any empty row would swallow every retry.
      expect(saidAlready([said('')], '   '), isFalse);
    });

    test('nothing to compare against means send it', () {
      // A history that could not be read is a question that was not answered, and the
      // owner pressed a button that says send it again.
      expect(saidAlready(const [], 'hola'), isFalse);
    });
  });

  group('what the last conversation was about', () {
    test('the owner\'s own last words are marked as theirs', () {
      expect(resumeHint(said('Quedamos a las seis'), 'Hermes'),
          'Tú: Quedamos a las seis');
    });

    test('and the agent\'s carry the agent\'s name', () {
      expect(resumeHint(said('Ya está enviado', role: 'assistant'), 'Hermes'),
          'Hermes: Ya está enviado');
    });

    test('markdown is flattened into one line of plain words', () {
      // An agent's turn opens with a heading or a bullet as often as not. A hint is
      // one line read at a glance, not a document.
      expect(
          resumeHint(
              said('## Resumen\n\n- **Primero**: el correo\n- Después: la cita',
                  role: 'assistant'),
              'Hermes'),
          'Hermes: Resumen Primero: el correo Después: la cita');
    });

    test('a reply shows the answer, not what it was quoting', () {
      // The quote is the previous message. Showing it as "the last thing said" would
      // walk the hint backwards one turn every time somebody replied.
      expect(
          resumeHint(
              said('> **Yo:** ¿lo mandaste?\n\nSí, esta mañana', role: 'assistant'),
              'Hermes'),
          'Hermes: Sí, esta mañana');
    });

    test('a recording says that it is one', () {
      // Nothing was typed, so there is nothing to quote - but "nothing" is not what
      // happened in that conversation.
      expect(
          resumeHint({
            'role': 'user',
            'text': '',
            'files': [
              {'name': 'nota.m4a', 'kind': 'audio'}
            ],
          }, 'Hermes'),
          'Tú: Nota de voz');
    });

    test('a turn that is only a file says so too', () {
      expect(
          resumeHint({
            'role': 'user',
            'text': '',
            'files': [
              {'name': 'factura.pdf'}
            ],
          }, 'Hermes'),
          'Tú: Un archivo');
    });

    test('a turn with nothing in it at all produces no hint', () {
      // Better a strip that says less than one that says "Tú:" and stops.
      expect(resumeHint(said(''), 'Hermes'), '');
    });

    test('a long answer is cut rather than wrapped forever', () {
      final long = resumeHint(said('a' * 400, role: 'assistant'), 'Hermes');
      expect(long.endsWith('…'), isTrue);
      expect(long.length, lessThan(180));
    });

    test('it speaks the language the phone is in', () {
      setLanguage(Lang.de);
      expect(resumeHint(said('Bis später'), 'Hermes'), 'Du: Bis später');
      setLanguage(Lang.en);
      expect(
          resumeHint({
            'role': 'user',
            'text': '',
            'files': [
              {'name': 'nota.m4a', 'kind': 'audio'}
            ],
          }, 'Hermes'),
          'You: Voice note');
    });
  });

  group('picking the old conversation up again', () {
    test('an empty screen takes it', () {
      expect(canResumeOver(const []), isTrue);
    });

    test('a screen holding only a message that never left still takes it', () {
      // The whole point: something failed to send, the owner backed out, came back,
      // and the offer to carry on the conversation has to still be there - with the
      // unsent message underneath it, not instead of it.
      expect(
          canResumeOver([
            {'role': 'user', 'text': 'no salió', 'failed': true}
          ]),
          isTrue);
    });

    test('but not once something has actually been said here', () {
      // Dropping sixty old turns on top of a message that went through would be
      // throwing that message away.
      expect(canResumeOver([said('ya dije algo')]), isFalse);
    });

    test('one that went through among ones that did not still blocks it', () {
      expect(
          canResumeOver([
            {'role': 'user', 'text': 'esta sí', 'failed': false},
            {'role': 'user', 'text': 'esta no', 'failed': true},
          ]),
          isFalse);
    });
  });
}
