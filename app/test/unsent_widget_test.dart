// The chat screen when a message does not get through.
//
// The pure parts of this are checked next door in unsent_test.dart. What is checked
// here is the thing that actually went wrong on a phone: a send that failed left the
// bubble looking exactly like one that had arrived, the words cleared out of the box
// underneath, and nothing anywhere to press. These tests drive the real screen with a
// link that refuses to carry anything, because the state machine - which bubble is
// marked, what the retry asks the computer first, what happens to the words when the
// owner gives up on them - is not something a pure function can be asked about.
//
// Every test uses its own agent, because an unsent message deliberately outlives the
// screen it was written on and would otherwise walk from one test into the next.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/link.dart';
import 'package:olivaw/main.dart';
import 'package:olivaw/strings.dart';
import 'package:olivaw/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// A computer that answers whatever it is told to answer, and remembers being asked.
class FakeLink extends Link {
  FakeLink()
      : super(Pairing(
          name: 'la computadora',
          host: '192.168.1.9',
          port: 8791,
          certDer: Uint8List(0),
          token: 'token',
        ));

  final List<String> asked = [];
  final Map<String, Object> breaks = {};
  final Map<String, Map<String, dynamic>> answers = {};

  int askedFor(String path) => asked.where((p) => p == path).length;

  @override
  Future<Map<String, dynamic>> reach(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
    Duration? wait,
  }) async {
    asked.add(path);
    final boom = breaks[path];
    if (boom != null) throw boom;
    return answers[path] ?? const {'ok': true};
  }
}

/// A conversation this agent is allowed to have, with nothing in it yet.
FakeLink linkThatWorks() {
  final link = FakeLink();
  link.answers['/api/talk/status'] = {'ok': true, 'phone': true};
  link.answers['/api/talk/sessions'] = {'ok': true, 'sessions': []};
  link.answers['/api/talk/new'] = {
    'ok': true,
    'session': {'id': 's1'}
  };
  link.answers['/api/talk/history'] = {'ok': true, 'messages': [], 'start': 0};
  return link;
}

Agent agentNamed(String slug) =>
    Agent(slug, 'Hermes', true, const {}, false, null);

Widget screen(FakeLink link, Agent agent) => MaterialApp(
      theme: olivawLight,
      home: Scaffold(body: ChatTab(link: link, agent: agent)),
    );

/// Pumped by hand rather than settled: the waiting screen animates on a loop, and
/// pumpAndSettle waits for an animation that is never going to stop.
Future<void> breathe(WidgetTester tester, [int frames = 14]) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 16));
  }
}

Future<void> type(WidgetTester tester, String words) async {
  await tester.enterText(find.byType(TextField), words);
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    setLanguage(Lang.es);
    SharedPreferences.setMockInitialValues({});
    // The foreground service, the notification permission and the notification
    // itself. None of them exist in a test, and none of them are what is being asked
    // about here.
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('olivaw/native'),
            (call) async => call.method == 'canNotify' ? true : null);
  });

  testWidgets('a message that does not get through says so, and offers to go again',
      (tester) async {
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-fallo')));
    await breathe(tester);

    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // The words are still there - that is the whole point - and they are no longer
    // dressed as a message somebody has received.
    expect(find.text('manda ese correo'), findsOneWidget);
    expect(find.text('No se envió.'), findsOneWidget);
    expect(find.text('Reintentar'), findsOneWidget);
    // And the agent is not left pretending to think about a message it never got.
    expect(find.text('escribiendo…'), findsNothing);
  });

  testWidgets('pressing it again sends it again, and the answer arrives',
      (tester) async {
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-reintento')));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // The road comes back.
    link.breaks.remove('/api/talk/send');
    link.answers['/api/talk/send'] = {'ok': true, 'reply': 'Enviado.'};

    await tester.tap(find.text('Reintentar'));
    await breathe(tester);

    expect(find.text('Enviado.'), findsOneWidget);
    expect(find.text('No se envió.'), findsNothing);
    expect(find.text('Reintentar'), findsNothing);
    expect(link.askedFor('/api/talk/send'), 2);
  });

  testWidgets('one that had arrived after all is not sent a second time',
      (tester) async {
    // The failure that costs real money: the request went out, the agent did the
    // thing, and only the answer was lost on the way back. Pressing "send it again"
    // must not send that email twice.
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('El computadora tardó demasiado.');

    await tester.pumpWidget(screen(link, agentNamed('a-duplicado')));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    // It turns out the computer has it, and has answered it.
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'user', 'content': 'manda ese correo'},
        {'role': 'assistant', 'content': 'Ya estaba enviado.'},
      ],
    };

    await tester.tap(find.text('Reintentar'));
    await breathe(tester);

    expect(link.askedFor('/api/talk/send'), 1, reason: 'said once, not twice');
    expect(find.text('Ya estaba enviado.'), findsOneWidget);
    expect(find.text('No se envió.'), findsNothing);
    // And the owner is told why nothing was sent, or the button looks broken.
    expect(find.text('Ese mensaje ya había llegado.'), findsOneWidget);
  });

  testWidgets('walking out of the chat does not throw the words away',
      (tester) async {
    // Which is what somebody does while a message refuses to go. Before this, the
    // unsent message lived only in the list it was drawn in.
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');
    final agent = agentNamed('a-vuelta');

    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);
    expect(find.text('No se envió.'), findsOneWidget);

    // Out of the chat entirely, and back in.
    await tester.pumpWidget(MaterialApp(
        theme: olivawLight, home: const Scaffold(body: Text('otra pantalla'))));
    await breathe(tester, 4);
    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);

    expect(find.text('manda ese correo'), findsOneWidget);
    expect(find.text('Reintentar'), findsOneWidget);
  });

  testWidgets('and it survives coming back with nothing answering at all',
      (tester) async {
    // The version of that which actually happens. The send failed because the
    // computer could not be reached, and when the owner opens the agent again it
    // still cannot - so the screen cannot ask whether talking is even switched on.
    // The words are on this phone and owe that question nothing, but they used to be
    // restored after it, which meant the one time they were not restored was the one
    // time there was something to restore.
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');
    final agent = agentNamed('a-sin-senal');

    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);
    expect(find.text('No se envió.'), findsOneWidget);

    // Out of the chat, and now nothing answers at all.
    await tester.pumpWidget(MaterialApp(
        theme: olivawLight, home: const Scaffold(body: Text('otra pantalla'))));
    await breathe(tester, 4);
    for (final route in ['/api/talk/status', '/api/talk/sessions',
        '/api/talk/history', '/api/talk/new']) {
      link.breaks[route] = LinkError('No encuentro tu computadora.');
    }
    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);

    expect(find.text('manda ese correo'), findsOneWidget);
    expect(find.text('Reintentar'), findsOneWidget);
    // And the screen does not tell them they have never said anything.
    expect(find.text('Todavía no os habéis dicho nada.'), findsNothing);
  });

  testWidgets('and rescuing it there gives the owner the box back', (tester) async {
    // The screen could not ask whether talking was on, so it assumed not and left the
    // composer dead. A reply from the agent settles the question.
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');
    final agent = agentNamed('a-caja');

    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    await tester.pumpWidget(MaterialApp(
        theme: olivawLight, home: const Scaffold(body: Text('otra pantalla'))));
    await breathe(tester, 4);
    for (final route in ['/api/talk/status', '/api/talk/sessions',
        '/api/talk/history']) {
      link.breaks[route] = LinkError('No encuentro tu computadora.');
    }
    await tester.pumpWidget(screen(link, agent));
    await breathe(tester);
    expect(tester.widget<TextField>(find.byType(TextField)).enabled, isFalse);

    // The road comes back, and the message is rescued.
    link.breaks.remove('/api/talk/send');
    link.answers['/api/talk/send'] = {'ok': true, 'reply': 'Hecho.'};
    await tester.tap(find.text('Reintentar'));
    await breathe(tester);

    expect(find.text('Hecho.'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField)).enabled, isTrue);
  });

  testWidgets('giving up on it puts the words back in the box', (tester) async {
    // Discarding has to be how a message gets rewritten, not how it gets lost.
    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-descarte')));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    await tester.tap(find.byIcon(Icons.close));
    await breathe(tester);

    expect(find.text('No se envió.'), findsNothing);
    final box = tester.widget<TextField>(find.byType(TextField));
    expect(box.controller?.text, 'manda ese correo');
  });

  testWidgets('the failure line is not squeezed into a column beside the button',
      (tester) async {
    // On the Fold's cover screen, in German, it was: the sentence and the two buttons
    // were one Row, so "Wurde nicht gesendet." got whatever width the button left
    // over and came out as three stacked words. The default test surface is 800
    // logical pixels wide and hides it completely, so this asks on a real one.
    tester.view.physicalSize = const Size(904, 2316);
    tester.view.devicePixelRatio = 2.625;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    setLanguage(Lang.de);

    final link = linkThatWorks();
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-aleman')));
    await breathe(tester);
    await type(tester, 'manda ese correo');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    final line = find.text('Wurde nicht gesendet.');
    expect(line, findsOneWidget);
    // Measured as a share of the bubble rather than as a number of lines: how many
    // lines a sentence needs is a question about the font, and the font here is the
    // test one, where every glyph is a square. The bug was that the sentence got
    // whatever the buttons left over - about a quarter of the bubble - so what has
    // to be true is that it gets most of it.
    final bubble = tester.getSize(find.text('manda ese correo')).width;
    expect(tester.getSize(line).width, greaterThan(bubble * 0.6));
    // And neither block ran out of the bubble on the way.
    expect(tester.takeException(), isNull);
  });

  testWidgets('the offer to carry on says what the conversation was about',
      (tester) async {
    final link = linkThatWorks();
    link.answers['/api/talk/sessions'] = {
      'ok': true,
      'sessions': [
        {'id': 's9', 'title': 'Correo a Seva'}
      ],
    };
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'user', 'content': '¿lo mandaste?'},
        {'role': 'assistant', 'content': 'Sí, esta mañana.'},
      ],
    };

    await tester.pumpWidget(screen(link, agentNamed('a-pista')));
    await breathe(tester);

    expect(find.text('Seguir la conversación anterior'), findsOneWidget);
    expect(find.text('Hermes: Sí, esta mañana.'), findsOneWidget);
  });

  testWidgets('and it goes away once this screen is a conversation of its own',
      (tester) async {
    // It was already refusing to open at that point - dropping sixty old turns on
    // top of a message that went through would throw that message away - so a strip
    // still sitting there, showing the last line of a different conversation above a
    // live one, was saying something that was not true any more.
    final link = linkThatWorks();
    link.answers['/api/talk/sessions'] = {
      'ok': true,
      'sessions': [
        {'id': 's9'}
      ],
    };
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'assistant', 'content': 'Sí, esta mañana.'},
      ],
    };
    link.answers['/api/talk/send'] = {'ok': true, 'reply': 'Hecho.'};

    await tester.pumpWidget(screen(link, agentNamed('a-oferta')));
    await breathe(tester);
    expect(find.text('Seguir la conversación anterior'), findsOneWidget);

    await type(tester, 'otra cosa');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    expect(find.text('Hecho.'), findsOneWidget);
    expect(find.text('Seguir la conversación anterior'), findsNothing);
  });

  testWidgets('but a message that failed leaves the offer standing', (tester) async {
    // The other side of the same rule: nothing was said to anybody, so the old
    // conversation is still the one that was going on - and it arrives above the
    // message still waiting to go, not instead of it.
    final link = linkThatWorks();
    link.answers['/api/talk/sessions'] = {
      'ok': true,
      'sessions': [
        {'id': 's9'}
      ],
    };
    link.answers['/api/talk/history'] = {
      'ok': true,
      'start': 0,
      'messages': [
        {'role': 'assistant', 'content': 'Sí, esta mañana.'},
      ],
    };
    link.breaks['/api/talk/send'] = LinkError('No encuentro tu computadora.');

    await tester.pumpWidget(screen(link, agentNamed('a-oferta-rota')));
    await breathe(tester);
    await type(tester, 'otra cosa');
    await tester.tap(find.byIcon(Icons.send));
    await breathe(tester);

    expect(find.text('No se envió.'), findsOneWidget);
    expect(find.text('Seguir la conversación anterior'), findsOneWidget);

    // And opening it puts the old turns above the one that never left.
    await tester.tap(find.text('Seguir la conversación anterior'));
    await breathe(tester);
    expect(find.text('Sí, esta mañana.'), findsOneWidget);
    expect(find.text('otra cosa'), findsOneWidget);
    expect(find.text('No se envió.'), findsOneWidget);
  });
}
