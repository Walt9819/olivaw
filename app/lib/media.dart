// Everything this conversation has carried: pictures, notes, files.
//
// WhatsApp's "media, links and docs", for the same reason it exists there - once a
// conversation is a few hundred messages long, "the deck he sent me" is findable by
// scrolling and by nothing else. Three tabs rather than one list, because looking for a
// picture and looking for a PDF are different errands.
//
// The files come from the messages already on screen, not from a new request: the
// computer sent each one's name, kind, size and fetch id along with the turn it belonged
// to, so this is a different arrangement of what is already here.

import 'package:flutter/material.dart';

import 'attachment.dart';
import 'link.dart';
import 'strings.dart';
import 'theme.dart';

class MediaScreen extends StatelessWidget {
  const MediaScreen({
    super.key,
    required this.link,
    required this.messages,
    required this.body,
  });

  final Link link;
  final List<Map<String, dynamic>> messages;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;

  /// Every file in the conversation, newest first, with who sent it.
  List<Map<String, dynamic>> get _all {
    final out = <Map<String, dynamic>>[];
    for (final m in messages) {
      for (final f in (m['files'] as List? ?? [])) {
        if (f is Map) {
          out.add({...f.cast<String, dynamic>(), '_mine': m['role'] == 'user'});
        }
      }
    }
    return out.reversed.toList();
  }

  @override
  Widget build(BuildContext context) {
    final all = _all;
    final images = all.where((f) => f['kind'] == 'image').toList();
    final audio = all.where((f) => f['kind'] == 'audio').toList();
    final files = all.where((f) => f['kind'] != 'image' && f['kind'] != 'audio').toList();
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: Text(tr('Fotos y archivos')),
          bottom: TabBar(
            tabs: [
              Tab(text: '${tr('Fotos')} (${images.length})'),
              Tab(text: '${tr('Archivos')} (${files.length})'),
              Tab(text: '${tr('Notas')} (${audio.length})'),
            ],
          ),
        ),
        body: TabBarView(children: [
          _Grid(link: link, body: body, files: images),
          _Rows(link: link, body: body, files: files),
          _Rows(link: link, body: body, files: audio),
        ]),
      ),
    );
  }
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
      itemBuilder: (c, i) => ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: Attachment(
          link: link,
          file: files[i],
          body: body,
          ink: c.c.ink,
          thumbnail: true,
        ),
      ),
    );
  }
}

class _Rows extends StatelessWidget {
  const _Rows({required this.link, required this.body, required this.files});

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final List<Map<String, dynamic>> files;

  @override
  Widget build(BuildContext context) {
    if (files.isEmpty) return const _Empty();
    return ListView.separated(
      itemCount: files.length,
      separatorBuilder: (c, i) => Divider(height: 1, color: c.c.line),
      itemBuilder: (c, i) {
        final f = files[i];
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(children: [
            Icon(
                f['kind'] == 'audio'
                    ? Icons.mic_none
                    : Icons.insert_drive_file_outlined,
                color: c.c.muted),
            const SizedBox(width: 12),
            Expanded(
              child: Attachment(
                link: link,
                file: f,
                body: body,
                ink: c.c.ink,
              ),
            ),
          ]),
        );
      },
    );
  }
}
