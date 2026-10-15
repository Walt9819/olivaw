// The Dart end of the three things this app asks Android for.
//
// See MainActivity.kt for why these are a hand-written channel rather than three
// packages. Every call here can fail for a reason the owner caused - they cancelled the
// picker, they declined the microphone - and none of those is an error worth a red
// banner, so the shapes below separate "nothing happened" from "something broke".

import 'package:flutter/services.dart';

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
      throw NativeError(e.code, e.message ?? 'No pude abrir ese enlace.');
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
      throw NativeError(e.code, e.message ?? 'No pude abrir el selector.');
    }
  }

  static Future<void> recordStart() async {
    try {
      await _ch.invokeMethod('recordStart');
    } on PlatformException catch (e) {
      throw NativeError(e.code, e.message ?? 'No pude grabar.');
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
