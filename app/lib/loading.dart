// Waiting, said properly.
//
// A spinner answers "is it working?" and nothing else. The waits in this app are not
// all the same: finding the computer can take a few seconds on mobile data, asking it
// for seven agents means seven CLI calls on the other side, and opening a file means
// fetching it whole. A single spinner for all three means the longest one looks broken,
// and the usual answer to that - disable the button and show nothing - is worse: a
// screen that has gone still is indistinguishable from a screen that has died.
//
// So this does two things a spinner does not. It SAYS what is happening, and after a
// few seconds it says that it is taking longer than usual, which is the sentence that
// stops somebody force-quitting the app. The line changes on its own, so the screen is
// visibly alive even when nothing has arrived yet.

import 'dart:async';

import 'package:flutter/material.dart';

import 'strings.dart';
import 'theme.dart';

/// A wait with something to read. [lines] are shown in turn, a few seconds apart; the
/// last one stays. Give the most reassuring one last, because that is the one somebody
/// who is still waiting will be looking at.
class Waiting extends StatefulWidget {
  const Waiting({
    super.key,
    this.lines = const [],
    this.compact = false,
    this.every = const Duration(seconds: 4),
  });

  final List<String> lines;
  final bool compact;
  final Duration every;

  @override
  State<Waiting> createState() => _WaitingState();
}

class _WaitingState extends State<Waiting> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  )..repeat();

  Timer? _tick;
  int _line = 0;

  @override
  void initState() {
    super.initState();
    if (widget.lines.length > 1) {
      _tick = Timer.periodic(widget.every, (t) {
        if (!mounted) return;
        // Stops at the last one rather than looping: a message that cycles forever
        // reads as a screen that is stuck repeating itself.
        if (_line >= widget.lines.length - 1) {
          t.cancel();
          return;
        }
        setState(() => _line++);
      });
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dots = _Pulse(controller: _c, color: context.c.accent);
    if (widget.compact) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        // 44, not 34: each dot swells to 10 at the top of its cycle and there are
        // two 6-pixel gaps, so the row needs 42 at its widest. Sized for the resting
        // state, it painted overflow stripes on every beat.
        SizedBox(width: 44, height: 12, child: dots),
        if (widget.lines.isNotEmpty) ...[
          const SizedBox(width: 10),
          Flexible(
            // Ellipsis rather than overflow: dropped into a row that is narrower
            // than the sentence, this should get shorter, not paint stripes over
            // whatever is beside it. (In a row with no width at all it can still
            // run off the end - no widget can save itself from that - so callers
            // put it somewhere bounded.)
            child: Text(tr(widget.lines[_line.clamp(0, widget.lines.length - 1)]),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, color: context.c.muted)),
          ),
        ],
      ]);
    }
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(width: 54, height: 16, child: dots),
            if (widget.lines.isNotEmpty) ...[
              const SizedBox(height: 18),
              // Keyed on the line so the text cross-fades instead of snapping, which is
              // the difference between "it changed" and "something is happening".
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 350),
                child: Text(
                  tr(widget.lines[_line.clamp(0, widget.lines.length - 1)]),
                  key: ValueKey(_line),
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 13, color: context.c.muted),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Three dots that rise and fall in turn. Chosen over a spinning ring because a ring
/// spins at the same rate whether or not anything is happening, and these read as a
/// heartbeat - which is what the screen is actually claiming.
class _Pulse extends StatelessWidget {
  const _Pulse({required this.controller, required this.color});

  final AnimationController controller;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return Row(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            for (var i = 0; i < 3; i++) ...[
              if (i > 0) const SizedBox(width: 6),
              _dot(i),
            ],
          ],
        );
      },
    );
  }

  Widget _dot(int i) {
    // Each dot a third of a cycle behind the one before it.
    final t = (controller.value + i / 3) % 1.0;
    // A smooth rise and fall rather than a sawtooth: sin over the cycle, shifted into
    // 0..1 so the dot is never fully invisible, which would read as a gap.
    final wave = 0.45 + 0.55 * (1 - (2 * t - 1).abs());
    return Opacity(
      opacity: wave,
      child: Container(
        width: 8 + 2 * wave,
        height: 8 + 2 * wave,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      ),
    );
  }
}

/// The small spinner that goes next to something already on screen - a file being
/// fetched, a switch being thrown. [label] is optional and usually a size.
class Busy extends StatelessWidget {
  const Busy({super.key, this.label = '', this.size = 14});

  final String label;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Row(mainAxisSize: MainAxisSize.min, children: [
      SizedBox(
        width: size,
        height: size,
        child: CircularProgressIndicator(strokeWidth: 2, color: context.c.accent),
      ),
      if (label.isNotEmpty) ...[
        const SizedBox(width: 8),
        Text(label, style: TextStyle(fontSize: 11, color: context.c.muted)),
      ],
    ]);
  }
}

/// "1,4 MB". Shown beside anything being fetched, because a wait with a number on it is
/// a wait you can judge: 40 kB that is slow is a bad connection, 40 MB that is slow is
/// simply 40 MB.
String humanSize(int bytes) {
  if (bytes <= 0) return '';
  const unit = ['B', 'kB', 'MB', 'GB'];
  var v = bytes.toDouble();
  var i = 0;
  while (v >= 1024 && i < unit.length - 1) {
    v /= 1024;
    i++;
  }
  final s = v >= 100 || i == 0 ? v.toStringAsFixed(0) : v.toStringAsFixed(1);
  return '${s.replaceAll('.', ',')} ${unit[i]}';
}
