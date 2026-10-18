// What the chat shows while it is working.
//
// A blank conversation is the one screen that reads as a crash rather than as waiting,
// and it is easy to ship: the rule used to live as a chain of conditions inside build()
// and depended on every path remembering to set a flag. Opening a past conversation
// forgot, so the whole fetch - seconds of it, over mobile data - rendered as "you have
// not said anything to each other yet".

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/main.dart';

ChatView view({
  bool talkOff = false,
  bool connecting = false,
  bool loadingHistory = false,
  bool ready = true,
  bool hasSession = false,
  bool loaded = false,
  bool hasMessages = false,
  bool hasError = false,
}) =>
    chatViewFor(
      talkOff: talkOff,
      connecting: connecting,
      loadingHistory: loadingHistory,
      ready: ready,
      hasSession: hasSession,
      loaded: loaded,
      hasMessages: hasMessages,
      hasError: hasError,
    );

void main() {
  group('while a conversation is on its way', () {
    test('a chosen conversation that has not arrived is never shown as empty', () {
      // The exact shape of the bug: the flag was not set, everything else said
      // "ready, no messages". That must still be a spinner.
      expect(view(hasSession: true, loadingHistory: false, loaded: false),
          ChatView.loading);
    });

    test('and with the flag set, too', () {
      expect(view(hasSession: true, loadingHistory: true), ChatView.loading);
    });

    test('a screen that has not finished connecting says so first', () {
      expect(view(connecting: true, hasSession: true), ChatView.connecting);
      expect(view(ready: false), ChatView.loading);
    });

    test('switched off beats everything, including a fetch in flight', () {
      expect(view(talkOff: true, loadingHistory: true, hasMessages: true),
          ChatView.off);
    });
  });

  group('once it has arrived', () {
    test('messages are messages', () {
      expect(view(hasSession: true, loaded: true, hasMessages: true),
          ChatView.messages);
    });

    test('a conversation that was read and said nothing is silent, not loading', () {
      // These exist: a routine's session is all tool calls and no speech, and a
      // spinner over one of those would never stop.
      expect(view(hasSession: true, loaded: true), ChatView.silent);
    });

    test('and no conversation at all is the empty screen it has always been', () {
      expect(view(hasSession: false, loaded: true), ChatView.nothingYet);
    });
  });

  group('when something went wrong', () {
    test('an error does not spin on top of itself', () {
      // The error is already written across the top of the screen. A spinner under it
      // would be promising something that is not coming.
      expect(view(hasSession: true, loaded: false, hasError: true),
          ChatView.nothingYet);
    });

    test('but messages already on screen stay on screen', () {
      expect(view(hasSession: true, hasMessages: true, hasError: true),
          ChatView.messages);
    });
  });
}
