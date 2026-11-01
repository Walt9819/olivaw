// Who said it, and what turns up on its own.
//
// Two things the chat screen got wrong, both of them about the fact that a stored
// conversation is not a transcript of two people talking.
//
// **Authorship.** Hermes writes its own bookkeeping into a session as `role: "user"`,
// because that is the only slot a runtime has for "something arrived between turns" -
// the task list re-injected after a compaction, a delegation batch report, a cron
// job's own briefing. Each of those reached this screen dressed as a message the
// owner had typed. The one the owner actually noticed was the task list: Olivaw showed
// him sending his own agent a list of things to do, in his own bubble, in conversations
// he had never typed it into. Another of his agents writing in had the same problem for
// a different reason - it IS a real message, from a real third party, and it was
// attributed to him.
//
// **Arrival.** A turn hands back one reply, and that was the only way anything could
// appear here. But an agent adds to a conversation without being asked: a long job
// finishes, a cron run delivers, a peer writes and gets answered. On Telegram those
// simply arrive; here they sat until somebody reopened the thread.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';
import 'package:olivaw/main.dart';
import 'package:olivaw/outbox.dart';
import 'package:olivaw/strings.dart';
import 'package:olivaw/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

class TalkingLink extends Link {
  TalkingLink()
      : super(Pairing(
          name: 'la computadora',
          host: '192.168.1.9',
          port: 8791,
          certDer: Uint8List(0),
          token: 'token',
        ));

  final Map<String, Map<String, dynamic>> answers = {};

  /// What `talk/watch` hands over on its next call, then nothing.
  List<Map<String, dynamic>> arriving = [];
  int total = 0;
  int watched = 0;

  @override
  Future<Map<String, dynamic>> reach(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
    Duration? wait,
  }) async {
    if (path == '/api/talk/watch') {
      watched++;
      final give = arriving;
      arriving = [];
      total += give.length;
      return {'ok': true, 'total': total, 'reset': false, 'messages': give};
    }
    return answers[path] ?? const {'ok': true};
  }
}

TalkingLink linkWith(List<Map<String, dynamic>> history) {
  final link = TalkingLink();
  link.answers['/api/talk/status'] = {'ok': true, 'phone': true};
  link.answers['/api/talk/sessions'] = {
    'ok': true,
    'sessions': [
      {'id': 's1'}
    ],
  };
  link.answers['/api/talk/history'] = {
    'ok': true,
    'start': 0,
    'total': history.length,
    'has_more': false,
    'messages': history,
  };
  link.total = history.length;
  return link;
}

Agent agentNamed(String slug) => Agent(slug, 'Hermes', true, const {}, false, null);

Widget screen(TalkingLink link, Agent agent) => MaterialApp(
      theme: olivawLight,
      home: Scaffold(body: ChatTab(link: link, agent: agent)),
    );

Future<void> breathe(WidgetTester tester, [int frames = 30]) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 16));
  }
}

/// The screen goes away, which is what stops it asking the computer anything.
Future<void> close(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox());
  Outbox.instance.resetForTests();
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late List<Map<String, String>> notified;

  setUp(() {
    setLanguage(Lang.es);
    SharedPreferences.setMockInitialValues({});
    Outbox.instance.resetForTests();
    Outbox.instance.onAccepted = forgetUnsent;
    notified = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('olivaw/native'), (call) async {
      if (call.method == 'canNotify') return true;
      if (call.method == 'notify') {
        final a = (call.arguments as Map).cast<String, dynamic>();
        notified.add({
          'title': (a['title'] ?? '').toString(),
          'body': (a['body'] ?? '').toString(),
        });
      }
      return null;
    });
  });

  tearDown(() => Outbox.instance.resetForTests());

  group('what Hermes writes down that nobody said', () {
    test('the bookkeeping turns are known by their opening', () {
      // Every one of these was found in the owner's own store, not invented. The task
      // list is the one that was reported: it arrives as `role: "user"` after every
      // compaction, so the phone drew the owner telling his agent what to do.
      expect(machineTurn('[Your active task list was preserved across compression]\n- [ ] x'),
          isTrue);
      expect(machineTurn('[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns…'), isTrue);
      expect(machineTurn('[ASYNC DELEGATION BATCH COMPLETE — deleg_4ea8]'), isTrue);
      expect(machineTurn('[IMPORTANT: You are running as a scheduled cron job. …]'),
          isTrue);
      expect(machineTurn('[PRIOR CONTEXT — for reference only; not a new message]'),
          isTrue);
    });

    test('and a real message is not one of them', () {
      expect(machineTurn('Adelante con la propuesta'), isFalse);
      expect(machineTurn('[no es un marcador] pero empieza con corchete'), isFalse);
      expect(machineTurn(''), isFalse);
    });
  });

  testWidgets('a task list Hermes re-injected is not drawn as the owner',
      (tester) async {
    final link = linkWith([
      {'role': 'user', 'content': 'sigue con el informe'},
      {
        'role': 'user',
        'content': '[Your active task list was preserved across context compression]\n'
            '- [ ] 6. Repetir las marcas con modelo real',
      },
      {'role': 'assistant', 'content': 'Voy con eso.'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-tareas')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);

    expect(find.text('sigue con el informe'), findsOneWidget);
    expect(find.text('Voy con eso.'), findsOneWidget);
    expect(find.textContaining('active task list'), findsNothing);
    expect(find.textContaining('Repetir las marcas'), findsNothing);
    await close(tester);
  });

  testWidgets('another agent writing in is shown as that agent, not as the owner',
      (tester) async {
    final link = linkWith([
      {
        'role': 'peer',
        'from': 'Chalenus',
        'content': 'Necesito la cotización de Honey.',
      },
      {'role': 'assistant', 'content': 'No la tengo.'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-peer')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);

    expect(find.text('Necesito la cotización de Honey.'), findsOneWidget);
    // The name above the words is the whole point: without it the message is
    // unattributed, which is how it came to read as the owner's.
    expect(find.text('Chalenus'), findsOneWidget);
    await close(tester);
  });

  testWidgets('a message the agent sends on its own arrives without being asked for',
      (tester) async {
    final link = linkWith([
      {'role': 'user', 'content': 'arranca el despliegue'},
      {'role': 'assistant', 'content': 'Voy.'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-solo')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);
    expect(find.text('Voy.'), findsOneWidget);

    // Twenty minutes later, as far as this conversation is concerned: the job
    // finished and the agent came back to say so, with nobody having typed anything.
    link.arriving = [
      {'role': 'assistant', 'content': 'Listo, desplegado en Vercel.'},
    ];
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    expect(find.text('Listo, desplegado en Vercel.'), findsOneWidget);
    await close(tester);
  });

  testWidgets('and several of them, one after another, like a chat', (tester) async {
    final link = linkWith([
      {'role': 'user', 'content': 'haz las tres cosas'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-varios')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);

    for (final said in ['La primera, hecha.', 'La segunda también.', 'Y la tercera.']) {
      link.arriving = [
        {'role': 'assistant', 'content': said}
      ];
      await tester.pump(kPollFloor + const Duration(seconds: 1));
      await breathe(tester);
    }

    expect(find.text('La primera, hecha.'), findsOneWidget);
    expect(find.text('La segunda también.'), findsOneWidget);
    expect(find.text('Y la tercera.'), findsOneWidget);
    await close(tester);
  });

  testWidgets('one that lands while the app is away is said out loud', (tester) async {
    final link = linkWith([
      {'role': 'user', 'content': 'avísame cuando acabes'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-aviso')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);

    // The phone goes in a pocket. The screen is still here; nobody is looking at it.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();
    link.arriving = [
      {'role': 'assistant', 'content': 'Acabé: quedó todo verde.'}
    ];
    await tester.pump(kPollFloor + const Duration(seconds: 1));
    await breathe(tester);

    expect(notified.length, 1);
    expect(notified.single['body'], contains('quedó todo verde'));
    await close(tester);
  });

  testWidgets('a quiet conversation does not spin asking', (tester) async {
    final link = linkWith([
      {'role': 'user', 'content': 'hola'},
    ]);
    await tester.pumpWidget(screen(link, agentNamed('a-quieto')));
    await breathe(tester);
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);

    // Ten seconds of nothing happening. The computer is asked to hold the question
    // open, but a computer that answers at once must not turn this into a loop that
    // asks as fast as the network allows - which is what it did the first time.
    await tester.pump(const Duration(seconds: 10));
    await breathe(tester);
    expect(link.watched, lessThan(8), reason: 'asked ${link.watched} times in 10s');
    await close(tester);
  });
}
