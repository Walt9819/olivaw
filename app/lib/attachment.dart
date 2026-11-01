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
          // `contain`, not `cover`. Cover CROPS to fill the box, and the box was 260
          // tall - so a screenshot of a page, which is the commonest thing an agent
          // sends, arrived as its middle third with the part worth reading cut off
          // both ends. Tall enough to see what it is, short enough that eighteen
          // slides in one message are still a message rather than a scroll; tap for
          // the whole thing.
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 320),
            child: Image.memory(
              bytes,
              fit: BoxFit.contain,
              alignment: Alignment.topLeft,
              errorBuilder: (c, e, st) => _row(c),
            ),
          ),
        ),
      ),
    );
  }

  /// Full size, on black, with the name - the one place the detail of a slide is
  /// legible, so it has to be a viewer rather than a bigger copy of the thumbnail.
  void _open(BuildContext context, Uint8List bytes) {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (c) => _Viewer(name: _name, bytes: bytes, onSave: () => _save(c, bytes)),
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

  /// What a file of this name is, in three letters. Drawn on the badge, because the
  /// extension is how somebody knows at a glance whether they are looking at the deck
  /// or at the spreadsheet - and a generic page icon says neither.
  String get ext {
    final dot = _name.lastIndexOf('.');
    if (dot < 0 || dot == _name.length - 1) return '';
    final e = _name.substring(dot + 1).toUpperCase();
    return e.length <= 4 ? e : e.substring(0, 4);
  }

  IconData get _glyph {
    switch (ext) {
      case 'PDF':
        return Icons.picture_as_pdf_outlined;
      case 'DOC':
      case 'DOCX':
      case 'ODT':
      case 'RTF':
        return Icons.description_outlined;
      case 'XLS':
      case 'XLSX':
      case 'CSV':
      case 'ODS':
        return Icons.table_chart_outlined;
      case 'PPT':
      case 'PPTX':
      case 'ODP':
        return Icons.slideshow_outlined;
      case 'ZIP':
      case 'RAR':
      case '7Z':
      case 'TAR':
      case 'GZ':
        return Icons.folder_zip_outlined;
      case 'MP3':
      case 'M4A':
      case 'OGG':
      case 'WAV':
      case 'OPUS':
        return Icons.graphic_eq;
      case 'MP4':
      case 'MOV':
      case 'MKV':
      case 'WEBM':
        return Icons.movie_outlined;
      case 'MD':
      case 'TXT':
      case 'LOG':
        return Icons.article_outlined;
      case 'HTML':
      case 'HTM':
        return Icons.language;
      case 'JSON':
      case 'PY':
      case 'JS':
      case 'DART':
      case 'SH':
        return Icons.code;
      default:
        return Icons.insert_drive_file_outlined;
    }
  }

  Widget _rowBody(BuildContext context, bool busy, VoidCallback? onTap) {
    final human = (file['human'] ?? '').toString();
    final folder = (file['folder'] ?? '').toString();
    // The computer could not find it any more. Weeks after a conversation that is
    // ordinary - things get tidied - and the honest thing is the name and the fact.
    final missing = file['missing'] == true;
    final fetchable = _id.isNotEmpty && !missing;
    // A badge the size of a thumb, not a 16-pixel outline. A file somebody sent is an
    // object in the conversation - that is how it reads in every messaging app the
    // owner already uses, and why the old row, which was the smallest thing on the
    // screen, kept being missed entirely on the Fold's outer display.
    final badge = SizedBox(
      width: 42,
      height: 42,
      child: busy
          ? Center(
              child: SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2.2, color: ink),
              ),
            )
          : DecoratedBox(
              decoration: BoxDecoration(
                color: ink.withValues(alpha: missing ? 0.06 : 0.12),
                shape: BoxShape.circle,
              ),
              child: Stack(
                alignment: Alignment.center,
                children: [
                  Icon(missing ? Icons.broken_image_outlined : _glyph,
                      size: 20,
                      color: ink.withValues(alpha: missing ? 0.5 : 0.95)),
                  // The extension across the bottom of the badge, where a glance
                  // finds it. Only when it is short enough to be read at this size.
                  if (!missing && ext.isNotEmpty && ext.length <= 4)
                    Positioned(
                      bottom: 3,
                      child: Text(ext,
                          style: TextStyle(
                              fontSize: 7.5,
                              height: 1,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 0.2,
                              color: ink.withValues(alpha: 0.75))),
                    ),
                ],
              ),
            ),
    );
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: (fetchable && !busy) ? onTap : null,
        onLongPress: fetchable ? () => saveCopy(context) : null,
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          badge,
          const SizedBox(width: 10),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(_name,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: 14,
                        height: 1.25,
                        fontWeight: FontWeight.w600,
                        color: ink)),
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
                    style: TextStyle(fontSize: 12, color: ink.withValues(alpha: 0.7)),
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


/// A picture, full size, with the gestures a phone actually has.
///
/// The old one was an InteractiveViewer inside a `Center` and nothing else, which in
/// practice meant it could not be zoomed: pinching needs two thumbs on a phone usually
/// held in one hand, there was no double-tap at all, and once magnified the image was
/// laid out as a centred child so the parts you had zoomed towards sat outside the box
/// where no amount of dragging would reach them.
///
/// So: double-tap to go in - at the point touched, not at the middle, which is the
/// difference between magnifying a screenshot and magnifying the bit of it you are
/// trying to read - double-tap again to come back out, pinch still works for anyone
/// using both hands, and the panning boundary is loose enough that the edges of a
/// zoomed picture are reachable.
class _Viewer extends StatefulWidget {
  const _Viewer({required this.name, required this.bytes, required this.onSave});

  final String name;
  final Uint8List bytes;
  final VoidCallback onSave;

  @override
  State<_Viewer> createState() => _ViewerState();
}

class _ViewerState extends State<_Viewer> with SingleTickerProviderStateMixin {
  final _view = TransformationController();
  late final AnimationController _anim = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 180))
    ..addListener(() {
      if (_run != null) _view.value = _run!.value;
    });
  Animation<Matrix4>? _run;

  /// How far in a double-tap goes. Three is the point where the text in a screenshot
  /// of a page becomes readable on a phone, which is what this gesture is for.
  static const double _zoomed = 3.0;

  @override
  void dispose() {
    _anim.dispose();
    _view.dispose();
    super.dispose();
  }

  bool get _isZoomed => _view.value.getMaxScaleOnAxis() > 1.05;

  void _glide(Matrix4 to) {
    _run = Matrix4Tween(begin: _view.value, end: to)
        .animate(CurveTween(curve: Curves.easeOutCubic).animate(_anim));
    _anim.forward(from: 0);
  }

  /// Zoom towards the point that was tapped, which is the whole trick: zooming to the
  /// centre and making the owner drag to what they were pointing at is the behaviour
  /// that made the old viewer feel broken.
  void _doubleTap(TapDownDetails d) {
    if (_isZoomed) {
      _glide(Matrix4.identity());
      return;
    }
    final at = d.localPosition;
    _glide(Matrix4.identity()
      ..translateByDouble(-at.dx * (_zoomed - 1), -at.dy * (_zoomed - 1), 0, 1)
      ..scaleByDouble(_zoomed, _zoomed, _zoomed, 1));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(widget.name, style: const TextStyle(fontSize: 15)),
        actions: [
          IconButton(
            icon: const Icon(Icons.download),
            tooltip: tr('Guardar'),
            onPressed: widget.onSave,
          ),
        ],
      ),
      body: GestureDetector(
        onDoubleTapDown: _doubleTap,
        // The handler has to exist for onDoubleTapDown to fire at all.
        onDoubleTap: () {},
        child: InteractiveViewer(
          transformationController: _view,
          // Room to go past the edges while zoomed. Without this the corners of a
          // magnified image are simply not reachable.
          boundaryMargin: const EdgeInsets.all(double.infinity),
          minScale: 1,
          // Eight, not five: an agent's screenshot of a dashboard has eight-pixel
          // labels on it, and five was still not enough to read them.
          maxScale: 8,
          clipBehavior: Clip.none,
          child: SizedBox.expand(
            child: Image.memory(widget.bytes, fit: BoxFit.contain),
          ),
        ),
      ),
    );
  }
}
