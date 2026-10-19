// What may be sent a second time.
//
// Recovering from a path that died under the phone means sending the request again
// down another one. That is free for a roster or a page of a conversation and it is
// not free for a message: a request that got no answer may still have ARRIVED, and the
// silence be only the answer's. The list is small, it is by name, and it is the kind
// of thing that is wrong for months without anybody noticing - which is why it is
// here rather than only in a comment.

import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';

void main() {
  group('which road to try first', () {
    final now = DateTime(2026, 10, 18, 18, 30);

    test('the computer itself, when nothing says otherwise', () {
      // At home the address is one hop away and the rendezvous is a detour through
      // another continent.
      expect(relayFirst(hasRelay: true, relayWorked: null, now: now), isFalse);
    });

    test('the rendezvous, once the address has failed and it has worked', () {
      // The cost this avoids is the FULL connect timeout, on every request: a carrier
      // does not answer a packet addressed to somebody's living room, it drops it.
      expect(
          relayFirst(
              hasRelay: true,
              relayWorked: now.subtract(const Duration(seconds: 30)),
              now: now),
          isTrue);
    });

    test('but not for ever - walking back in the door is not an event it hears', () {
      expect(
          relayFirst(
              hasRelay: true,
              relayWorked: now.subtract(preferRelayFor * 2),
              now: now),
          isFalse);
    });

    test('and never when there is no rendezvous to prefer', () {
      expect(
          relayFirst(
              hasRelay: false,
              relayWorked: now.subtract(const Duration(seconds: 5)),
              now: now),
          isFalse);
    });

    test('the window is minutes, not hours', () {
      // A long memory would leave a phone at home talking to its computer through a
      // datacentre; a short one pays the dead address again every few requests.
      expect(preferRelayFor.inMinutes, inInclusiveRange(1, 5));
    });
  });

  group('asking again', () {
    test('reading anything twice costs nothing', () {
      for (final path in [
        '/api/agents/list',
        '/api/talk/sessions',
        '/api/talk/history',
        '/api/talk/search',
        '/api/stats/usage',
        '/api/avatar/get',
        '/api/cron/list',
        '/api/agent/info',
        '/api/talk/status',
        '/ping',
      ]) {
        expect(repeatableRoute(path), isTrue, reason: path);
      }
    });

    test('but saying something twice means doing it twice', () {
      // Each of these leaves something behind that the owner would then have two of.
      for (final path in [
        '/api/talk/send',     // the message, in the conversation and on Telegram
        '/api/talk/new',      // an empty second conversation
        '/api/talk/attach',   // the same file, twice, in the agent's folder
        '/api/talk/voice',    // the same voice note, twice
      ]) {
        expect(repeatableRoute(path), isFalse, reason: path);
      }
    });

    test('a route nobody has thought about is treated as safe to repeat', () {
      // Deliberate: the alternative is a new read-only route silently losing its
      // recovery. The four above are the ones that create something, they are named,
      // and a new one that does has to be added here - which is what the test above
      // is for.
      expect(repeatableRoute('/api/something/new'), isTrue);
    });
  });
}
