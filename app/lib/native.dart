// The Dart end of the three things this app asks Android for.
//
// See MainActivity.kt for why these are a hand-written channel rather than three
// packages. Every call here can fail for a reason the owner caused - they cancelled the
// picker, they declined the microphone - and none of those is an error worth a red
// banner, so the shapes below separate "nothing happened" from "something broke".

import 'package:flutter/services.dart';
import 'strings.dart';

/// One file on its way to an agent: what it is called, and what is in it.
class PickedFile {
  const PickedFile(this.name, this.bytes);
  final String name;
  final Uint8List bytes;

  int get size => bytes.length;

  /// Rough, and only used to choose an icon.
  bool get isImage {
    final n = name.toLowerCase();
    return n.endsWith('.png') ||
        n.endsWith('.jpg') ||
        n.endsWith('.jpeg') ||
        n.endsWith('.gif') ||
        n.endsWith('.webp') ||
        n.endsWith('.bmp');
  }
}

/// Raised when the platform refused for a reason worth telling the owner about.
class NativeError implements Exception {
  NativeError(this.code, this.message);
  final String code;
  final String message;
  @override
  String toString() => message;
}

class Native {
  static const _ch = MethodChannel('olivaw/native');

  /// Open an http(s) link in whatever the phone uses for the web.
  ///
  /// Anything else is refused on the Android side: a message is text written by a model,
  /// and a scheme like `intent://` would make any message able to open anything on this
  /// phone that answers to it.
  static Future<void> openUrl(String url) async {
    try {
      await _ch.invokeMethod('openUrl', {'url': url});
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? tr('No pude abrir ese enlace.'));
    }
  }

  /// The system picker. Empty list means the owner backed out, which is not an error.
  static Future<List<PickedFile>> pick({String mime = '*/*'}) async {
    try {
      final raw = await _ch.invokeMethod<List<dynamic>>('pick', {'mime': mime});
      return [
        for (final f in raw ?? const [])
          PickedFile(
            ((f as Map)['name'] ?? 'archivo').toString(),
            f['bytes'] as Uint8List? ?? Uint8List(0),
          ),
      ];
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? tr('No pude abrir el selector.'));
    }
  }

  /// Hand a file to whatever app opens that kind - an HTML page to the browser, a PDF
  /// to a reader - without it going through Downloads first.
  ///
  /// Saving is still there for keeping a copy. This is for looking at the thing, which
  /// is what most files are actually for.
  static Future<void> openFile(String name, Uint8List bytes) async {
    try {
      await _ch.invokeMethod('openFile', {'name': name, 'bytes': bytes});
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? 'No pude abrir ese archivo.');
    }
  }

  /// Put [bytes] in the phone's Downloads under [name]. Returns where it landed.
  ///
  /// Through MediaStore on the Android side rather than a path: asking for the old
  /// storage permission would be asking for all of the owner's files in order to
  /// deliver one download.
  static Future<String> save(String name, Uint8List bytes) async {
    try {
      final where = await _ch.invokeMethod<String>(
          'save', {'name': name, 'bytes': bytes});
      return where ?? 'Descargas';
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? tr('No pude guardarlo.'));
    }
  }

  /// Keep this app alive while [who] is thinking, and show that it is waiting.
  ///
  /// Best effort on purpose: if the service will not start, the message has still been
  /// sent and the computer will still finish the turn. All that is lost is the wait
  /// surviving the screen going off, which is worth a line in a log and not worth
  /// failing a message over.
  static Future<void> waitStart(String who) async {
    try {
      await _ch.invokeMethod('waitStart', {'who': who});
    } on PlatformException {
      // see above
    }
  }

  static Future<void> waitStop() async {
    try {
      await _ch.invokeMethod('waitStop');
    } on PlatformException {
      // nothing useful to say if stopping fails
    }
  }

  /// Whether notifications are allowed. Android 13 and later ask; before that, always.
  static Future<bool> canNotify() async {
    try {
      return await _ch.invokeMethod<bool>('canNotify') ?? false;
    } on PlatformException {
      return false;
    }
  }

  /// Ask for permission to notify. Returns whether it is already granted; the dialog's
  /// answer arrives the next time [canNotify] is asked.
  static Future<bool> askNotify() async {
    try {
      return await _ch.invokeMethod<bool>('askNotify') ?? false;
    } on PlatformException {
      return false;
    }
  }

  /// One notification for one reply.
  static Future<void> notify(String title, String body) async {
    try {
      await _ch.invokeMethod('notify', {'title': title, 'body': body});
    } on PlatformException {
      // a notification that cannot be posted is not a reason to lose the reply
    }
  }

  /// Play a voice note. Returns how long it lasts, in milliseconds.
  ///
  /// One at a time: starting a second note stops the first, which is what a single
  /// pair of ears expects and what every messaging app already does.
  static Future<int> playStart(String name, Uint8List bytes) async {
    try {
      return await _ch.invokeMethod<int>(
              'playStart', {'name': name, 'bytes': bytes}) ??
          0;
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? 'No pude reproducir la nota.');
    }
  }

  /// How long a note lasts, without playing it. 0 when it cannot be read.
  static Future<int> probeDuration(String name, Uint8List bytes) async {
    try {
      return await _ch.invokeMethod<int>(
              'probeDuration', {'name': name, 'bytes': bytes}) ??
          0;
    } on PlatformException {
      return 0;
    }
  }

  static Future<void> playStop() async {
    try {
      await _ch.invokeMethod('playStop');
    } on PlatformException {
      // stopping something that is not playing is not worth saying anything about
    }
  }

  /// Where the note is up to, in milliseconds, or -1 when nothing is playing.
  static Future<int> playPosition() async {
    try {
      return await _ch.invokeMethod<int>('playPosition') ?? -1;
    } on PlatformException {
      return -1;
    }
  }

  static Future<void> recordStart() async {
    try {
      await _ch.invokeMethod('recordStart');
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? tr('No pude grabar.'));
    }
  }

  /// The finished note, or null if it was too short to be one.
  static Future<PickedFile?> recordStop() async {
    try {
      final r = await _ch.invokeMethod<Map<dynamic, dynamic>>('recordStop');
      if (r == null) return null;
      return PickedFile(
        (r['name'] ?? 'nota.m4a').toString(),
        r['bytes'] as Uint8List? ?? Uint8List(0),
      );
    } on PlatformException catch (e) {
      if (e.code == 'short') return null;
      throw NativeError(e.code, e.message ?? 'No pude guardar la nota.');
    }
  }

  static Future<void> recordCancel() async {
    try {
      await _ch.invokeMethod('recordCancel');
    } on PlatformException {
      // Cancelling is best effort: there is nothing useful to say if it fails.
    }
  }
}
