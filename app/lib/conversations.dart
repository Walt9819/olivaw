// Finding one conversation among hundreds.
//
// An agent that has been working for a month has more conversations than anyone will
// ever scroll through - the machine this was written on had 461 for its main agent -
// and the sheet used to ask for the first thirty and call that the list. Thirty was
// both too much to wait for on mobile data and far too few to find anything in:
// everything older than a fortnight was simply unreachable.
//
// So: a page at a time, the next one fetched while the owner is still reading the one
// before, and a search that looks INSIDE the conversations rather than at their names.
// Hermes rarely names a session - what a person remembers is a sentence from the middle
// of it, which is exactly what `talk/search` reads.

import 'dart:async';

import 'package:flutter/material.dart';

import 'link.dart';
import 'strings.dart';
import 'theme.dart';

/// How many conversations arrive at a time. Small enough that the first page is on
/// screen almost at once over a relay, large enough to fill a phone.
const int kConversationPage = 15;

/// How long after the last keystroke the search is actually sent. Long enough not to
/// search for "in", "inf", "info" on the way to "informe", short enough not to feel
/// like waiting.
const Duration kSearchPause = Duration(milliseconds: 350);

/// Offer this agent's past conversations and return the chosen one.
///
/// Returns `{'new': true}` when the owner asked for a fresh thread, the session row
/// when they picked one, and null when they simply closed the sheet.
Future<Map<String, dynamic>?> pickConversation(
  BuildContext context, {
  required Link link,
  required Map<String, dynamic> Function([Map<String, dynamic>?]) body,
}) {
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    isScrollControlled: true,
    // Capped, and not merely 'as tall as it needs': a scrollable sheet grows to the
    // full height of the screen and puts its own heading under the clock.
    constraints:
        BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.78),
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
    builder: (sheet) => _Conversations(link: link, body: body),
  );
}

class _Conversations extends StatefulWidget {
  const _Conversations({required this.link, required this.body});

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;

  @override
  State<_Conversations> createState() => _ConversationsState();
}

class _ConversationsState extends State<_Conversations> {
  final _scroll = ScrollController();
  final _field = TextEditingController();

  /// The list as it is browsed: page after page, newest first.
  final List<Map<String, dynamic>> _rows = [];
  int _offset = 0;
  bool _more = true;
  bool _loading = false;
  String _error = '';

  /// And the list as it is searched, which is a different list entirely - kept apart
  /// so that clearing the search puts the browsing back exactly where it was rather
  /// than starting it again.
  String _query = '';
  bool _searching = false;
  List<Map<String, dynamic>>? _found;
  int _scanned = 0;
  Timer? _typing;

  List<Map<String, dynamic>> get _shown => _found ?? _rows;

  @override
  void initState() {
    super.initState();
    _loadPage();
    _scroll.addListener(() {
      // Fetched before the owner reaches the end, so the next page is usually already
      // there by the time they would have noticed it was missing.
      if (_found == null &&
          _scroll.position.pixels >
              _scroll.position.maxScrollExtent - 400) {
        _loadPage();
      }
    });
  }

  @override
  void dispose() {
    _typing?.cancel();
    _scroll.dispose();
    _field.dispose();
    super.dispose();
  }

  Future<void> _loadPage() async {
    if (_loading || !_more) return;
    setState(() => _loading = true);
    try {
      final res = await widget.link.reach('POST', '/api/talk/sessions',
          body: widget.body({'limit': kConversationPage, 'offset': _offset}));
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() => _error =
            tr((res['detail'] ?? tr('No pude leer las conversaciones.')).toString()));
        return;
      }
      final got = [
        for (final r in (res['sessions'] as List? ?? []))
          if (r is Map) r.cast<String, dynamic>(),
      ];
      setState(() {
        _rows.addAll(got);
        _offset += got.length;
        // Trusting `has_more` alone would loop for ever against a computer that keeps
        // saying yes and sending nothing.
        _more = res['has_more'] == true && got.isNotEmpty;
        _error = '';
      });
    } catch (e) {
      if (mounted) setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _typed(String q) {
    _typing?.cancel();
    final text = q.trim();
    if (text.isEmpty) {
      setState(() {
        _query = '';
        _found = null;
        _searching = false;
        _error = '';
      });
      return;
    }
    // Two letters is where a search starts being about something.
    if (text.length < 2) return;
    _typing = Timer(kSearchPause, () => _search(text));
  }

  Future<void> _search(String q) async {
    setState(() {
      _query = q;
      _searching = true;
      _error = '';
    });
    try {
      final res = await widget.link.reach('POST', '/api/talk/search',
          body: widget.body({'query': q, 'limit': 40}));
      if (!mounted || _query != q) return;   // they kept typing; this answer is stale
      if (res['ok'] != true) {
        setState(() => _error =
            tr((res['detail'] ?? tr('No pude buscar en las conversaciones.')).toString()));
        return;
      }
      setState(() {
        _found = [
          for (final r in (res['sessions'] as List? ?? []))
            if (r is Map) r.cast<String, dynamic>(),
        ];
        _scanned = (res['scanned'] as num?)?.toInt() ?? 0;
      });
    } catch (e) {
      if (mounted && _query == q) setState(() => _error = humanError(e));
    } finally {
      if (mounted && _query == q) setState(() => _searching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _shown;
    return SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 8),
          child: Row(children: [
            Text(tr('Conversaciones anteriores'),
                style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                    color: context.c.ink)),
          ]),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
          child: TextField(
            controller: _field,
            onChanged: _typed,
            textInputAction: TextInputAction.search,
            onSubmitted: (q) {
              _typing?.cancel();
              if (q.trim().length >= 2) _search(q.trim());
            },
            style: TextStyle(color: context.c.ink),
            decoration: InputDecoration(
              isDense: true,
              hintText: tr('Buscar en lo que os dijisteis'),
              hintStyle: TextStyle(color: context.c.muted),
              prefixIcon: Icon(Icons.search, color: context.c.muted, size: 20),
              suffixIcon: _query.isEmpty
                  ? null
                  : IconButton(
                      icon: Icon(Icons.close, color: context.c.muted, size: 18),
                      onPressed: () {
                        _field.clear();
                        _typed('');
                      },
                    ),
              filled: true,
              fillColor: context.c.panel2,
              border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide(color: context.c.line)),
              enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide(color: context.c.line)),
            ),
          ),
        ),
        // Somebody who opens this list looking for 'a different conversation' is often
        // after a new one. Hidden while searching, where it is not what was asked for.
        if (_query.isEmpty) ...[
          ListTile(
            leading: Icon(Icons.edit_square, color: context.c.accent),
            title: Text(tr('Conversación nueva'),
                style: TextStyle(
                    color: context.c.accent, fontWeight: FontWeight.w600)),
            onTap: () => Navigator.pop(context, <String, dynamic>{'new': true}),
          ),
          Divider(height: 1, color: context.c.line),
        ],
        if (_error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 10, 18, 4),
            child: Text(_error,
                style: TextStyle(color: context.c.err, fontSize: 13)),
          ),
        Flexible(child: _list(rows)),
        if (_found != null && !_searching && _scanned > 0)
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 6, 18, 2),
            child: Text(
                trp('Buscado en {0} conversaciones', [_scanned]),
                style: TextStyle(fontSize: 11, color: context.c.muted)),
          ),
        const SizedBox(height: 8),
      ]),
    );
  }

  Widget _list(List<Map<String, dynamic>> rows) {
    if (_searching && _found == null) {
      return const Padding(
        padding: EdgeInsets.all(28),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (rows.isEmpty) {
      if (_loading) {
        return const Padding(
          padding: EdgeInsets.all(28),
          child: Center(child: CircularProgressIndicator()),
        );
      }
      return Padding(
        padding: const EdgeInsets.fromLTRB(28, 18, 28, 28),
        child: Text(
            _query.isEmpty
                ? tr('Todavía no hay conversaciones anteriores.')
                : tr('Nada que diga eso.'),
            textAlign: TextAlign.center,
            style: TextStyle(color: context.c.muted)),
      );
    }
    // One extra row at the end while more are on the way, so the list says it is still
    // filling rather than appearing to end early.
    final busy = _found == null && (_loading || _more);
    return ListView.builder(
      controller: _scroll,
      shrinkWrap: true,
      itemCount: rows.length + (busy ? 1 : 0),
      itemBuilder: (c, i) {
        if (i >= rows.length) {
          return const Padding(
            padding: EdgeInsets.symmetric(vertical: 18),
            child: Center(
                child: SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))),
          );
        }
        return _row(rows[i]);
      },
    );
  }

  Widget _row(Map<String, dynamic> r) {
    final telegram = (r['source'] ?? '') == 'telegram';
    final snippet = r['snippet'];
    return ListTile(
      leading: Icon(telegram ? Icons.send : Icons.chat_bubble_outline,
          color: context.c.muted),
      title: Text(conversationTitle(r),
          maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(conversationWhen(r),
              style: TextStyle(fontSize: 12, color: context.c.muted)),
          if (snippet is Map) _snippet(snippet.cast<String, dynamic>()),
        ],
      ),
      isThreeLine: snippet is Map,
      onTap: () => Navigator.pop(context, r),
    );
  }

  /// The matched words, with what surrounds them.
  ///
  /// Three parts rather than one string the app searches again: the computer cut them
  /// where it found the match, which is the only place that is certainly right - it
  /// matched without accents and without case, and finding it a second time here would
  /// mean writing that same folding twice in two languages.
  Widget _snippet(Map<String, dynamic> s) {
    final style = TextStyle(fontSize: 12, color: context.c.muted);
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: RichText(
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        text: TextSpan(style: style, children: [
          TextSpan(text: (s['before'] ?? '').toString()),
          TextSpan(
              text: (s['hit'] ?? '').toString(),
              style: style.copyWith(
                  color: context.c.accent, fontWeight: FontWeight.w700)),
          TextSpan(text: (s['after'] ?? '').toString()),
        ]),
      ),
    );
  }
}

/// What to call a conversation nobody named.
String conversationTitle(Map<String, dynamic> r) {
  final t = (r['title'] ?? '').toString().trim();
  if (t.isNotEmpty) return t;
  final p = (r['preview'] ?? '').toString().trim().replaceAll(RegExp(r'\s+'), ' ');
  if (p.isNotEmpty) return p.length > 70 ? '${p.substring(0, 70)}…' : p;
  return tr('Sin título');
}

/// When it happened, and where it happened. Lifted out of the chat screen unchanged -
/// the wording is already translated and a sheet that said it differently would be a
/// second vocabulary for the same fact.
String conversationWhen(Map<String, dynamic> r) {
  final secs = (r['last_active'] ?? r['started_at']);
  final n = (r['message_count'] as num?)?.toInt() ?? 0;
  final where = (r['source'] ?? '') == 'telegram' ? 'Telegram' : 'Olivaw';
  if (secs is! num) return where;
  final when = DateTime.fromMillisecondsSinceEpoch((secs * 1000).round());
  final ago = DateTime.now().difference(when);
  String rel;
  if (ago.inMinutes < 60) {
    rel = trp('hace {0} min', [ago.inMinutes]);
  } else if (ago.inHours < 24) {
    rel = trp('hace {0} h', [ago.inHours]);
  } else if (ago.inDays < 30) {
    rel = trp('hace {0} d', [ago.inDays]);
  } else {
    rel = '${when.day}/${when.month}/${when.year}';
  }
  return n > 0
      ? '$where · $rel · ${trp('{0} mensajes', [n])}'
      : '$where · $rel';
}
