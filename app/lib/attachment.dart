// A file inside a message: shown, not merely named.
//
// The computer hands over each file as a small record - name, kind, size, and an id to
// fetch it by when it is small enough to be worth fetching. An image fetches itself and
// appears in the bubble the way a photo does in any messaging app; anything else is a
// row you can tap to save. A file with no id (too big to send over the wire) says where
// it is on the computer instead of pretending it can be opened here.
//
// Bytes are cached for the life of the screen, because a conversation that is scrolled
// back through would otherwise refetch the same eighteen slides every time they pass.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'link.dart';
import 'native.dart';
import 'theme.dart';
import 'loading.dart';
import 'strings.dart';

/// Fetched file bytes, by grant id. Deliberately not persisted: an id is only good while
/// the computer still remembers granting it.
final Map<String, Uint8List> _cache = {};

class Attachment extends StatelessWidget {
  const Attachment({
    super.key,
    required this.link,
    required this.file,
    required this.body,
    required this.ink,
    this.thumbnail = false,
  });

  final Link link;
  final Map<String, dynamic> file;

  /// The body to send with the fetch, so the computer knows which agent is being asked.
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final Color ink;

  /// In a grid: fill the cell and crop, with no bubble padding. A picture in a
  /// conversation wants its own shape; a picture in a gallery wants to tile.
  final bool thumbnail;

  String get _name => (file['name'] ?? 'archivo').toString();
  String get _id => (file['id'] ?? '').toString();
  bool get _isImage =>
      (file['kind'] ?? '').toString() == 'image' ||
      (file['data_url'] ?? '').toString().startsWith('data:image');

  @override
  Widget build(BuildContext context) {
    // An image small enough to have travelled inline is already here; nothing to fetch.
    final inline = (file['data_url'] ?? '').toString();
    if (_isImage && inline.startsWith('data:')) {
      final comma = inline.indexOf(',');
      if (comma > 0) {
        try {
          return _frame(context, base64Decode(inline.substring(comma + 1)));
        } catch (_) {
          // A data URL we cannot decode is not worth a crash; fall through to the row.
        }
      }
    }
    // A missing image has nothing to preview; it falls through to the named row.
    if (_isImage && _id.isNotEmpty && file['missing'] != true) {
      return _ImagePreview(this);
    }
    return _row(context);
  }

  Widget _frame(BuildContext context, Uint8List bytes) {
    if (thumbnail) {
      return GestureDetector(
        onTap: () => _open(context, bytes),
        child: SizedBox.expand(
          child: Image.memory(bytes,
              fit: BoxFit.cover, errorBuilder: (c, e, st) => _row(c)),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: GestureDetector(
          onTap: () => _open(context, bytes),
          // Tall enough to see what it is, short enough that eighteen slides in one
          // message are still a message rather than a scroll. Tap for the full size.
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 260),
            child: Image.memory(
              bytes,
              fit: BoxFit.cover,
              errorBuilder: (c, e, st) => _row(c),
            ),
          ),
        ),
      ),
    );
  }

  /// Full size, on black, with the name - the one place the detail of a slide is legible.
  void _open(BuildContext context, Uint8List bytes) {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (c) => Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          backgroundColor: Colors.black,
          foregroundColor: Colors.white,
          title: Text(_name, style: const TextStyle(fontSize: 15)),
          actions: [
            IconButton(
              icon: const Icon(Icons.download),
              tooltip: tr('Guardar'),
              onPressed: () => _save(c, bytes),
            ),
          ],
        ),
        body: Center(
          child: InteractiveViewer(
            minScale: 0.8,
            maxScale: 5,
            child: Image.memory(bytes),
          ),
        ),
      ),
    ));
  }

  Future<void> _save(BuildContext context, Uint8List bytes) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final where = await Native.save(_name, bytes);
      messenger.showSnackBar(SnackBar(content: Text(trp('Guardado en {0}', [where]))));
    } on NativeError catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  /// A file nobody has tapped yet. The row is its own widget because tapping it
  /// starts a fetch that can take seconds on mobile data, and the old one had
  /// nowhere to put a spinner - so a tap looked like nothing had happened, and the
  /// obvious response is to tap again.
  Widget _row(BuildContext context) => _FileRow(this);

  Widget _rowBody(BuildContext context, bool busy, VoidCallback? onTap) {
    final human = (file['human'] ?? '').toString();
    final folder = (file['folder'] ?? '').toString();
    // The computer could not find it any more. Weeks after a conversation that is
    // ordinary - things get tidied - and the honest thing is the name and the fact.
    final missing = file['missing'] == true;
    final fetchable = _id.isNotEmpty && !missing;
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: InkWell(
        onTap: (fetchable && !busy) ? onTap : null,
        onLongPress: fetchable ? () => saveCopy(context) : null,
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (busy)
            SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 2, color: ink),
            )
          else
            Icon(
                missing
                    ? Icons.broken_image_outlined
                    : fetchable
                        ? Icons.download_outlined
                        : Icons.insert_drive_file_outlined,
                size: 16,
                color: ink.withValues(alpha: missing ? 0.6 : 1.0)),
          const SizedBox(width: 6),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(_name,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: 13,
                        color: ink,
                        decoration: fetchable ? TextDecoration.underline : null,
                        decorationColor: ink.withValues(alpha: 0.4))),
                if (busy)
                  Text(
                    human.isEmpty
                        ? tr('Trayéndolo…')
                        : trp('Trayéndolo… {0}', [human]),
                    style: TextStyle(fontSize: 11, color: ink.withValues(alpha: 0.7)),
                  )
                else if (human.isNotEmpty || !fetchable)
                  Text(
                    missing
                        ? 'ya no está en la computadora'
                        : fetchable
                            ? human
                            // Too big to come down the wire. Saying where it is beats a
                            // button that cannot work.
                            : [human, if (folder.isNotEmpty) 'en la computadora']
                                .where((s) => s.isNotEmpty)
                                .join(' · '),
                    style: TextStyle(fontSize: 11, color: ink.withValues(alpha: 0.7)),
                  ),
              ],
            ),
          ),
        ]),
      ),
    );
  }

  Future<Uint8List?> fetch() async {
    if (_id.isEmpty) return null;
    final hit = _cache[_id];
    if (hit != null) return hit;
    final res = await link.reach('POST', '/api/talk/file',
        body: body({'id': _id}), wait: Link.thinkingWait);
    if (res['ok'] != true) {
      throw LinkError(tr((res['detail'] ?? tr('No pude traer ese archivo.')).toString()));
    }
    final b64 = (res['data_b64'] ?? '').toString();
    if (b64.isEmpty) throw LinkError(tr('Ese archivo llegó vacío.'));
    final bytes = base64Decode(b64);
    _cache[_id] = bytes;
    return bytes;
  }

  /// Tapping a file OPENS it. Saving is the long press.
  ///
  /// An HTML page is the case that made this obvious: downloading it, finding it in
  /// Downloads and picking a browser is four steps to read a thing somebody sent you,
  /// and three of them are about file management rather than about the page.
  Future<void> _download(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final bytes = await fetch();
      if (bytes == null) return;
      await Native.openFile(_name, bytes);
    } on NativeError catch (e) {
      // Nothing on the phone can open it: saving it is the next best thing, and the
      // owner still ends up with the file.
      if (e.code == 'noapp' || e.code == 'kind') {
        try {
          final bytes = await fetch();
          if (bytes == null) return;
          final where = await Native.save(_name, bytes);
          messenger.showSnackBar(
              SnackBar(content: Text(trp('Guardado en {0}', [where]))));
          return;
        } on NativeError catch (e2) {
          messenger.showSnackBar(SnackBar(content: Text(e2.message)));
          return;
        }
      }
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(humanError(e))));
    }
  }

  /// Keep a copy, from the long press.
  Future<void> saveCopy(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final bytes = await fetch();
      if (bytes == null) return;
      final where = await Native.save(_name, bytes);
      messenger.showSnackBar(SnackBar(content: Text(trp('Guardado en {0}', [where]))));
    } on NativeError catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(humanError(e))));
    }
  }
}

/// An image that fetches itself once and then stays put.
class _FileRow extends StatefulWidget {
  const _FileRow(this.att);
  final Attachment att;

  @override
  State<_FileRow> createState() => _FileRowState();
}

class _FileRowState extends State<_FileRow> {
  bool _busy = false;

  Future<void> _tap() async {
    setState(() => _busy = true);
    try {
      await widget.att._download(context);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) =>
      widget.att._rowBody(context, _busy, _tap);
}

class _ImagePreview extends StatefulWidget {
  const _ImagePreview(this.att);
  final Attachment att;

  @override
  State<_ImagePreview> createState() => _ImagePreviewState();
}

class _ImagePreviewState extends State<_ImagePreview> {
  Uint8List? _bytes;
  String _failed = '';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (_busy) return;
    _busy = true;
    try {
      final b = await widget.att.fetch();
      if (mounted) setState(() => _bytes = b);
    } catch (e) {
      if (mounted) setState(() => _failed = humanError(e));
    } finally {
      _busy = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = _bytes;
    if (b != null) return widget.att._frame(context, b);
    if (_failed.isNotEmpty) return widget.att._row(context);
    // A placeholder the size of a small picture, so the bubble does not jump when the
    // image lands.
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Container(
        height: 120,
        decoration: BoxDecoration(
          color: context.c.panel2,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: context.c.line),
        ),
        child: Center(
          // With the size on it: 40 kB that is slow is a bad connection and 4 MB
          // that is slow is simply 4 MB, and those want different patience.
          child: Busy(label: (widget.att.file['human'] ?? '').toString()),
        ),
      ),
    );
  }
}
