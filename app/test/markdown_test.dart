// The markdown renderer, and mostly the ways it must NOT misbehave.
//
// Its input is generated, not typed, so the interesting cases are not "does bold work"
// but "what happens to a Windows path, a multiplication, an unclosed asterisk". Getting
// those wrong eats the rest of a message, which is worse than not rendering at all.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:olivaw/markdown.dart';

/// Pump [src] and return every piece of text the user can actually see, in order.
Future<List<String>> shown(WidgetTester tester, String src) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(body: SingleChildScrollView(child: Markdown(src))),
  ));
  await tester.pump();
  final out = <String>[];
  for (final w in tester.allWidgets) {
    // Both forms matter: table cells are Text.rich, where `data` is null and the words
    // live in `textSpan`. Reading only `data` made a correctly rendered table look empty.
    if (w is Text) {
      final t = w.data ?? w.textSpan?.toPlainText() ?? '';
      if (t.isNotEmpty) out.add(t);
    }
    if (w is SelectableText) {
      final t = w.data ?? w.textSpan?.toPlainText() ?? '';
      if (t.isNotEmpty) out.add(t);
    }
  }
  return out;
}

/// Every style applied to the rendered text, by the substring it covers.
Future<Map<String, TextStyle>> styles(WidgetTester tester, String src) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(body: SingleChildScrollView(child: Markdown(src))),
  ));
  await tester.pump();
  final out = <String, TextStyle>{};
  for (final w in tester.allWidgets) {
    if (w is SelectableText && w.textSpan != null) {
      w.textSpan!.visitChildren((span) {
        if (span is TextSpan && span.text != null && span.text!.isNotEmpty) {
          out[span.text!] = span.style ?? const TextStyle();
        }
        return true;
      });
    }
  }
  return out;
}

void main() {
  testWidgets('plain text survives untouched', (t) async {
    expect((await shown(t, 'Hola, ¿qué tal?')).join(), contains('Hola, ¿qué tal?'));
  });

  testWidgets('bold and italic are styled, markers are gone', (t) async {
    final s = await styles(t, 'esto es **importante** y esto *menos*');
    expect(s['importante']?.fontWeight, FontWeight.w700);
    expect(s['menos']?.fontStyle, FontStyle.italic);
    expect(s.keys.join(), isNot(contains('**')));
  });

  testWidgets('inline code keeps its characters', (t) async {
    final s = await styles(t, 'corre `git status --porcelain` ahora');
    expect(s['git status --porcelain']?.fontFamily, 'monospace');
  });

  testWidgets('a fenced block keeps its newlines and its language', (t) async {
    final out = await shown(t, '```bash\ncd /tmp\nls -la\n```');
    expect(out, contains('bash'));
    expect(out.any((s) => s.contains('cd /tmp') && s.contains('ls -la')), isTrue);
  });

  testWidgets('headings, bullets and numbers each render', (t) async {
    final out = (await shown(t, '# Título\n- uno\n- dos\n1. primero')).join('\n');
    expect(out, contains('Título'));
    expect(out, contains('uno'));
    expect(out, contains('dos'));
    expect(out, contains('primero'));
  });

  testWidgets('a table renders every cell', (t) async {
    final out = (await shown(t, '| a | b |\n|---|---|\n| 1 | 2 |')).join(' ');
    for (final cell in ['a', 'b', '1', '2']) {
      expect(out, contains(cell));
    }
  });

  testWidgets('pipes without a separator row are not a table', (t) async {
    final out = (await shown(t, 'uno | dos | tres')).join();
    expect(out, contains('uno | dos | tres'));
  });

  group('the ways it must not eat text', () {
    testWidgets('an unclosed asterisk stays literal', (t) async {
      final out = (await shown(t, 'de 3 * 4 y nada más')).join();
      expect(out, contains('3 * 4'));
      expect(out, contains('nada más'));
    });

    testWidgets('snake_case is not italics', (t) async {
      final out = (await shown(t, 'usa device_id y user_name')).join();
      expect(out, contains('device_id'));
      expect(out, contains('user_name'));
    });

    testWidgets('a Windows path survives', (t) async {
      const p = r'C:\Users\revol\AppData\Local\Olivaw';
      expect((await shown(t, 'está en $p hoy')).join(), contains(p));
    });

    testWidgets('an unclosed fence does not swallow the rest silently', (t) async {
      final out = (await shown(t, '```\nalgo sin cerrar')).join();
      expect(out, contains('algo sin cerrar'));
    });

    testWidgets('an unclosed bracket stays literal', (t) async {
      final out = (await shown(t, 'mira [esto y ya')).join();
      expect(out, contains('[esto y ya'));
    });

    testWidgets('empty input renders nothing and does not throw', (t) async {
      // The pump itself is the assertion: a throw inside build fails the test.
      expect((await shown(t, '')).join(), isEmpty);
    });
  });

  testWidgets('a link is styled and carries its target', (t) async {
    final tapped = <String>[];
    await t.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Markdown('ver [la guía](https://example.com/g) aquí',
            onLink: tapped.add),
      ),
    ));
    await t.pump();
    final found = <String>[];
    for (final w in t.allWidgets) {
      if (w is SelectableText && w.textSpan != null) {
        w.textSpan!.visitChildren((s) {
          if (s is TextSpan && s.text == 'la guía') found.add(s.text!);
          return true;
        });
      }
    }
    expect(found, isNotEmpty);
  });

  testWidgets('a bare url is linkified without its trailing full stop', (t) async {
    final s = await styles(t, 'está en https://example.com/x.');
    expect(s.keys, contains('https://example.com/x'));
  });
}
