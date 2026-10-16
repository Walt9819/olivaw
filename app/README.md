# Olivaw en el teléfono

A Flutter app that talks to the Olivaw running on your computer. Android first; the
code is plain Dart and nothing in it is Android-specific, so iOS is a build target
away (but see *Distribution* below — that is where the two platforms stop being the
same).

> **Status: runs on a real phone.** Installed on a Galaxy Z Fold 5 (Android 16) and
> driven end to end: it finds the computer on its own, pairs, lists the agents, holds a
> conversation, records a voice note, attaches a file, reopens a past conversation and
> drives the agent's Chrome — and then did all of it again **on mobile data alone**,
> tether unplugged, with the computer not answering a ping. `flutter analyze` is silent,
> `flutter test` passes 28, and `tools/test_phone.py` is at 150 checks.
>
> Still unexercised: a *successful* voice transcription from the phone's own microphone
> (the room was quiet, so Whisper correctly found no words; the same route returns
> correct Spanish in 13s when given real speech), and iOS, which needs a Mac.

## What it is

| | |
|---|---|
| **Roster** | every agent with its face, in the colour its Chrome window wears |
| **Hablar** | the same conversation the desktop console has, from the phone |
| **Rutinas** | what each agent does on its own, and the switch that pauses it |
| **Navegador** | the agent's Chrome, live — watch it, and take the wheel |

What it deliberately does *not* have is the half of the console that decides what Olivaw
**is**: the brain, the vault, the channels, deleting an agent. Those stay on the desktop.
The server refuses them regardless (`wizard/pairing.py`), which is the right order — the
app not showing a button is a courtesy, the server not answering is the rule.

## Dependencies

One: `shared_preferences`, from the Flutter team's own package repo, to keep the pairing
token across restarts. Everything else is written here against `dart:io` and
`dart:typed_data` —

- `link.dart` — TLS with a **pinned certificate**, HTTP/1.1, and the WebSocket client half
- `relay.dart` — the tunnel for when the phone is not on the computer's wifi
- `sha256.dart` — because Dart's core libraries have no hash, and this one is checked
  against the published FIPS test vectors on every start

## How pairing works, and why there is no QR

On the computer: **Tu teléfono → Enlazar un teléfono**. It shows an address, a six-digit
code, and **two words**.

In the app: type the address (or pick it from the list — the app finds computers by
asking every address on the subnet whether it is Olivaw, which needs no discovery
protocol and no package), type the code, and then confirm that the two words the app
shows are the two words on the computer's screen.

Those words are computed on each side from the certificate that side is actually holding.
Somebody sitting in the middle of the wifi would have to present a different certificate,
and a different certificate makes different words. That is the whole check, and it is why
there is no QR scanner: scanning one would need a camera package, and a QR verifies no
more than two words a person compares.

From then on the app accepts **that certificate and no other** — stricter than the public
CA system, which vouches for a name and lets in anyone who fools a CA.

## Away from the wifi

`Link.reach()` tries the local address first and falls back to the rendezvous. Same TLS,
same pinned certificate, same token, same permissions: the rendezvous forwards bytes it
has no key for.

There is one running already, so this works out of the box: pairing a phone turns the
rendezvous on and the computer attaches to the shipped address. Change it or turn it
off on the computer under **Tu teléfono → Fuera de casa**; `relay/README.md` and
`relay/OPTIONS.md` cover running your own.

The one thing it cannot do is *start* away from home: pairing is where the phone learns
the certificate to pin and where to meet, so it has to see the computer on the same wifi
once. A phone paired before any of this existed learns the address from `net/home` the
next time it is on that wifi.

## Building

```bash
cd app
flutter create . --platforms=android      # generates android/, ios/ scaffolding
flutter pub get
flutter run                               # a phone on the same wifi as the computer
flutter build apk --release
```

`flutter create .` on an existing directory fills in the platform folders and leaves
`lib/` and `pubspec.yaml` alone.

### Android needs two things in the manifest

```xml
<uses-permission android:name="android.permission.INTERNET"/>
```

and, because the computer's address is a plain `https://192.168.x.x` with a self-signed
certificate, **nothing else** — the pinning is done in Dart with
`SecureSocket.connect(onBadCertificate:)`, which does not go through Android's network
security config. Do *not* add `usesCleartextTraffic`: nothing here is ever cleartext.

## Distribution

The APK goes on GitHub Releases as `Olivaw.apk`, next to the Olivaw zip that is already
published there — no Play Store account, no third party, no cost. Android asks the person
to allow installing from that source once.

It is signed with a key kept outside this repository (see *The signing key*), which is
what lets one version install over the last. Lose that key and there is no recovery
worth the name: a replacement key means every phone has to uninstall first, and
uninstalling takes the pairing and the stored conversations with it.

iOS cannot be distributed this way: without the App Store an app has to be re-signed
every seven days. That is a real limit, not an oversight, and it is why Android is first.

## Building it

The development machine is set up already (Flutter 3.47.7 at `C:\src\flutter`, Android
SDK 36, OpenJDK 17, all on `PATH`). From `app/`:

```
flutter analyze          # must be silent
flutter test             # 7 tests, including the two words agreeing with Python
flutter build apk --release
```

The APK lands in `build/app/outputs/flutter-apk/app-release.apk`. To put it on a phone:
enable *Developer options → USB debugging*, plug it in, and `flutter install --release`
— or copy the APK across and open it, which is the route a person who is not a developer
would take.

### On another machine

Flutter SDK, Android SDK 36 with `build-tools;36.0.0`, and a JDK 17. Then `flutter
create . --platforms=android --project-name olivaw --org com.olivaw` ONLY if `android/`
is missing — it is tracked in this repository precisely because regenerating it undoes
the three fixes above.

### The signing key

`android/app/build.gradle.kts` looks for `key.properties` in `$OLIVAW_SIGNING`, then
`%LOCALAPPDATA%\Olivaw\signing`, then `~/.olivaw/signing`, and falls back to the debug
key with a line in the build output if it finds none. The key itself is **not** in this
repository and must never be: whoever holds it can publish an update that Android will
install over Olivaw without asking the person running it. Back up that folder somewhere
safe — if it is lost, every installed copy has to be uninstalled by hand before a new
one will go on.

## What it looks like

The palette is the console's, token for token — `app/lib/theme.dart` carries the same
`--accent` (#5B5BD6 light, #8B7DFF dark) that marks an Olivaw window on the computer, so
an agent's face is the same colour in both places. Dark mode follows the phone.

The launcher icon is a white O on that accent, drawn by `tools/make_app_icon.py` with no
image library — see the comment at the top of that file for why the O is a ring rather
than a letter in a typeface.

## The conversation

| | |
|---|---|
| **Markdown** | headings, bold, code, fences, quotes, lists, tables, links — `app/lib/markdown.dart`, written here because `flutter_markdown` is discontinued |
| **Files** | the system picker, uploaded while you are still typing |
| **Voice notes** | recorded on the phone, transcribed on the computer's GPU, dropped into the box so you can fix a misheard name before sending |
| **Swipe to reply** | Telegram's gesture; the agent is told with a quote |
| **Past conversations** | the history button, including threads that happened on Telegram |

Files, recording and opening a link go through one hand-written `MethodChannel`
(`MainActivity.kt`) rather than three packages — which is how the app still has exactly
one dependency.

Voice notes need a transcriber on the computer: the `faster-whisper` virtualenv that
Hermes already uses for Telegram voice notes. Without it the route says so in plain words
rather than failing. The first note on a new machine takes about five minutes while 2 GB
of model downloads; after that it is around thirteen seconds.
