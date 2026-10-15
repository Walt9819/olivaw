// The colours Olivaw already wears, carried onto the phone.
//
// These are not new. Every value here is copied from the desktop console's
// `src/wizard/web/app.css`, token for token, including its dark set. The point is that
// the two should not feel like two products: the same purple that marks a window as
// Olivaw's on the computer marks the app on the phone, and an agent's face is the same
// colour in both places because both get it from the same palette on the computer.
//
// Where Flutter wants a name the CSS does not have, the name here says what it is FOR
// rather than what it looks like, so a future change of palette has one place to happen.

import 'package:flutter/material.dart';

/// One set of colours. Two instances: [Palette.light] and [Palette.dark].
@immutable
class Palette extends ThemeExtension<Palette> {
  const Palette({
    required this.bg,
    required this.panel,
    required this.panel2,
    required this.ink,
    required this.muted,
    required this.line,
    required this.line2,
    required this.accent,
    required this.accent2,
    required this.accentInk,
    required this.ok,
    required this.okBg,
    required this.err,
    required this.errBg,
    required this.warn,
    required this.warnBg,
  });

  final Color bg, panel, panel2, ink, muted, line, line2;
  final Color accent, accent2, accentInk;
  final Color ok, okBg, err, errBg, warn, warnBg;

  /// app.css `:root`
  static const light = Palette(
    bg: Color(0xFFF4F5FB),
    panel: Color(0xFFFFFFFF),
    panel2: Color(0xFFF8F9FF),
    ink: Color(0xFF1A1C2E),
    muted: Color(0xFF666A85),
    line: Color(0xFFE6E8F4),
    line2: Color(0xFFEEF0F9),
    accent: Color(0xFF5B5BD6),
    accent2: Color(0xFF8A63E8),
    accentInk: Color(0xFFFFFFFF),
    ok: Color(0xFF1F9D63),
    okBg: Color(0xFFE7F7EE),
    err: Color(0xFFD64550),
    errBg: Color(0xFFFDECED),
    warn: Color(0xFFB3791A),
    warnBg: Color(0xFFFDF3E2),
  );

  /// app.css `@media (prefers-color-scheme: dark)`
  static const dark = Palette(
    bg: Color(0xFF0F1120),
    panel: Color(0xFF181A2C),
    panel2: Color(0xFF1E2137),
    ink: Color(0xFFEEF0FF),
    muted: Color(0xFF9AA0C4),
    line: Color(0xFF2A2D47),
    line2: Color(0xFF23263C),
    accent: Color(0xFF8B7DFF),
    accent2: Color(0xFFC07DF0),
    accentInk: Color(0xFFFFFFFF),
    ok: Color(0xFF4FD08A),
    okBg: Color(0xFF123227),
    err: Color(0xFFFF7A84),
    errBg: Color(0xFF331A1E),
    warn: Color(0xFFE7B45A),
    warnBg: Color(0xFF332913),
  );

  @override
  Palette copyWith({
    Color? bg, Color? panel, Color? panel2, Color? ink, Color? muted,
    Color? line, Color? line2, Color? accent, Color? accent2, Color? accentInk,
    Color? ok, Color? okBg, Color? err, Color? errBg, Color? warn, Color? warnBg,
  }) {
    return Palette(
      bg: bg ?? this.bg,
      panel: panel ?? this.panel,
      panel2: panel2 ?? this.panel2,
      ink: ink ?? this.ink,
      muted: muted ?? this.muted,
      line: line ?? this.line,
      line2: line2 ?? this.line2,
      accent: accent ?? this.accent,
      accent2: accent2 ?? this.accent2,
      accentInk: accentInk ?? this.accentInk,
      ok: ok ?? this.ok,
      okBg: okBg ?? this.okBg,
      err: err ?? this.err,
      errBg: errBg ?? this.errBg,
      warn: warn ?? this.warn,
      warnBg: warnBg ?? this.warnBg,
    );
  }

  @override
  Palette lerp(ThemeExtension<Palette>? other, double t) {
    if (other is! Palette) return this;
    Color c(Color a, Color b) => Color.lerp(a, b, t)!;
    return Palette(
      bg: c(bg, other.bg),
      panel: c(panel, other.panel),
      panel2: c(panel2, other.panel2),
      ink: c(ink, other.ink),
      muted: c(muted, other.muted),
      line: c(line, other.line),
      line2: c(line2, other.line2),
      accent: c(accent, other.accent),
      accent2: c(accent2, other.accent2),
      accentInk: c(accentInk, other.accentInk),
      ok: c(ok, other.ok),
      okBg: c(okBg, other.okBg),
      err: c(err, other.err),
      errBg: c(errBg, other.errBg),
      warn: c(warn, other.warn),
      warnBg: c(warnBg, other.warnBg),
    );
  }
}

/// `Palette.of(context)` - the colours for whichever mode the phone is in.
extension PaletteOf on BuildContext {
  Palette get c => Theme.of(this).extension<Palette>() ?? Palette.light;
}

/// Black or white, whichever can actually be read on [background].
///
/// Needed because a few surfaces here are NOT theme colours and do not flip with the
/// mode: an agent's own colour, and the warn amber, which is a dark ochre in the light
/// palette and a light amber in the dark one. White reads on the first and disappears
/// on the second, so the choice has to come from the colour itself.
///
/// The threshold is 0.45 rather than 0.5: white text on a mid-tone carries better than
/// black does, so the tie is broken towards white.
Color inkOn(Color background) =>
    background.computeLuminance() > 0.45 ? const Color(0xFF14151A) : Colors.white;

ThemeData _theme(Palette p, Brightness brightness) {
  final scheme = ColorScheme.fromSeed(
    seedColor: p.accent,
    brightness: brightness,
  ).copyWith(
    primary: p.accent,
    onPrimary: p.accentInk,
    secondary: p.accent2,
    surface: p.panel,
    onSurface: p.ink,
    error: p.err,
  );
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    scaffoldBackgroundColor: p.bg,
    fontFamily: 'Roboto',
    extensions: [p],
    appBarTheme: AppBarTheme(
      backgroundColor: p.bg,
      foregroundColor: p.ink,
      elevation: 0,
      scrolledUnderElevation: 0,
      surfaceTintColor: Colors.transparent,
    ),
    dividerTheme: DividerThemeData(color: p.line, thickness: 1, space: 1),
    listTileTheme: ListTileThemeData(textColor: p.ink, iconColor: p.muted),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: p.panel,
      surfaceTintColor: Colors.transparent,
      indicatorColor: p.accent.withValues(alpha: 0.16),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (s) => TextStyle(
          fontSize: 12,
          fontWeight: s.contains(WidgetState.selected) ? FontWeight.w700 : FontWeight.w500,
          color: s.contains(WidgetState.selected) ? p.accent : p.muted,
        ),
      ),
      iconTheme: WidgetStateProperty.resolveWith(
        (s) => IconThemeData(color: s.contains(WidgetState.selected) ? p.accent : p.muted),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: p.panel,
      hintStyle: TextStyle(color: p.muted),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: p.line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: p.line),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: p.accent, width: 2),
      ),
      disabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: p.line2),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: p.accent,
        foregroundColor: p.accentInk,
        minimumSize: const Size(0, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: p.accent),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: p.ink,
      contentTextStyle: TextStyle(color: p.bg),
      behavior: SnackBarBehavior.floating,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: p.panel,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
    ),
  );
}

ThemeData get olivawLight => _theme(Palette.light, Brightness.light);
ThemeData get olivawDark => _theme(Palette.dark, Brightness.dark);
