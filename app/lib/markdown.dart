// Markdown, rendered.
//
// An agent writes in markdown. Shown as plain text, a reply with a table of steps, a
// path in backticks and a bolded warning arrives as a wall of asterisks and hashes - the
// emphasis inverted, the structure gone, the one line that mattered buried. Telegram
// renders it; so does the desktop console; so should this.
//
// Written here rather than taken from a package. The obvious one, flutter_markdown, was
// discontinued by the Flutter team and the replacement it points at is not theirs; this
// app's whole claim is that it has one dependency, and a dead parser is a poor reason to
// break it. What is implemented is what an agent actually emits:
//
//   # headings        **bold**        `code`          > quotes
//   - bullets         *italic*        ```fences```    1. numbers
//   ---               [links](url)    ~~struck~~      | tables |
//
// What is deliberately NOT implemented: raw HTML (an agent's output is not trusted
// markup and this is not a browser), reference links, and footnotes. Anything
// unrecognised falls through as the literal text that was written, which is the only
// safe failure for a renderer whose input is generated.

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import 'theme.dart';

/// Renders [source] as a column of blocks.
class Markdown extends StatelessWidget {
  const Markdown(this.source,
      {super.key,
      this.baseStyle,
      this.onLink,
      this.highlight = '',
      this.actions = const []});

  final String source;
  final TextStyle? baseStyle;
  final void Function(String url)? onLink;

  /// What a search is looking for, marked wherever it appears. Empty for no search.
  final String highlight;

  /// Extra entries for the selection toolbar: copy the whole message, reply to it,
  /// ask it again. They live there rather than behind a long press of their own
  /// because a long press already means 'select', and taking that away would make it
  /// impossible to copy half of an answer - which is the thing people actually do.
  final List<(String, VoidCallback)> actions;

  @override
  Widget build(BuildContext context) {
    final base = baseStyle ?? DefaultTextStyle.of(context).style;
    final blocks = _parseBlocks(source);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < blocks.length; i++) ...[
          if (i > 0) SizedBox(height: blocks[i].gapBefore),
          blocks[i].build(context, base, onLink, highlight, actions),
        ],
      ],
    );
  }
}

/// The selection toolbar, with this message's own actions added to it.
///
/// Flutter's defaults (copy, select all, share) stay exactly where they were: the
/// point is to ADD to the menu a thumb already knows, not to replace it.
Widget Function(BuildContext, EditableTextState) menuFor(
    List<(String, VoidCallback)> actions) {
  return (BuildContext context, EditableTextState state) {
    return AdaptiveTextSelectionToolbar.buttonItems(
      anchors: state.contextMenuAnchors,
      buttonItems: [
        ...state.contextMenuButtonItems,
        for (final (label, run) in actions)
          ContextMenuButtonItem(
            label: label,
            onPressed: () {
              ContextMenuController.removeAny();
              run();
            },
          ),
      ],
    );
  };
}

// ── blocks ────────────────────────────────────────────────────────────────────────────

abstract class _Block {
  double get gapBefore => 8;
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions);
}

class _Paragraph extends _Block {
  _Paragraph(this.text);
  final String text;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
          String hl, List<(String, VoidCallback)> actions) =>
      SelectableText.rich(TextSpan(children: _inline(c, text, base, onLink, hl)),
          contextMenuBuilder: menuFor(actions));
}

class _Heading extends _Block {
  _Heading(this.level, this.text);
  final int level;
  final String text;
  @override
  double get gapBefore => level <= 2 ? 16 : 12;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions) {
    // Four sizes for six levels: past h4 the difference stops being legible on a phone
    // and starts being noise.
    const scale = [1.5, 1.32, 1.16, 1.06, 1.0, 1.0];
    final style = base.copyWith(
      fontSize: (base.fontSize ?? 14) * scale[level - 1],
      fontWeight: FontWeight.w700,
      height: 1.25,
    );
    return SelectableText.rich(TextSpan(children: _inline(c, text, style, onLink, hl)),
        contextMenuBuilder: menuFor(actions));
  }
}

class _Code extends _Block {
  _Code(this.code, this.language);
  final String code;
  final String language;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(vertical: 2),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: c.c.panel2,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: c.c.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (language.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(language,
                  style: TextStyle(
                      fontSize: 11, color: c.c.muted, fontWeight: FontWeight.w600)),
            ),
          // Horizontal scroll rather than wrapping: a wrapped command line is a command
          // line you cannot copy and run.
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SelectableText(
              code,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: (base.fontSize ?? 14) - 1,
                height: 1.4,
                color: c.c.ink,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Quote extends _Block {
  _Quote(this.text);
  final String text;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions) {
    return Container(
      padding: const EdgeInsets.only(left: 10),
      decoration: BoxDecoration(
        border: Border(left: BorderSide(color: c.c.accent, width: 3)),
      ),
      child: SelectableText.rich(
          TextSpan(
              children: _inline(c, text, base.copyWith(color: c.c.muted), onLink, hl)),
          contextMenuBuilder: menuFor(actions)),
    );
  }
}

class _ListItem extends _Block {
  _ListItem(this.marker, this.text, this.depth);
  final String marker;
  final String text;
  final int depth;
  @override
  double get gapBefore => 4;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions) {
    return Padding(
      padding: EdgeInsets.only(left: 4.0 + depth * 16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 22,
            child: Text(marker,
                style: base.copyWith(
                    color: c.c.muted, fontFeatures: const [FontFeature.tabularFigures()])),
          ),
          Expanded(
            child: SelectableText.rich(
                TextSpan(children: _inline(c, text, base, onLink, hl)),
                contextMenuBuilder: menuFor(actions)),
          ),
        ],
      ),
    );
  }
}

class _Rule extends _Block {
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
          String hl, List<(String, VoidCallback)> actions) =>
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Divider(color: c.c.line, height: 1),
      );
}

class _Table extends _Block {
  _Table(this.rows, this.hasHeader);
  final List<List<String>> rows;
  final bool hasHeader;
  @override
  Widget build(BuildContext c, TextStyle base, void Function(String)? onLink,
      String hl, List<(String, VoidCallback)> actions) {
    // Scrolls sideways instead of squeezing: three columns of numbers crushed into a
    // phone's width are not a table any more.
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Container(
        decoration: BoxDecoration(
          border: Border.all(color: c.c.line),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var r = 0; r < rows.length; r++)
              Container(
                decoration: BoxDecoration(
                  color: (hasHeader && r == 0) ? c.c.panel2 : null,
                  border: r + 1 < rows.length
                      ? Border(bottom: BorderSide(color: c.c.line))
                      : null,
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final cell in rows[r])
                      Container(
                        constraints: const BoxConstraints(minWidth: 70, maxWidth: 240),
                        padding:
                            const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                        child: Text.rich(TextSpan(
                            children: _inline(
                                c,
                                cell,
                                hasHeader && r == 0
                                    ? base.copyWith(fontWeight: FontWeight.w700)
                                    : base,
                                onLink,
                                hl))),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ── the block parser ──────────────────────────────────────────────────────────────────

final _fence = RegExp(r'^\s*(```+|~~~+)\s*([A-Za-z0-9_+-]*)\s*$');
final _heading = RegExp(r'^(#{1,6})\s+(.*)$');
final _rule = RegExp(r'^\s*([-*_])(\s*\1){2,}\s*$');
final _bullet = RegExp(r'^(\s*)[-*+]\s+(.*)$');
final _number = RegExp(r'^(\s*)(\d{1,3})[.)]\s+(.*)$');
final _quote = RegExp(r'^\s*>\s?(.*)$');
final _tableRow = RegExp(r'^\s*\|(.+)\|\s*$');
final _tableSep = RegExp(r'^\s*\|?[\s:|-]+\|[\s:|-]*$');

List<_Block> _parseBlocks(String src) {
  final lines = src.replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n');
  final out = <_Block>[];
  final para = <String>[];

  void flush() {
    if (para.isEmpty) return;
    out.add(_Paragraph(para.join('\n').trim()));
    para.clear();
  }

  for (var i = 0; i < lines.length; i++) {
    final line = lines[i];

    final fence = _fence.firstMatch(line);
    if (fence != null) {
      flush();
      final closer = fence.group(1)![0];
      final body = <String>[];
      i++;
      while (i < lines.length) {
        final m = _fence.firstMatch(lines[i]);
        if (m != null && m.group(1)![0] == closer) break;
        body.add(lines[i]);
        i++;
      }
      out.add(_Code(body.join('\n'), fence.group(2) ?? ''));
      continue;
    }

    if (line.trim().isEmpty) {
      flush();
      continue;
    }
    if (_rule.hasMatch(line)) {
      flush();
      out.add(_Rule());
      continue;
    }
    final h = _heading.firstMatch(line);
    if (h != null) {
      flush();
      out.add(_Heading(h.group(1)!.length, h.group(2)!.trim()));
      continue;
    }

    // A table needs its separator row to be a table at all; without it these are just
    // lines with pipes in them, and an agent writes plenty of those.
    if (_tableRow.hasMatch(line) &&
        i + 1 < lines.length &&
        _tableSep.hasMatch(lines[i + 1])) {
      flush();
      List<String> cells(String s) => s
          .trim()
          .replaceAll(RegExp(r'^\||\|$'), '')
          .split('|')
          .map((c) => c.trim())
          .toList();
      final rows = <List<String>>[cells(line)];
      i += 2;
      while (i < lines.length && _tableRow.hasMatch(lines[i])) {
        rows.add(cells(lines[i]));
        i++;
      }
      i--;
      out.add(_Table(rows, true));
      continue;
    }

    final q = _quote.firstMatch(line);
    if (q != null) {
      flush();
      final body = <String>[q.group(1)!];
      while (i + 1 < lines.length && _quote.hasMatch(lines[i + 1])) {
        body.add(_quote.firstMatch(lines[++i])!.group(1)!);
      }
      out.add(_Quote(body.join('\n').trim()));
      continue;
    }

    final b = _bullet.firstMatch(line);
    if (b != null) {
      flush();
      out.add(_ListItem('•', b.group(2)!, b.group(1)!.length ~/ 2));
      continue;
    }
    final n = _number.firstMatch(line);
    if (n != null) {
      flush();
      out.add(_ListItem('${n.group(2)}.', n.group(3)!, n.group(1)!.length ~/ 2));
      continue;
    }
    para.add(line);
  }
  flush();
  return out;
}

// ── inline ────────────────────────────────────────────────────────────────────────────

/// `**bold**`, `*italic*`, `` `code` ``, `~~struck~~`, `[text](url)` and bare URLs.
///
/// One left-to-right pass rather than a grammar: markdown's emphasis rules are famously
/// ambiguous, and the failure that matters here is not "an edge case nested wrongly" but
/// "an asterisk the agent meant literally ate the rest of the message". Anything that
/// does not close is emitted as the text that was written.
List<InlineSpan> _inline(BuildContext c, String text, TextStyle base,
    void Function(String)? onLink, String hl) {
  final spans = <InlineSpan>[];
  final buf = StringBuffer();

  /// Emit [t] in [style], with every occurrence of the search term marked.
  ///
  /// Done at the point the text is finally emitted rather than up front, so a term
  /// that straddles a `**bold**` boundary marks the part inside each piece instead of
  /// not matching at all - and so the marking survives whatever styling is around it.
  void emit(String t, TextStyle style) {
    if (t.isEmpty) return;
    if (hl.isEmpty) {
      spans.add(TextSpan(text: t, style: style));
      return;
    }
    final hay = t.toLowerCase();
    final needle = hl.toLowerCase();
    var from = 0;
    while (true) {
      final at = hay.indexOf(needle, from);
      if (at < 0) {
        spans.add(TextSpan(text: t.substring(from), style: style));
        return;
      }
      if (at > from) {
        spans.add(TextSpan(text: t.substring(from, at), style: style));
      }
      spans.add(TextSpan(
        text: t.substring(at, at + needle.length),
        style: style.copyWith(
          backgroundColor: c.c.warn.withValues(alpha: 0.45),
          color: c.c.ink,
          fontWeight: FontWeight.w700,
        ),
      ));
      from = at + needle.length;
    }
  }

  void flush() {
    if (buf.isEmpty) return;
    emit(buf.toString(), base);
    buf.clear();
  }

  int i = 0;
  while (i < text.length) {
    final rest = text.substring(i);

    // code first: nothing inside backticks is markup
    if (text[i] == '`') {
      final end = text.indexOf('`', i + 1);
      if (end > i) {
        flush();
        spans.add(TextSpan(
          text: text.substring(i + 1, end),
          style: base.copyWith(
            fontFamily: 'monospace',
            fontSize: (base.fontSize ?? 14) - 0.5,
            backgroundColor: c.c.panel2,
            color: c.c.ink,
          ),
        ));
        i = end + 1;
        continue;
      }
    }

    // Longest marker first, so `***` is not read as `**` followed by a stray `*`.
    var matched = false;
    for (final mark in const ['***', '**', '~~']) {
      if (!rest.startsWith(mark)) continue;
      final end = text.indexOf(mark, i + mark.length);
      if (end <= i) continue; // never closed: it is literal text, leave it alone
      flush();
      var style = base;
      if (mark == '~~') {
        style = style.copyWith(decoration: TextDecoration.lineThrough);
      } else {
        style = style.copyWith(fontWeight: FontWeight.w700);
        if (mark == '***') style = style.copyWith(fontStyle: FontStyle.italic);
      }
      spans.addAll(_inline(c, text.substring(i + mark.length, end), style, onLink, hl));
      i = end + mark.length;
      matched = true;
      break;
    }
    if (matched) continue;

    // single-character emphasis, but never inside a word: snake_case and 2*3 are not
    // italics, and treating them as such is the classic way a renderer mangles a path.
    if ((text[i] == '*' || text[i] == '_') &&
        (i == 0 || !_wordChar(text[i - 1]))) {
      final mark = text[i];
      final end = text.indexOf(mark, i + 1);
      if (end > i + 1 &&
          (end + 1 >= text.length || !_wordChar(text[end + 1])) &&
          !text.substring(i + 1, end).contains('\n')) {
        flush();
        spans.addAll(_inline(c, text.substring(i + 1, end),
            base.copyWith(fontStyle: FontStyle.italic), onLink, hl));
        i = end + 1;
        continue;
      }
    }

    if (text[i] == '[') {
      final close = text.indexOf(']', i);
      if (close > i && close + 1 < text.length && text[close + 1] == '(') {
        final paren = text.indexOf(')', close);
        if (paren > close) {
          flush();
          final label = text.substring(i + 1, close);
          final url = text.substring(close + 2, paren).trim();
          spans.add(_link(c, label, url, base, onLink));
          i = paren + 1;
          continue;
        }
      }
    }

    if (rest.startsWith('http://') || rest.startsWith('https://')) {
      var end = i;
      while (end < text.length && !' \n\t<>"\')'.contains(text[end])) {
        end++;
      }
      // Trailing punctuation belongs to the sentence, not the address.
      while (end > i && '.,;:!?'.contains(text[end - 1])) {
        end--;
      }
      flush();
      final url = text.substring(i, end);
      spans.add(_link(c, url, url, base, onLink));
      i = end;
      continue;
    }

    buf.write(text[i]);
    i++;
  }
  flush();
  return spans;
}

final _word = RegExp(r'[A-Za-z0-9_]');
bool _wordChar(String ch) => _word.hasMatch(ch);

InlineSpan _link(BuildContext c, String label, String url, TextStyle base,
    void Function(String)? onLink) {
  final style = base.copyWith(
    color: c.c.accent,
    decoration: TextDecoration.underline,
    decorationColor: c.c.accent.withValues(alpha: 0.4),
  );
  return TextSpan(
    text: label,
    style: style,
    recognizer: onLink == null ? null : (TapGestureRecognizer()..onTap = () => onLink(url)),
  );
}
