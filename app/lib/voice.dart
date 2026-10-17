// A voice note in the conversation.
//
// What you sent was a recording, so a recording is what the conversation shows - press
// play and hear it back, the way Telegram and WhatsApp do. The transcript is what the
// AGENT reads, and it is shown underneath, folded away: it is worth being able to check
// what the computer heard, and not worth reading every time. A misheard name is obvious
// the moment you look, and invisible if the words are never shown at all.
//
// Playback is a MediaPlayer on the Android side, polled for its position - no package,
// for the same reason as the rest of the native channel.

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'attachment.dart';
import 'link.dart';
import 'native.dart';
import 'strings.dart';

class VoiceNote extends StatefulWidget {
  const VoiceNote({
    super.key,
    required this.link,
    required this.file,
    required this.body,
    required this.ink,
    required this.accent,
    this.transcript = '',
    this.localBytes,
    this.pending = false,
  });

  final Link link;

  /// The file record from the computer: name, size, and an id to fetch it by. Absent
  /// while a note is still on its way up, when [localBytes] carries it instead.
  final Map<String, dynamic> file;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final Color ink;
  final Color accent;

  /// What the computer heard. Empty while it is still listening.
  final String transcript;

  /// The recording as it came off the microphone, before it has been anywhere.
  final Uint8List? localBytes;

  /// Still uploading or being transcribed.
  final bool pending;

  @override
  State<VoiceNote> createState() => _VoiceNoteState();
}

class _VoiceNoteState extends State<VoiceNote> {
  bool _open = false;        // is the transcript unfolded
  bool _playing = false;
  int _durationMs = 0;
  int _positionMs = 0;
  Timer? _poll;
  Uint8List? _bytes;
  bool _fetching = false;

  @override
  void initState() {
    super.initState();
    _measure();
  }

  @override
  void didUpdateWidget(VoiceNote old) {
    super.didUpdateWidget(old);
    // The recording arrives before the file record does; measure once it is here.
    if (_durationMs == 0) _measure();
  }

  /// So the bubble does not sit at 0:00 until somebody presses play.
  Future<void> _measure() async {
    if (_durationMs > 0) return;
    final local = widget.localBytes;
    if (local == null || local.isEmpty) return;   // a fetched note measures on play
    final ms = await Native.probeDuration(
        (widget.file['name'] ?? 'nota.m4a').toString(), local);
    if (mounted && ms > 0) setState(() => _durationMs = ms);
  }

  @override
  void dispose() {
    _poll?.cancel();
    if (_playing) Native.playStop();
    super.dispose();
  }

  Future<Uint8List?> _audio() async {
    if (widget.localBytes != null) return widget.localBytes;
    if (_bytes != null) return _bytes;
    if (_fetching) return null;
    _fetching = true;
    try {
      final att = Attachment(
        link: widget.link,
        file: widget.file,
        body: widget.body,
        ink: widget.ink,
      );
      final b = await att.fetch();
      if (b != null) _bytes = b;
      return b;
    } finally {
      _fetching = false;
    }
  }

  Future<void> _toggle() async {
    if (_playing) {
      _poll?.cancel();
      await Native.playStop();
      if (mounted) setState(() { _playing = false; _positionMs = 0; });
      return;
    }
    final messenger = ScaffoldMessenger.of(context);
    try {
      final bytes = await _audio();
      if (bytes == null || bytes.isEmpty) return;
      final ms = await Native.playStart(
          (widget.file['name'] ?? 'nota.m4a').toString(), bytes);
      if (!mounted) return;
      setState(() {
        _playing = true;
        _durationMs = ms;
        _positionMs = 0;
      });
      // Polled rather than streamed: a progress bar is the only thing watching, and
      // four times a second is smoother than it needs to be.
      _poll = Timer.periodic(const Duration(milliseconds: 250), (t) async {
        final at = await Native.playPosition();
        if (!mounted) return;
        if (at < 0) {
          t.cancel();
          setState(() { _playing = false; _positionMs = 0; });
          return;
        }
        setState(() => _positionMs = at);
      });
    } on NativeError catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(humanError(e))));
    }
  }

  String get _clock {
    final ms = _playing && _durationMs > 0 ? _durationMs - _positionMs : _durationMs;
    final secs = (ms / 1000).round().clamp(0, 59 * 60);
    return '${secs ~/ 60}:${(secs % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final progress =
        (_durationMs > 0 && _playing) ? (_positionMs / _durationMs).clamp(0.0, 1.0) : 0.0;
    final hasText = widget.transcript.trim().isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(mainAxisSize: MainAxisSize.min, children: [
          // Playable from the moment it exists.
          //
          // A spinner used to sit here while the computer listened to it, so the one
          // thing you could not do with a note you had just recorded was hear it -
          // and that is the moment you most want to, because that is when you find
          // out whether you said what you meant. The bytes never left the phone;
          // there was nothing to wait for. What is still being waited on is the
          // transcript, and that says so on its own line below.
          InkWell(
            onTap: _toggle,
            customBorder: const CircleBorder(),
            child: Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                  color: widget.ink.withValues(alpha: 0.15),
                  shape: BoxShape.circle),
              child: Icon(_playing ? Icons.stop : Icons.play_arrow,
                  size: 20, color: widget.ink),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 120,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: _playing ? progress : 0.0,
                minHeight: 4,
                backgroundColor: widget.ink.withValues(alpha: 0.2),
                valueColor: AlwaysStoppedAnimation(widget.ink),
              ),
            ),
          ),
          const SizedBox(width: 10),
          Text(
            // The length as soon as it is known, which for a note just recorded is
            // immediately. 'Enviando…' only while there is no length to show yet.
            (_durationMs <= 0 && widget.pending) ? tr('Enviando…') : _clock,
            style: TextStyle(
                fontSize: 12,
                color: widget.ink.withValues(alpha: 0.8),
                fontFeatures: const [FontFeature.tabularFigures()]),
          ),
        ]),
        if (hasText || widget.pending)
          InkWell(
            onTap: hasText ? () => setState(() => _open = !_open) : null,
            child: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                if (widget.pending && !hasText)
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: SizedBox(
                      width: 11,
                      height: 11,
                      child: CircularProgressIndicator(
                          strokeWidth: 1.6,
                          color: widget.ink.withValues(alpha: 0.7)),
                    ),
                  )
                else
                  Icon(_open ? Icons.expand_less : Icons.expand_more,
                      size: 16, color: widget.ink.withValues(alpha: 0.7)),
                const SizedBox(width: 2),
                Text(
                  widget.pending && !hasText
                      ? tr('Transcribiendo…')
                      : _open
                          ? tr('Ocultar transcripción')
                          : tr('Ver transcripción'),
                  style: TextStyle(
                      fontSize: 11, color: widget.ink.withValues(alpha: 0.7)),
                ),
              ]),
            ),
          ),
        if (_open && hasText)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: widget.ink.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(widget.transcript,
                  style: TextStyle(fontSize: 13, color: widget.ink, height: 1.3)),
            ),
          ),
      ],
    );
  }
}
