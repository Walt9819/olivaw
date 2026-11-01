// Messages on their way, and the answers coming back - neither of them owned by a
// screen.
//
// What this replaces
// ------------------
// Sending used to be a method on the chat screen that awaited one HTTP request for as
// long as the agent took, up to ten minutes, and the reply came back as that request's
// response. Two consequences, both of which the owner met:
//
//   * The answer rode on the socket the question went up. A phone changes network the
//     way it changes pocket - wifi to mobile data, a relay re-attaching, a carrier
//     dropping an idle tunnel - and every one of those killed the socket. The computer
//     never noticed and finished the turn anyway; the phone showed a failed send, no
//     answer, and no notification. The conversation looked dead while the agent was
//     working, which is the single worst thing a messaging app can do.
//
//   * The send belonged to a widget. Backing out of the chat, or opening another
//     agent, left it in a State nobody was holding any more.
//
// So the sending lives here, above every screen, and the socket is no longer the
// delivery. The computer accepts a message and answers at once with the id of a turn
// it will finish on its own. This asks what became of that turn, over whatever
// connection is working at the time, as many times as it needs. Losing the network
// costs a reconnection; it no longer costs an answer.
//
// Said twice is worse than said late
// ----------------------------------
// The one thing this must never do is deliver a message twice - these agents act, and
// a second "send that email" is not a cosmetic duplicate. Every composed message gets
// a `clientId` once, at the moment it is written, and keeps it through every attempt.
// The computer keys turns on it, so a retry after a dropped connection re-attaches to
// the turn already running instead of starting another. That is what makes retrying
// safe enough to do automatically.
//
// Still nothing on disk
// ---------------------
// As with the outbox it grew out of: in memory and nowhere else. The conversations
// live on the computer precisely so a lost phone is not carrying a copy, and a message
// in flight is the same words. Closing the app forgets what was in flight - and the
// computer still finishes it, and the answer is in the conversation when the phone
// comes back.

import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import 'link.dart';
import 'native.dart';
import 'strings.dart';

/// One message the computer has been asked to carry.
class Outgoing {
  Outgoing({
    required this.agent,
    required this.agentName,
    required this.session,
    required this.mine,
    required this.clientId,
    required this.isDefault,
  });

  /// Which agent's conversation this belongs to, by slug.
  final String agent;

  /// What to put in the notification. The slug is not a name.
  final String agentName;

  /// The conversation. Empty until the computer opens one, which the first message
  /// of a thread does.
  String session;

  /// The bubble, shared by reference with the chat screen that drew it. Writing the
  /// result onto this map is what makes the screen's copy right as well.
  final Map<String, dynamic> mine;

  /// Minted once per composed message and kept through every attempt. See the header.
  final String clientId;

  /// Whether this is the main agent, which is the one that travels without a profile
  /// in the body. Carried here rather than guessed from the slug: the poller asks
  /// about turns long after the screen that knew is gone.
  final bool isDefault;

  /// The computer's name for the turn, once it has accepted one. Empty while the
  /// message has not reached the computer at all - which is the retryable state.
  String turn = '';

  /// Why it is not moving, if it is not. Shown on the bubble.
  String error = '';

  /// The answer, once there is one.
  String reply = '';
  List<dynamic> replyFiles = const [];

  /// Already told the owner about this answer.
  bool announced = false;

  bool get accepted => turn.isNotEmpty;
  bool get answered => reply.isNotEmpty || replyFiles.isNotEmpty;
}

/// Mint one. Long enough that two phones and a hundred messages never collide, short
/// enough to be a field rather than a payload.
String newClientId() {
  final r = Random.secure();
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
  return List.generate(22, (_) => alphabet[r.nextInt(alphabet.length)]).join();
}

/// How long the computer is asked to hold a poll open before answering "nothing yet".
///
/// Long enough that an idle conversation costs one request a minute rather than twenty,
/// short enough that the carrier middleboxes which kill a quiet tunnel do not get there
/// first. Safe to hold where holding the SEND open was not: if this dies, nothing was
/// riding on it and the next one asks the same question.
const Duration kPollHold = Duration(seconds: 20);

/// After a poll fails outright, how long before trying again. A phone in a lift should
/// not spend the trip hammering a dead socket.
const Duration kPollRetry = Duration(seconds: 5);

/// The shortest gap between two polls, whatever the computer did.
///
/// The hold above is a REQUEST, not a guarantee. A computer that has not learnt to
/// hold the question open answers "still thinking" the instant it is asked - and a
/// loop that trusts the hold to do the waiting then asks again immediately, for as
/// long as the agent takes. That is a phone spinning one core and a battery for the
/// length of a turn. The floor costs an idle conversation nothing, because a poll that
/// really was held open has already waited longer than this.
const Duration kPollFloor = Duration(seconds: 2);

/// Decide what a poll should ask for next.
///
/// Pulled out of the loop so it can be tested without a network: given what is in
/// flight, these are the turn ids worth asking about and whether anything is left to
/// wait for at all.
List<String> turnsToWatch(Iterable<Outgoing> live) =>
    [for (final o in live) if (o.accepted && !o.answered && o.error.isEmpty) o.turn];

class Outbox extends ChangeNotifier {
  Outbox._();

  static final Outbox instance = Outbox._();

  final List<Outgoing> _live = [];

  /// The agent whose conversation is open and in front of the owner, if any. A reply
  /// that lands on the screen somebody is looking at does not also need a
  /// notification; one that lands anywhere else does.
  String watching = '';

  /// Set by the chat screen while the app itself is in the foreground.
  bool inFront = false;

  /// Whether the computer on the other end takes a message and answers later, rather
  /// than holding the request open for the whole turn.
  ///
  /// Learnt from the first answer rather than from a version number, because that is
  /// the thing actually being asked about: a phone and a computer here update on
  /// different days - one from a store page, the other by itself overnight - so a
  /// phone that got the new habit first has to work against a computer that has not.
  /// It matters for exactly one decision: whether retrying is safe on its own. A
  /// computer that hands over recognises the same message twice and re-attaches; an
  /// older one cannot, so the screen asks the conversation first.
  bool handsOver = false;

  Link? _link;
  Timer? _retry;
  bool _polling = false;
  bool _waiting = false;

  List<Outgoing> get live => List.unmodifiable(_live);

  /// What is still on its way for one agent, oldest first.
  List<Outgoing> forAgent(String slug) =>
      [for (final o in _live) if (o.agent == slug) o];

  /// The turn carrying this exact bubble, if one is.
  Outgoing? of(Map<String, dynamic> mine) {
    for (final o in _live) {
      if (identical(o.mine, mine)) return o;
    }
    return null;
  }

  /// Anything at all still in the air.
  bool get busy => _live.any((o) => !o.answered && o.error.isEmpty);

  /// Hand a message to the computer and stop waiting for it.
  ///
  /// Returns as soon as the computer has accepted it - typically well under a second -
  /// rather than when the agent has finished answering. The answer arrives later,
  /// through [addListener], whatever screen is open by then.
  ///
  /// `openSession` is called if this message had to open a conversation, because the
  /// screen that sent it needs the id as much as this does.
  Future<void> send({
    required Link link,
    required String agent,
    required String agentName,
    required Map<String, dynamic> mine,
    required bool isDefault,
    String session = '',
    void Function(String session)? openSession,
  }) async {
    _link = link;
    var entry = of(mine);
    if (entry == null) {
      entry = Outgoing(
        agent: agent,
        agentName: agentName,
        session: session,
        mine: mine,
        isDefault: isDefault,
        // Kept across retries if the bubble already carries one: the whole point is
        // that the second attempt is recognisably the same message.
        clientId: (mine['clientId'] ?? '').toString().isNotEmpty
            ? mine['clientId'].toString()
            : newClientId(),
      );
      mine['clientId'] = entry.clientId;
      _live.add(entry);
    } else {
      if (session.isNotEmpty) entry.session = session;
    }
    entry.error = '';
    mine['failed'] = false;
    mine['sent'] = false;
    notifyListeners();
    await _handOver(entry, isDefault: isDefault, openSession: openSession);
  }

  Map<String, dynamic> _body(bool isDefault, String agent,
          [Map<String, dynamic>? extra]) =>
      {
        if (!isDefault) 'profile': agent,
        ...?extra,
      };

  Future<void> _handOver(
    Outgoing e, {
    required bool isDefault,
    void Function(String session)? openSession,
  }) async {
    final link = _link;
    if (link == null) return;
    try {
      if (e.session.isEmpty) {
        final made = await link.reach('POST', '/api/talk/new',
            body: _body(isDefault, e.agent));
        if (made['ok'] != true) {
          throw LinkError(
              tr((made['detail'] ?? tr('No se pudo abrir la conversación.')).toString()));
        }
        e.session =
            ((made['session'] as Map?)?['id'] ?? made['id'] ?? '').toString();
        if (e.session.isEmpty) {
          throw LinkError(tr('La computadora no abrió ninguna conversación.'));
        }
        e.mine['session'] = e.session;
        openSession?.call(e.session);
      }
      final res = await link.reach('POST', '/api/talk/send',
          body: _body(isDefault, e.agent, {
            'session_id': e.session,
            'text': (e.mine['sendText'] ?? e.mine['text'] ?? '').toString(),
            if ((e.mine['sendFiles'] as List? ?? []).isNotEmpty)
              'files': [for (final f in (e.mine['sendFiles'] as List? ?? [])) f],
            // What makes this a handover rather than a wait. An older computer has
            // never heard of the field, ignores it, and answers the old way - which
            // is handled below, so a phone that updated first still works.
            'async': true,
            'client_id': e.clientId,
          }),
          // Only as long as it takes to be ACCEPTED. The agent's thinking is no
          // longer on the other end of this.
          wait: Link.replyWait);
      if (res['ok'] != true) {
        throw LinkError(tr((res['detail'] ?? tr('Sin respuesta')).toString()));
      }
      if (res['queued'] == true && res['turn'] is Map) {
        handsOver = true;
        final t = (res['turn'] as Map).cast<String, dynamic>();
        e.turn = (t['id'] ?? '').toString();
        final sid = (t['session_id'] ?? '').toString();
        if (sid.isNotEmpty && sid != e.session) {
          e.session = sid;
          e.mine['session'] = sid;
          openSession?.call(sid);
        }
        e.mine['sent'] = true;
        _take(t, e);
        notifyListeners();
        _keepUp();
        return;
      }
      // An older computer: it held the request open and this IS the answer. Nothing
      // to watch, nothing to poll - take it and finish.
      handsOver = false;
      e.mine['sent'] = true;
      e.reply = (res['reply'] ?? '').toString().trim();
      e.replyFiles = (res['files'] as List?) ?? const [];
      if (!e.answered) e.reply = tr('(sin respuesta)');
      _settle(e);
    } catch (err) {
      e.error = humanError(err);
      e.mine['failed'] = true;
      notifyListeners();
      _keepUp();
    }
  }

  /// Read one turn as the computer describes it.
  void _take(Map<String, dynamic> t, Outgoing e) {
    switch ((t['state'] ?? '').toString()) {
      case 'done':
        e.reply = (t['reply'] ?? '').toString().trim();
        e.replyFiles = (t['files'] as List?) ?? const [];
        if (!e.answered) e.reply = tr('(sin respuesta)');
        _settle(e);
      case 'failed':
        e.error = tr((t['detail'] ?? tr('No se pudo enviar.')).toString());
        e.mine['failed'] = true;
      case 'unknown':
        // The computer has forgotten this turn - it restarted, or enough time passed.
        // The message was accepted, so it was almost certainly answered; the answer is
        // in the conversation, and the screen reads it from there. Saying "it failed"
        // here would invite a retry that says everything twice.
        e.mine['sent'] = true;
        e.reply = '';
        e.replyFiles = const [];
        _forget(e, reload: true);
    }
  }

  /// An answer. Tell whoever needs telling, and let it go.
  void _settle(Outgoing e) {
    e.mine['failed'] = false;
    e.mine['sent'] = true;
    if (!e.announced) {
      e.announced = true;
      // Only when they are not already looking at it. A notification for a reply
      // appearing on the screen in front of you is noise.
      final seen = inFront && watching == e.agent;
      if (!seen && e.reply.isNotEmpty) {
        Native.notify(e.agentName, e.reply);
      }
    }
    notifyListeners();
  }

  /// Done with this one. The screen has had its chance to read the reply off it.
  void _forget(Outgoing e, {bool reload = false}) {
    e.mine.remove('clientId');
    _live.remove(e);
    if (reload) _reloads.add(e.agent);
    notifyListeners();
    _keepUp();
  }

  /// Agents whose conversation should be read again from the computer, because
  /// something happened that this cannot account for message by message.
  final Set<String> _reloads = {};

  /// Does this agent's conversation need re-reading? Asked and cleared by the screen.
  bool takeReload(String agent) => _reloads.remove(agent);

  /// Drop the answers for this agent WITHOUT drawing them.
  ///
  /// For the one moment where drawing them would be saying them twice: a chat being
  /// reopened reads the conversation off the computer, and a turn that is already
  /// answered is already in it. The outbox exists to carry an answer to a screen that
  /// was not there - not to add a second copy to one that has just read it.
  void collectAnswered(String agent) {
    for (final e in [..._live]) {
      if (e.agent == agent && e.answered) _forget(e);
    }
  }

  /// The screen has drawn this answer. Drop it.
  void collected(Outgoing e) => _forget(e);

  /// Give up on a message nobody is going to send.
  void discard(Map<String, dynamic> mine) {
    final e = of(mine);
    if (e != null) _forget(e);
  }

  /// Keep asking the computer what became of the turns in the air, and keep the phone
  /// awake while there are any.
  void _keepUp() {
    final watch = turnsToWatch(_live);
    if (watch.isEmpty) {
      _retry?.cancel();
      _retry = null;
      if (_waiting) {
        _waiting = false;
        Native.waitStop();
      }
      return;
    }
    if (!_waiting) {
      _waiting = true;
      // From here the agent may take minutes. The service keeps this app out of the
      // range where Android freezes it, so putting the phone away does not lose the
      // wait. It is started for the OUTBOX rather than for a screen, which is the
      // difference: leaving the chat no longer stops it.
      Native.waitStart(_live.first.agentName);
    }
    if (!_polling) unawaited(_poll());
  }

  Future<void> _poll() async {
    if (_polling) return;
    _polling = true;
    try {
      while (true) {
        final watch = turnsToWatch(_live);
        if (watch.isEmpty) break;
        final link = _link;
        if (link == null) break;
        // Which agent's turns these are. One request per agent, because the profile
        // is part of the question.
        final first = _live.firstWhere((o) => watch.contains(o.turn));
        Map<String, dynamic> res;
        final began = DateTime.now();
        try {
          res = await link.reach('POST', '/api/talk/turns',
              body: _body(first.isDefault, first.agent, {
                'ids': watch,
                'wait': kPollHold.inSeconds,
              }),
              wait: kPollHold + const Duration(seconds: 10));
        } catch (_) {
          // The computer is not reachable right now. Nothing is lost - the turn is
          // running there regardless - so wait and ask again rather than failing
          // messages that are perfectly fine.
          _retry?.cancel();
          _retry = Timer(kPollRetry, () {
            _retry = null;
            _keepUp();
          });
          break;
        }
        if (res['ok'] != true) break;
        for (final row in (res['turns'] as List? ?? [])) {
          if (row is! Map) continue;
          final t = row.cast<String, dynamic>();
          final id = (t['id'] ?? '').toString();
          for (final e in [..._live]) {
            if (e.turn == id) _take(t, e);
          }
        }
        notifyListeners();
        // See kPollFloor: the hold is something the computer is ASKED for, and one
        // that answers at once must not turn this into a spin. Handed to the timer
        // rather than slept through, so that unpairing - or a test ending - can take
        // it away instead of waiting for it.
        final spent = DateTime.now().difference(began);
        if (spent < kPollFloor && turnsToWatch(_live).isNotEmpty) {
          _retry?.cancel();
          _retry = Timer(kPollFloor - spent, () {
            _retry = null;
            _keepUp();
          });
          break;
        }
      }
    } finally {
      _polling = false;
      // Settling a turn can leave nothing to wait for, which is where the service is
      // put down and the wake lock with it.
      final watch = turnsToWatch(_live);
      if (watch.isEmpty && _waiting) {
        _waiting = false;
        Native.waitStop();
      }
    }
  }

  /// Put the waiting service down, unless something still needs it.
  ///
  /// The one way anything outside this file is allowed to stop it. A voice note has a
  /// wait of its own - the transcription - and used to end that wait by stopping the
  /// service outright, which also ended the wait for every message already in the air.
  void standDown() {
    if (busy) return;
    if (_waiting) {
      _waiting = false;
      Native.waitStop();
    }
  }

  /// Keep the phone awake for something that is not a turn, such as a recording on its
  /// way to being transcribed.
  void standBy(String who) {
    if (_waiting) return;
    _waiting = true;
    Native.waitStart(who);
  }

  /// Ask again now, whatever the poller thought it was doing.
  ///
  /// A phone coming back out of a pocket is a process whose timers and sockets Android
  /// may have frozen and dropped without telling anyone - so the loop can believe it is
  /// waiting on a poll that no longer exists. This costs one request and is the
  /// difference between the answer appearing as the screen lights up and appearing
  /// whenever something else happens to wake the app.
  void nudge() {
    _retry?.cancel();
    _retry = null;
    _keepUp();
  }

  /// Everything the phone was carrying for this pairing, dropped. Used when the owner
  /// unpairs - the words were theirs and they are going.
  void clear() {
    _live.clear();
    _reloads.clear();
    _retry?.cancel();
    _retry = null;
    if (_waiting) {
      _waiting = false;
      Native.waitStop();
    }
    notifyListeners();
  }

  @visibleForTesting
  void resetForTests() {
    _live.clear();
    _reloads.clear();
    _retry?.cancel();
    _retry = null;
    _waiting = false;
    _polling = false;
    _link = null;
    watching = '';
    inFront = false;
  }
}
