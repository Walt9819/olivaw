// Everything this agent has: pictures, notes, files.
//
// WhatsApp's "media, links and docs", for the same reason it exists there - once a
// conversation is a few hundred messages long, "the deck he sent me" is findable by
// scrolling and by nothing else. Three tabs rather than one list, because looking for
// a picture and looking for a PDF are different errands.
//
// Two places to look, and why
// ---------------------------
// This started as a rearrangement of what the phone already had: the computer sends
// each file's name, kind, size and fetch id along with the turn it belonged to, so the
// gallery could be built out of the messages on screen without asking for anything.
// Which was honest, and wrong in the place it is mostly opened from. It is reached from
// the AGENT's settings, and from there "files" quietly meant "the files of whichever
// conversation happens to be open" - so an agent three months and two hundred
// conversations old showed four of them, and the report it wrote last week was not
// among them.
//
// So there are two scopes and a switch between them. THIS CONVERSATION is the old
// behaviour, and it keeps the one thing the other cannot have: every file knows which
// message it arrived with, so you can go back to what was being said around it.
// EVERYTHING is the agent's own folder, read from the computer - which is where its
// work actually accumulates, and the only answer to "find me that report".

import 'package:flutter/material.dart';

import 'attachment.dart';
import 'link.dart';
import 'strings.dart';
import 'teams.dart' show since;
import 'theme.dart';

/// Which pile of files is being looked at.
enum Pile {
  /// The conversation that is open. Each file knows where it came from.
  thread,

  /// Everything in the agent's workspace, however old and whatever thread it was
  /// made in.
  agent,
}

class MediaScreen extends StatefulWidget {
  const MediaScreen({
    super.key,
    required this.link,
    required this.messages,
    required this.body,
    this.start = Pile.thread,
    this.agentName = '',
  });

  final Link link;
  final List<Map<String, dynamic>> messages;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;

  /// Which pile to open on. From the agent's settings that is everything it has;
  /// from inside a conversation it is that conversation.
  final Pile start;

  final String agentName;

  @override
  State<MediaScreen> createState() => _MediaScreenState();
}

class _MediaScreenState extends State<MediaScreen> {
  late Pile _pile = widget.start;

  /// The agent's own folder, once it has been asked for. Null means not yet.
  List<Map<String, dynamic>>? _theirs;
  String _failed = '';
  bool _busy = false;
  String _where = '';

  @override
  void initState() {
    super.initState();
    if (_pile == Pile.agent) _load();
  }

  Future<void> _load() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _failed = '';
    });
    try {
      final res = await widget.link.reach('POST', '/api/talk/outbox',
          body: widget.body({'limit': 300}));
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() => _failed =
            tr((res['detail'] ?? tr('No pude leer su carpeta.')).toString()));
        return;
      }
      setState(() {
        _where = (res['folder'] ?? '').toString();
        _theirs = [
          for (final f in (res['files'] as List? ?? []))
            if (f is Map) f.cast<String, dynamic>(),
        ];
        // An agent with a folder and nothing in it is not an error, and the empty
        // state below says so better than a red line would.
        _failed = (res['detail'] ?? '').toString();
      });
    } catch (e) {
      if (mounted) setState(() => _failed = humanError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Every file in the conversation, newest first, with who sent it, the first
  /// words of the message it arrived with, and where that message is.
  ///
  /// The words are the point. 'presentacion-v3.pdf' six weeks later is a filename;
  /// 'esto es lo que le enseñé al cliente' is the thing you are looking for.
  List<Map<String, dynamic>> get _inThread {
    final out = <Map<String, dynamic>>[];
    for (var i = 0; i < widget.messages.length; i++) {
      final m = widget.messages[i];
      for (final f in (m['files'] as List? ?? [])) {
        if (f is Map) {
          out.add({
            ...f.cast<String, dynamic>(),
            '_mine': m['role'] == 'user',
            '_said': _firstWords((m['text'] ?? '').toString()),
            '_at': i,
          });
        }
      }
    }
    return out.reversed.toList();
  }

  /// One line of it. Markdown and newlines flattened, because this is a caption in a
  /// list, not a message.
  static String _firstWords(String text) {
    final flat = text
        .replaceAll(RegExp(r'[`*_#>]'), ' ')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    if (flat.isEmpty) return '';
    if (flat.length <= 90) return flat;
    return '${flat.substring(0, 89).trimRight()}…';
  }

  @override
  Widget build(BuildContext context) {
    final all = _pile == Pile.thread ? _inThread : (_theirs ?? const []);
    final images = all.where((f) => f['kind'] == 'image').toList();
    final audio = all.where((f) => f['kind'] == 'audio').toList();
    final files =
        all.where((f) => f['kind'] != 'image' && f['kind'] != 'audio').toList();
    final loading = _pile == Pile.agent && _theirs == null && _busy;
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: Text(tr('Fotos y archivos')),
          actions: [
            if (_pile == Pile.agent)
              IconButton(
                icon: const Icon(Icons.refresh),
                tooltip: tr('Volver a mirar'),
                onPressed: _busy ? null : _load,
              ),
          ],
          bottom: TabBar(
            tabs: [
              Tab(text: '${tr('Fotos')} (${images.length})'),
              Tab(text: '${tr('Archivos')} (${files.length})'),
              Tab(text: '${tr('Notas')} (${audio.length})'),
            ],
          ),
        ),
        body: Column(children: [
          _Switch(
            pile: _pile,
            agentName: widget.agentName,
            onPick: (p) {
              setState(() => _pile = p);
              if (p == Pile.agent && _theirs == null) _load();
            },
          ),
          if (_failed.isNotEmpty && _theirs == null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
              child: Text(_failed,
                  style: TextStyle(fontSize: 12, color: context.c.muted)),
            ),
          Expanded(
            child: loading
                ? const Center(child: CircularProgressIndicator())
                : TabBarView(children: [
                    _Grid(link: widget.link, body: widget.body, files: images),
                    _Rows(
                        link: widget.link,
                        body: widget.body,
                        files: files,
                        where: _pile == Pile.agent ? _where : ''),
                    _Rows(
                        link: widget.link,
                        body: widget.body,
                        files: audio,
                        where: _pile == Pile.agent ? _where : ''),
                  ]),
          ),
        ]),
      ),
    );
  }
}

/// Which pile, as two words you press rather than a menu you go looking for.
class _Switch extends StatelessWidget {
  const _Switch({required this.pile, required this.onPick, required this.agentName});

  final Pile pile;
  final String agentName;
  final void Function(Pile) onPick;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
        child: SegmentedButton<Pile>(
          showSelectedIcon: false,
          style: const ButtonStyle(
            visualDensity: VisualDensity.compact,
            textStyle: WidgetStatePropertyAll(TextStyle(fontSize: 12.5)),
          ),
          segments: [
            ButtonSegment(
                value: Pile.thread, label: Text(tr('Esta conversación'))),
            ButtonSegment(
                value: Pile.agent,
                label: Text(agentName.isEmpty
                    ? tr('Todo')
                    : trp('Todo lo de {0}', [agentName]))),
          ],
          selected: {pile},
          onSelectionChanged: (s) => onPick(s.first),
        ),
      );
}

class _Empty extends StatelessWidget {
  const _Empty();
  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(tr('Nada por aquí todavía.'),
              textAlign: TextAlign.center,
              style: TextStyle(color: context.c.muted)),
        ),
      );
}

class _Grid extends StatelessWidget {
  const _Grid({required this.link, required this.body, required this.files});

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final List<Map<String, dynamic>> files;

  @override
  Widget build(BuildContext context) {
    if (files.isEmpty) return const _Empty();
    return GridView.builder(
      padding: const EdgeInsets.all(8),
      gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
        // By width rather than a fixed count, so the Fold's inner screen uses the room
        // it has instead of showing three enormous thumbnails.
        maxCrossAxisExtent: 170,
        crossAxisSpacing: 8,
        mainAxisSpacing: 8,
      ),
      itemCount: files.length,
      itemBuilder: (c, i) => Stack(
        fit: StackFit.expand,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Attachment(
              link: link,
              file: files[i],
              body: body,
              ink: c.c.ink,
              thumbnail: true,
            ),
          ),
          // A corner button rather than a tap on the picture: tapping a photo means
          // 'show me the photo', and taking that away to mean something else is how
          // a gallery stops behaving like one.
          if (files[i]['_at'] != null)
            Positioned(
              right: 2,
              bottom: 2,
              child: Material(
                color: Colors.black.withValues(alpha: 0.45),
                shape: const CircleBorder(),
                child: IconButton(
                  iconSize: 16,
                  visualDensity: VisualDensity.compact,
                  tooltip: tr('Ver en la conversación'),
                  icon: const Icon(Icons.forum_outlined, color: Colors.white),
                  onPressed: () => Navigator.pop(c, files[i]['_at'] as int),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _Rows extends StatelessWidget {
  const _Rows({
    required this.link,
    required this.body,
    required this.files,
    this.where = '',
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final List<Map<String, dynamic>> files;

  /// The agent's folder, when these came from it: every row sits under it, so the
  /// path is said once at the top rather than on each of forty lines.
  final String where;

  @override
  Widget build(BuildContext context) {
    if (files.isEmpty) return const _Empty();
    return ListView.separated(
      itemCount: files.length + (where.isEmpty ? 0 : 1),
      separatorBuilder: (c, i) => Divider(height: 1, color: c.c.line),
      itemBuilder: (c, i) {
        if (where.isNotEmpty && i == 0) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: Text(where,
                style: TextStyle(fontSize: 11, color: c.c.muted),
                overflow: TextOverflow.ellipsis),
          );
        }
        final f = files[i - (where.isEmpty ? 0 : 1)];
        // Where it is and when it was made, for the pile that has no conversation to
        // point at. A folder six weeks later is most of what distinguishes two files
        // called informe.pdf.
        final rel = (f['rel'] ?? '').toString();
        final at = (f['at'] as num?) ?? 0;
        final trail = [
          if (rel.contains('/')) rel.substring(0, rel.lastIndexOf('/')),
          if (at > 0) since(at),
        ].where((s) => s.isNotEmpty).join(' · ');
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              // The attachment draws its own badge now - a round icon with the
              // extension on it, the size of a thumb. It used to be a 16-pixel
              // outline next to this row's own grey icon, which was two small icons
              // saying the same thing and neither of them visible at arm's length.
              Attachment(link: link, file: f, body: body, ink: c.c.ink),
              if (trail.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(left: 52, bottom: 2),
                  child: Text(trail,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11, color: c.c.muted)),
                ),
              Padding(
                padding: const EdgeInsets.only(left: 52),
                child: _Said(said: (f['_said'] ?? '').toString(), at: f['_at']),
              ),
            ],
          ),
        );
      },
    );
  }
}

/// The first words of the message a file arrived with, and the way back to it.
///
/// Tapping the FILE opens the file - that is what tapping a file means. Tapping what
/// was said around it goes to the conversation, which is the other thing somebody
/// wants from a gallery and has nowhere else to live.
class _Said extends StatelessWidget {
  const _Said({required this.said, required this.at});

  final String said;
  final Object? at;

  @override
  Widget build(BuildContext context) {
    if (said.isEmpty || at is! int) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 2, bottom: 4),
      child: InkWell(
        onTap: () => Navigator.pop(context, at as int),
        child: Row(children: [
          Icon(Icons.subdirectory_arrow_right, size: 13, color: context.c.muted),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              said,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, color: context.c.muted),
            ),
          ),
        ]),
      ),
    );
  }
}
