// The screens that are shown while nothing has arrived yet.
//
// These exist because `flutter analyze` is happy with a widget that throws the moment it
// is laid out, and the screens most likely to throw are the ones nobody looks at while
// building: the wait, the empty state, the thing that only appears on a bad connection.
// Pumping them is the cheapest way to find out they render at all.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/loading.dart';
import 'package:olivaw/theme.dart';

Widget _app(Widget child) =>
    MaterialApp(theme: olivawLight, home: Scaffold(body: child));

void main() {
  testWidgets('a wait says what it is waiting for', (tester) async {
    await tester.pumpWidget(_app(const Waiting(lines: ['Buscando tu computadora…'])));
    await tester.pump();
    expect(find.text('Buscando tu computadora…'), findsOneWidget);
  });

  testWidgets('it moves on to the next line on its own', (tester) async {
    await tester.pumpWidget(_app(const Waiting(
      lines: ['Primero', 'Después'],
      every: Duration(milliseconds: 50),
    )));
    await tester.pump();
    expect(find.text('Primero'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('Después'), findsOneWidget);
    // And stops there rather than looping: a message that cycles forever reads as a
    // screen stuck repeating itself.
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Primero'), findsNothing);
  });

  testWidgets('a wait with no words still draws', (tester) async {
    await tester.pumpWidget(_app(const Waiting()));
    await tester.pump();
    expect(find.byType(Waiting), findsOneWidget);
  });

  testWidgets('the compact one fits on a row', (tester) async {
    // Somewhere bounded, which is where it actually goes - a list row, a cell. A
    // Row hands its children unbounded width, and nothing that can be given infinite
    // room can promise to fit in it.
    await tester.pumpWidget(_app(
      const SizedBox(
        width: 220,
        child: Row(children: [
          Expanded(child: Waiting(compact: true, lines: ['Trayéndolo…'])),
        ]),
      ),
    ));
    await tester.pump();
    expect(find.text('Trayéndolo…'), findsOneWidget);
  });

  testWidgets('a small spinner can carry a size', (tester) async {
    await tester.pumpWidget(_app(const Busy(label: '1,4 MB')));
    await tester.pump();
    expect(find.text('1,4 MB'), findsOneWidget);
  });

  group('sizes a person can judge', () {
    test('bytes, kilobytes, megabytes', () {
      expect(humanSize(0), '');
      expect(humanSize(-1), '');
      expect(humanSize(512), '512 B');
      expect(humanSize(1024), '1,0 kB');
      expect(humanSize(1536), '1,5 kB');
      expect(humanSize(1024 * 1024), '1,0 MB');
      expect(humanSize(52 * 1024 * 1024), '52,0 MB');
    });

    test('a comma, because the app speaks Spanish', () {
      expect(humanSize(1536).contains(','), isTrue);
      expect(humanSize(1536).contains('.'), isFalse);
    });
  });
}
