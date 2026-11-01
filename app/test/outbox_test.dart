// A message that is no longer the property of the screen it was typed on.
//
// The failure this is about
// -------------------------
// Sending used to be one HTTP request held open for the whole of an agent turn, with
// the answer coming back down it. On a phone that is a bet against the network: wifi
// hands over to mobile data, the relay re-attaches, a carrier drops an idle tunnel,
// Android freezes the app in a pocket. When the socket died the computer never noticed
// and finished the turn anyway - so the owner saw a failed send, no answer and no
// notification, while the agent was in fact working. The conversation looked dead.
//
// So the computer now takes the message and answers later, and these tests are about
// the part of that living on the phone: that the send leaves before anything else can
// get in the way, that the answer arrives at a screen which was not there when it was
// asked for, that the owner is told about it, and - the one worth more than the
// feature - that nothing is ever said twice.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';
import 'package:olivaw/main.dart';
import 'package:olivaw/outbox.dart';
import 'package:olivaw/strings.dart';
import 'package:olivaw/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// A computer that takes a message now and has an answer later.
///
/// It behaves the way the real one does: `talk/send` returns a turn id straight away,
/// and `talk/turns` says "running" until the test says otherwise. It also keeps every
/// body it was sent, because what is being asked here is as much about what goes on
/// the wire - one turn per message, the same id on a retry - as about what comes back.
class HandsOverLink extends Link {
  HandsOverLink()
      : super(Pairing(
          name: 'la computadora',
          host: '192.168.1.9',
          port: 8791,
          certDer: Uint8List(0),
          token: 'token',
        ));

  final List<String> asked = [];
  final List<Map<String, dynamic>> sends = [];
  final Map<String, Object> breaks = {};
  final Map<String, Map<String, dynamic>> answers = {};

  /// turn id -> what `talk/turns` should say about it.
  final Map<String, Map<String, dynamic>> turns = {};

  /// A computer that has never heard of handing a message over: it holds the request
  /// open and answers down it, the way every version before this one did.
  bool oldSchool = false;

  /// Held here, the send does not finish until the test lets it - which is the only
  /// way to walk out of the chat WHILE a message is being handed over, as a thumb
  /// does and as no instant fake can.
  Completer<void>? holdSend;
  int _minted = 0;

  /// How many separate turns this computer was asked to run.
  int get started => _minted;

  int askedFor(String path) => asked.where((p) => p == path).length;

  /// The answer lands. From here a poll will report it.
  void answer(String turn, String reply) {
    turns[turn] = {
      'id': turn,
      'state': 'done',
      'session_id': 's1',
      'reply': reply,
      'files': <dynamic>[],
    };
  }

  void refuse(String turn, String why) {
    turns[turn] = {'id': turn, 'state': 'failed', 'session_id': 's1', 'detail': why};
  }

  void forget(String turn) {
    turns[turn] = {'id': turn, 'state': 'unknown'};
  }

  @override
  Future<Map<String, dynamic>> reach(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
    Duration? wait,
  }) async {
    asked.add(path);
    // Recorded before it is allowed to fail: an attempt that died on the way out is
    // still an attempt, and whether the SECOND one carries the same name as the first
    // is the whole question.
    if (path == '/api/talk/send') sends.add({...?body});
    if (path == '/api/talk/send' && holdSend != null) await holdSend!.future;
    final boom = breaks[path];
    if (boom != null) throw boom;
    if (path == '/api/talk/send' && !oldSchool) {
      final cid = (body?['client_id'] ?? '').toString();
      // The real computer keys on this: the same id is always the same turn, which is
      // what makes a retry after a lost connection safe.
      for (final entry in turns.entries) {
        if ((entry.value['client_id'] ?? '') == cid && cid.isNotEmpty) {
          return {'ok': true, 'queued': true, 'again': true, 'turn': entry.value};
        }
      }
      _minted++;
      final id = 't$_minted';
      turns[id] = {
        'id': id,
        'state': 'running',
        'session_id': 's1',
        'client_id': cid,
      };
      return {'ok': true, 'queued': true, 'turn': turns[id]};
    }
    if (path == '/api/talk/turns') {
      final ids = [for (final i in (body?['ids'] as List? ?? [])) i.toString()];
      return {
        'ok': true,
        'turns': [
          for (final i in ids)
            turns[i] ?? {'id': i, 'state': 'unknown'},
        ],
      };
    }
    return answers[path] ?? const {'ok': true};
  }
}

HandsOverLink linkThatHandsOver() {
  final link = HandsOverLink();
  link.answers['/api/talk/status'] = {'ok': true, 'phone': true};
  link.answers['/api/talk/sessions'] = {'ok': true, 'sessions': []};
  link.answers['/api/talk/new'] = {
    'ok': true,
    'session': {'id': 's1'}
  };
  link.answers['/api/talk/history'] = {'ok': true, 'messages': [], 'start': 0};
  return link;
}

Agent agentNamed(String slug) => Agent(slug, 'Hermes', true, const {}, false, null);

Widget screen(HandsOverLink link, Agent agent) => MaterialApp(
      theme: olivawLight,
      home: Scaffold(body: ChatTab(link: link, agent: agent)),
    );

Future<void> breathe(WidgetTester tester, [int frames = 14]) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 16));
  }
}

/// Put the outbox down.
///
/// It is a singleton that deliberately outlives screens, which in a test means it
/// outlives the test: a message still in the air at the end of one is a poll still
/// scheduled when the framework checks for stray timers - and, worse, a message that
/// would walk into the next test. In the app that is the whole point of it; here it
/// has to be ended by hand.
Future<void> stopWaiting(WidgetTester tester) async {
  // The screen goes first: a chat that is still mounted is still asking the computer
  // whether anything has arrived, and that question has a timer behind it.
  await tester.pumpWidget(const SizedBox());
  Outbox.instance.resetForTests();
  await tester.pump();
}

Future<void> type(WidgetTester tester, String words) async {
  await tester.enterText(find.byType(TextField), words);
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  /// What the phone was asked to show the owner, in order.
  late List<Map<String, String>> notified;

  /// Whether the service keeping this app out of Android's reach is up.
  late List<String> service;

  setUp(() {
    setLanguage(Lang.es);
    SharedPreferences.setMockInitialValues({});
    Outbox.instance.resetForTests();
    // What main() does at startup. These tests pump a widget rather than booting the
    // app, so without this the outbox has nobody to tell when a message has left the
    // phone - which is exactly the wiring the test below is about.
    Outbox.instance.onAccepted = forgetUnsent;
    notified = [];
    service = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('olivaw/native'), (call) async {
      switch (call.method) {
        case 'canNotify':
          return true;
        case 'notify':
          final a = (call.arguments as Map).cast<String, dynamic>();
          notified.add({
            'title': (a['title'] ?? '').toString(),
            'body': (a['body'] ?? '').toString(),
          });
          return null;
        case 'waitStart':
          service.add('up');
          return null;
        case 'waitStop':
          service.add('down');
          return null;
      }
      return null;
    });
  });

  // The outbox deliberately outlives every screen, which in a test means it outlives
  // the test. Left alone it would carry one test's message, and its poll, into the
  // next one.
  tearDown(() => Outbox.instance.resetForTests());

  group('the pure part', () {
    test('only turns still waiting for an answer are worth asking about', () {
      final answered = Outgoing(
          agent: 'a',
          agentName: 'A',
          session: 's',
          mine: {},
          clientId: 'c1',
          isDefault: false)
        ..turn = 't1'
        ..reply = 'listo';
      final running = Outgoing(
          agent: 'a',
          agentName: 'A',
          session: 's',
          mine: {},
          clientId: 'c2',
          isDefault: false)
        ..turn = 't2';
      final broken = Outgoing(
          agent: 'a',
          agentName: 'A',
          session: 's',
          mine: {},
          clientId: 'c3',
          isDefault: false)
        ..turn = 't3'
        ..error = 'no se pudo';
      // Never accepted at all: there is no turn to ask after, only words to send
      // again. Asking about an empty id would be asking about every turn at once.
      final never = Outgoing(
          agent: 'a',
          agentName: 'A',
          session: 's',
          mine: {},
          clientId: 'c4',
          isDefault: false);

      expect(turnsToWatch([answered, running, broken, never]), ['t2']);
    });

    test('a message keeps one name however many times it is sent', () {
      final a = newClientId();
      final b = newClientId();
      expect(a, isNot(b));
      expect(a.length, greaterThan(15));
    });
  });

  testWidgets('the send is accepted at once, not when the agent has finished',
      (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-rapido')));
    await breathe(tester);

    await type(tester, 'hola');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // Nothing has answered yet and the screen is not blocked waiting for it.
    expect(link.askedFor('/api/talk/send'), 1);
    expect(find.text('hola'), findsOneWidget);
    expect(find.text('No se envió.'), findsNothing);
    // The message asked to be carried rather than waited for, and named itself.
    expect(link.sends.single['async'], isTrue);
    expect((link.sends.single['client_id'] ?? '').toString(), isNotEmpty);
    await stopWaiting(tester);
  });

  testWidgets('the answer arrives after the screen has gone and comes back',
      (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-vuelta')));
    await breathe(tester);
    await type(tester, '¿cómo va el informe?');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // Out of the chat entirely, which is what somebody does after asking something
    // that will take a while.
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: Text('otra cosa'))));
    await breathe(tester);

    // The agent finishes while nobody is looking.
    link.answer('t1', 'Va bien: queda el anexo.');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    // Told about it, because there was no screen to put it on.
    expect(notified.length, 1);
    expect(notified.single['title'], 'Hermes');
    expect(notified.single['body'], contains('queda el anexo'));

    // And walking back in lands in that conversation with the answer in it, rather
    // than on a blank page offering to continue something.
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'user', 'content': '¿cómo va el informe?'},
        {'role': 'assistant', 'content': 'Va bien: queda el anexo.'},
      ],
    };
    await tester.pumpWidget(screen(link, agentNamed('a-vuelta')));
    await breathe(tester, 30);
    expect(find.textContaining('queda el anexo'), findsOneWidget);
    await stopWaiting(tester);
  });

  testWidgets('a message answered while the chat was closed is not drawn twice',
      (tester) async {
    // Found on a real phone, not in a test. Send, leave, let the answer land, come
    // back: the conversation read off the computer had the message in it, and so did
    // the copy this phone was still holding as "never sent" - because the thing that
    // takes it out of that box was a listener on a screen that was not there. The
    // outbox says so itself now.
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-doble')));
    await breathe(tester);
    await type(tester, 'cuenta del 1 al 5');

    // Out of the chat WHILE the message is being handed over - a thumb is faster than
    // a round trip, and this is the gesture that produced the bug on a real phone.
    link.holdSend = Completer<void>();
    await tester.tap(find.byIcon(Icons.send));
    await tester.pump();
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: Text('fuera'))));
    await breathe(tester);
    link.holdSend!.complete();
    link.holdSend = null;
    await breathe(tester);

    link.answer('t1', '1, 2, 3, 4, 5');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'user', 'content': 'cuenta del 1 al 5'},
        {'role': 'assistant', 'content': '1, 2, 3, 4, 5'},
      ],
    };
    await tester.pumpWidget(screen(link, agentNamed('a-doble')));
    await breathe(tester, 30);

    expect(find.text('cuenta del 1 al 5'), findsOneWidget);
    expect(find.text('1, 2, 3, 4, 5'), findsOneWidget);
    expect(find.text('No se envió.'), findsNothing);
    await stopWaiting(tester);
  });

  testWidgets('an answer that lands on the screen in front of you is not a notification',
      (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-delante')));
    await breathe(tester);
    await type(tester, 'hola');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    link.answer('t1', 'hola a ti');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    expect(find.textContaining('hola a ti'), findsOneWidget);
    expect(notified, isEmpty);
    await stopWaiting(tester);
  });

  testWidgets('leaving the chat does not put down the service keeping the phone awake',
      (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-servicio')));
    await breathe(tester);
    await type(tester, 'algo largo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);
    expect(service, contains('up'));

    // This is the gesture that used to lose the answer: dispose() stopped the service.
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: Text('fuera'))));
    await breathe(tester);
    expect(service.contains('down'), isFalse,
        reason: 'the wait belongs to the message, not to the screen');

    // It comes down when the last message has been answered, and not before.
    link.answer('t1', 'ya está');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);
    expect(service.last, 'down');
    await stopWaiting(tester);
  });

  testWidgets('a retry is the same message, never a second one', (tester) async {
    final link = linkThatHandsOver();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-dos-veces')));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);
    expect(find.text('No se envió.'), findsOneWidget);

    link.breaks.remove('/api/talk/send');
    await tester.tap(find.text('Reintentar'));
    await breathe(tester);

    // Two attempts, one message: the computer was handed the same name both times.
    expect(link.sends.length, 2);
    expect(link.sends[0]['client_id'], link.sends[1]['client_id']);
    expect(link.sends[0]['text'], link.sends[1]['text']);

    link.answer('t1', 'hecho');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);
    expect(find.text('hecho'), findsOneWidget);
    expect(link.started, 1, reason: 'one turn, not two');
    await stopWaiting(tester);
  });

  testWidgets('a turn the agent refused says so on the message', (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-rechazo')));
    await breathe(tester);
    await type(tester, 'hola');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    link.refuse('t1', 'El agente dejó de escuchar.');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    expect(find.textContaining('dejó de escuchar'), findsOneWidget);
    // And it is not left pretending to think about it.
    expect(find.text('escribiendo…'), findsNothing);
    await stopWaiting(tester);
  });

  testWidgets('a computer that forgot the turn sends us to the conversation',
      (tester) async {
    final link = linkThatHandsOver();
    await tester.pumpWidget(screen(link, agentNamed('a-olvido')));
    await breathe(tester);
    await type(tester, 'hola');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // Olivaw restarted underneath the message. It was ACCEPTED, so it was almost
    // certainly answered - the answer is in the conversation, and that is where to
    // look. Calling this a failure would invite a retry that says it all twice.
    link.forget('t1');
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'user', 'content': 'hola'},
        {'role': 'assistant', 'content': 'hola, dime'},
      ],
    };
    final before = link.askedFor('/api/talk/history');
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester, 30);

    expect(link.askedFor('/api/talk/history'), greaterThan(before));
    expect(find.text('No se envió.'), findsNothing);
    expect(link.started, 1);
    await stopWaiting(tester);
  });

  testWidgets('an older computer that answers on the spot still works', (tester) async {
    // A phone updates from a store page and a computer updates itself overnight, so
    // they are routinely a version apart. A computer that has never heard of handing
    // over ignores the field and answers the old way, and nothing about that may look
    // broken to the owner.
    final link = linkThatHandsOver();
    link.oldSchool = true;
    link.answers['/api/talk/send'] = {
      'ok': true,
      'reply': 'contestado a la antigua',
      'files': <dynamic>[],
    };

    await tester.pumpWidget(screen(link, agentNamed('a-viejo')));
    await breathe(tester);
    await type(tester, 'hola');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    expect(find.text('contestado a la antigua'), findsOneWidget);
    expect(Outbox.instance.handsOver, isFalse);
    // Nothing to poll for: the answer came back on the send itself.
    expect(link.askedFor('/api/talk/turns'), 0);
    await stopWaiting(tester);
  });

  testWidgets('giving up on a message stops it being sent', (tester) async {
    final link = linkThatHandsOver();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-descarte')));
    await breathe(tester);
    await type(tester, 'mejor no');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    await tester.tap(find.byIcon(Icons.close));
    await breathe(tester);

    // Out of the conversation, back in the box, and out of the outbox - which would
    // otherwise go on trying to send words the owner has just taken back.
    expect(Outbox.instance.forAgent('a-descarte'), isEmpty);
    expect(find.widgetWithText(TextField, 'mejor no'), findsOneWidget);
    await stopWaiting(tester);
  });
}
