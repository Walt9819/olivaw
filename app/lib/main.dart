// Olivaw on a phone.
//
// The shape of this app follows the console on the desktop, because they are the same
// thing seen from two places: a roster of agents with faces, each one opening onto a
// conversation and its settings. What the phone deliberately does NOT get is the half of
// the console that decides what Olivaw is - the brain, the vault, the channels, deleting
// an agent. Those live on the computer. The server enforces that; this app only avoids
// showing buttons the server would refuse, which is the right order for those two to be
// in.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'attachment.dart';
import 'conversations.dart';
import 'link.dart';
import 'loading.dart';
import 'sha256.dart';
import 'markdown.dart';
import 'media.dart';
import 'native.dart';
import 'theme.dart';
import 'voice.dart';
import 'settings.dart';
import 'strings.dart';
import 'teams.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // A hash that is quietly wrong would make every computer look like an impostor.
  assert(sha256SelfTest(), 'SHA-256 self test failed');
  // Before the first frame, so nothing is drawn in the wrong language and then redrawn.
  chooseLanguage();
  await loadOpenThreads();
  runApp(const OlivawApp());
}



class OlivawApp extends StatelessWidget {
  const OlivawApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Olivaw',
      debugShowCheckedModeBanner: false,
      theme: olivawLight,
      darkTheme: olivawDark,
      // Whatever the phone is set to. A tool you reach for at midnight should not be
      // the one white rectangle on the screen.
      themeMode: ThemeMode.system,
      home: const Boot(),
    );
  }
}

// ── where the pairing is kept ─────────────────────────────────────────────────────────

class Store {
  static const _key = 'olivaw.pairings.v1';

  static Future<List<Pairing>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null || raw.isEmpty) return [];
    try {
      return (jsonDecode(raw) as List)
          .map((j) => Pairing.fromJson(j as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> save(List<Pairing> all) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(all.map((p) => p.toJson()).toList()));
  }

  static const _threadsKey = 'olivaw.threads.v1';

  /// Which conversation is open with each agent, kept across launches.
  ///
  /// Only the id. The messages themselves stay on the computer, where they already are
  /// and where they belong - a phone that is lost should not be carrying a copy of
  /// every conversation. Reopening fetches them again.
  static Future<Map<String, String>> loadThreads() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_threadsKey);
    if (raw == null || raw.isEmpty) return {};
    try {
      return (jsonDecode(raw) as Map).map((k, v) => MapEntry('$k', '$v'));
    } catch (_) {
      return {};
    }
  }

  static Future<void> saveThreads(Map<String, String> threads) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_threadsKey, jsonEncode(threads));
  }

  static const _rosterKey = 'olivaw.roster.v1';

  /// The last roster, so the first screen has something to draw immediately.
  ///
  /// Asking the computer for seven agents means seven CLI calls on the other side,
  /// and away from home a round trip on top. Doing that from a blank screen EVERY
  /// time you came back from a conversation is the thing this avoids: the names and
  /// faces barely change, so showing the last ones and quietly correcting them is
  /// both faster and more honest than a spinner that hides what you already know.
  ///
  /// Names, colours and faces only - nothing anybody said. A phone that is lost
  /// should not be carrying a copy of the conversations, and it does not.
  static Future<List<Map<String, dynamic>>> loadRoster() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_rosterKey);
    if (raw == null || raw.isEmpty) return [];
    try {
      return (jsonDecode(raw) as List)
          .map((j) => (j as Map).cast<String, dynamic>())
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> saveRoster(List<Map<String, dynamic>> rows) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_rosterKey, jsonEncode(rows));
  }
}

class Boot extends StatefulWidget {
  const Boot({super.key});
  @override
  State<Boot> createState() => _BootState();
}

class _BootState extends State<Boot> {
  List<Pairing>? _saved;

  @override
  void initState() {
    super.initState();
    Store.load().then((v) => setState(() => _saved = v));
  }

  /// Not a closure inside build(): there, the `context` parameter shadows the State's
  /// own, so a BuildContext would be held across the save and `mounted` would be
  /// guarding something else.
  Future<void> _paired(Pairing p) async {
    await Store.save([p]);
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => Home(pairing: p)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final saved = _saved;
    if (saved == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (saved.isEmpty) {
      return LinkScreen(onPaired: _paired);
    }
    return Home(pairing: saved.first);
  }
}

// ── linking a computer ────────────────────────────────────────────────────────────────

class LinkScreen extends StatefulWidget {
  const LinkScreen({super.key, required this.onPaired});
  final Future<void> Function(Pairing) onPaired;

  @override
  State<LinkScreen> createState() => _LinkScreenState();
}

class _LinkScreenState extends State<LinkScreen> {
  final _host = TextEditingController();
  final _pin = TextEditingController();
  String _status = '';
  bool _busy = false;
  List<String> _found = [];
  bool _scanning = false;

  @override
  void initState() {
    super.initState();
    _scan();
  }

  /// Look for computers on this wifi by asking every address in the subnet whether it is
  /// Olivaw. No discovery protocol and no dependency: 254 cheap connections in parallel,
  /// and /ping is deliberately the one route that answers without a token.
  Future<void> _scan() async {
    setState(() {
      _scanning = true;
      _found = [];
    });
    String? base;
    for (final ni in await NetworkInterface.list(type: InternetAddressType.IPv4)) {
      for (final a in ni.addresses) {
        if (!a.isLoopback && a.address.startsWith('192.168.') ||
            a.address.startsWith('10.') ||
            a.address.startsWith('172.')) {
          base = a.address.substring(0, a.address.lastIndexOf('.'));
          break;
        }
      }
      if (base != null) break;
    }
    if (base == null) {
      setState(() => _scanning = false);
      return;
    }
    final hits = <String>[];
    await Future.wait([
      for (var i = 1; i < 255; i++)
        () async {
          final ip = '$base.$i';
          try {
            final s = await Socket.connect(ip, 8791,
                timeout: const Duration(milliseconds: 700));
            s.destroy();
            hits.add(ip);
          } catch (_) {}
        }(),
    ]);
    if (!mounted) return;
    setState(() {
      _found = hits..sort();
      _scanning = false;
      if (hits.length == 1 && _host.text.isEmpty) _host.text = hits.first;
    });
  }

  Future<void> _pair() async {
    final host = _host.text.trim();
    final pin = _pin.text.trim();
    if (host.isEmpty || pin.length != 6) {
      setState(() => _status = 'Escribe la dirección y el código de seis cifras.');
      return;
    }
    setState(() {
      _busy = true;
      _status = 'Conectando…';
    });
    var hostOnly = host;
    var port = 8791;
    if (host.contains(':')) {
      hostOnly = host.split(':').first;
      port = int.tryParse(host.split(':').last) ?? 8791;
    }
    final draft = Pairing(
      name: hostOnly,
      host: hostOnly,
      port: port,
      certDer: Uint8List(0),
      token: '',
    );
    final link = Link(draft);
    try {
      // First connection: there is nothing pinned yet, so the certificate is accepted
      // and then shown to the person as two words. They compare those with the two words
      // on the computer's screen. Somebody sitting in the middle of this wifi would have
      // to present a different certificate - and a different certificate makes different
      // words.
      await link.call('GET', '/ping', auth: false, expectDer: null);
      final cert = link.lastCertificate;
      if (cert == null) throw LinkError('No pude ver el certificado de la computadora.');
      final der = Uint8List.fromList(cert.der);
      final words = shortCode(hex(sha256(der)));
      if (!mounted) return;
      final agreed = await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
              title: Text(tr('¿Dice esto tu computadora?')),
              content: Column(mainAxisSize: MainAxisSize.min, children: [
                const Text('En la pantalla de la computadora, debajo del código, hay dos palabras. Tienen que ser exactamente estas:'),
                const SizedBox(height: 14),
                SelectableText(words,
                    style: const TextStyle(
                        fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: 1)),
                const SizedBox(height: 14),
                Text(tr('Si no coinciden, no continúes: no estarías hablando con tu computadora.'), style: const TextStyle(fontSize: 12)),
              ]),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(c, false),
                    child: Text(tr('No coinciden'))),
                FilledButton(
                    onPressed: () => Navigator.pop(c, true),
                    child: Text(tr('Son estas'))),
              ],
            ),
          ) ??
          false;
      if (!agreed) {
        setState(() {
          _busy = false;
          _status = 'Enlace cancelado.';
        });
        return;
      }
      draft.certDer = der;
      final res = await link.call('POST', '/pair',
          auth: false,
          expectDer: der,
          body: {'pin': pin, 'name': 'Mi teléfono', 'platform': 'android'});
      if (res['ok'] != true) {
        setState(() {
          _busy = false;
          _status = tr((res['detail'] ?? 'No se pudo enlazar.').toString());
        });
        return;
      }
      draft.token = res['token'] as String;
      draft.caps = (res['caps'] as List?)?.cast<String>() ?? const [];
      draft.name = 'Mi computadora';
      // How to find this computer when not on its wifi. Handed over now because later is
      // too late: the moment the phone needs it is the moment it cannot ask.
      final relay = res['relay'];
      if (relay is Map) {
        draft.relayUrl = (relay['url'] ?? '').toString();
        draft.relayRoom = (relay['room'] ?? '').toString();
      }
      await widget.onPaired(draft);
    } catch (e) {
      setState(() {
        _busy = false;
        _status = e is HandshakeException
            ? 'El computadora presentó otro certificado. No he seguido.'
            : trp('No pude conectar: {0}', [e]);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(tr('Enlazar tu computadora'))),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        const Text('En la computadora, abre Olivaw y ve a «Tu teléfono» → «Enlazar un teléfono». Te dará una dirección y un código.'),
        const SizedBox(height: 20),
        if (_scanning)
          Row(children: [
            const SizedBox(
                width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: 10),
            Text(tr('Buscando en tu wifi…')),
          ])
        else if (_found.isNotEmpty)
          Wrap(
            spacing: 8,
            children: _found
                .map((ip) => ActionChip(
                      label: Text(ip),
                      onPressed: () => setState(() => _host.text = ip),
                    ))
                .toList(),
          ),
        const SizedBox(height: 12),
        TextField(
          controller: _host,
          decoration: const InputDecoration(
            labelText: 'Dirección de la computadora',
            hintText: '192.168.1.40',
            border: OutlineInputBorder(),
          ),
          keyboardType: TextInputType.url,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _pin,
          decoration: InputDecoration(
            labelText: tr('Código de seis cifras'),
            border: const OutlineInputBorder(),
          ),
          keyboardType: TextInputType.number,
          maxLength: 6,
          style: const TextStyle(fontSize: 24, letterSpacing: 6),
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: _busy ? null : _pair,
          child: Text(_busy ? 'Enlazando…' : tr('Enlazar')),
        ),
        TextButton(onPressed: _scanning ? null : _scan, child: Text(tr('Buscar otra vez'))),
        if (_status.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(_status, style: TextStyle(color: context.c.err)),
          ),
      ]),
    );
  }
}

// ── the roster ────────────────────────────────────────────────────────────────────────

class Agent {
  Agent(this.slug, this.name, this.running, this.look, this.isDefault, this.team);
  final String slug;
  final String name;
  final bool? running;
  final Map<String, dynamic> look;
  final bool isDefault;

  /// {id, name, icon} for the team this agent belongs to, or null. It rides with the
  /// roster rather than being asked for per agent, for the same reason the face does.
  final Map<String, dynamic>? team;

  String get teamName => (team?['name'] ?? '').toString();
  String get teamIcon => (team?['icon'] ?? '').toString();

  static Agent from(Map<String, dynamic> row) => Agent(
        (row['slug'] ?? 'default').toString(),
        (row['name'] ?? '?').toString(),
        row['gateway_running'] as bool?,
        (row['look'] as Map?)?.cast<String, dynamic>() ?? const {},
        row['is_default'] == true,
        (row['team'] as Map?)?.cast<String, dynamic>(),
      );

  String get initials => (look['initials'] ?? '?').toString();
  Color get colour {
    final hexa = (look['hex'] ?? '#2F6F4E').toString().replaceAll('#', '');
    return Color(int.parse('FF$hexa', radix: 16));
  }

  String get emoji => (look['emoji'] ?? '').toString();
  String get state => running == true
      ? tr('encendido')
      : running == false
          ? tr('apagado')
          : tr('sin saber');
}

class Home extends StatefulWidget {
  const Home({super.key, required this.pairing});
  final Pairing pairing;

  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  late final Link _link = Link(widget.pairing);
  List<Agent> _agents = [];
  String _error = '';
  bool _loading = true;

  /// Agents, teams, usage. Three views rather than one list with everything on it:
  /// 'which agents are there', 'who works with whom' and 'who has been doing the
  /// work' are three questions, and answering them in one column meant answering
  /// only the first.
  int _tab = 0;

  @override
  void initState() {
    super.initState();
    _showLast();
    _refresh();
  }

  /// Whatever was on this screen last time, drawn before anybody is asked anything.
  Future<void> _showLast() async {
    final rows = await Store.loadRoster();
    if (!mounted || rows.isEmpty || _agents.isNotEmpty) return;
    setState(() {
      _agents = rows.map(Agent.from).toList();
      _loading = false;
    });
  }

  @override
  void dispose() {
    _link.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    // Only a BLANK screen waits visibly. Coming back from a conversation used to
    // empty the list and start again from 'Buscando tu computadora…', which made a
    // roster you were looking at a second ago feel like it had to be fetched from
    // nothing - and on mobile data that is several seconds of staring at a screen
    // that has the answer cached.
    if (_agents.isEmpty) setState(() => _loading = true);
    try {
      final res = await _link.reach('POST', '/api/agents/list');
      if (res['ok'] != true) throw LinkError(tr((res['detail'] ?? tr('Sin respuesta')).toString()));
      final out = <Agent>[];
      if (res['default'] != null) {
        out.add(Agent.from((res['default'] as Map).cast<String, dynamic>()));
      }
      for (final e in (res['extra'] as List? ?? [])) {
        out.add(Agent.from((e as Map).cast<String, dynamic>()));
      }
      setState(() {
        _agents = out;
        _error = '';
        _loading = false;
      });
      // Kept for the next time this screen opens. Names, colours and faces only.
      await Store.saveRoster([
        if (res['default'] != null) (res['default'] as Map).cast<String, dynamic>(),
        for (final e in (res['extra'] as List? ?? []))
          (e as Map).cast<String, dynamic>(),
      ]);
      // A rendezvous configured after this phone was paired, or a computer that moved to
      // another address on the wifi, both arrive here. _link.hostChanged covers the
      // third case: reach() had to go looking for the computer and found it elsewhere.
      final moved = _link.hostChanged;
      _link.hostChanged = false;
      if (await _link.refreshHome() || moved) {
        await Store.save([widget.pairing]);
      }
    } catch (e) {
      setState(() {
        // A refresh that failed behind a list that is already drawn does not wipe
        // it: the names are still the names, and saying so beats replacing seven
        // agents with an apology. Only an empty screen gets the error.
        _error = _agents.isEmpty ? humanError(e) : '';
        _loading = false;
      });
      if (_agents.isNotEmpty && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(humanError(e))),
        );
      }
    }
  }

  /// Open one agent's screen, from the roster or from a team.
  void _open(BuildContext c, Agent a) {
    Navigator.push<bool>(
      c,
      MaterialPageRoute(
        builder: (_) => AgentScreen(link: _link, agent: a, pairing: widget.pairing),
      ),
    ).then((changed) {
      if (changed == true && mounted) _refresh();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text([
          tr('Tus agentes'),
          tr('Equipos'),
          tr('Uso'),
        ][_tab.clamp(0, 2)]),
        actions: [
          if (_tab == 0)
            IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab.clamp(0, 2),
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [
          NavigationDestination(
              icon: const Icon(Icons.people_outline), label: tr('Agentes')),
          NavigationDestination(
              icon: const Icon(Icons.workspaces_outline), label: tr('Equipos')),
          NavigationDestination(
              icon: const Icon(Icons.insights_outlined), label: tr('Uso')),
        ],
      ),
      body: _tab == 1
          ? TeamsTab(
              link: _link,
              agents: _agents,
              onOpenAgent: (a) => _open(context, a),
            )
          : _tab == 2
              ? UsageTab(link: _link, agents: _agents)
              : RefreshIndicator(
        onRefresh: _refresh,
        child: _error.isNotEmpty
            ? ListView(children: [
                Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(children: [
                    Icon(Icons.wifi_off, size: 42, color: context.c.muted.withValues(alpha: 0.5)),
                    const SizedBox(height: 12),
                    Text(_error, textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    Text(
                      tr('Si estás fuera de casa, tu computadora necesita tener configurado el punto de encuentro en «Tu teléfono».'),
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 12, color: context.c.muted),
                    ),
                  ]),
                )
              ])
            : _loading
                ? const Waiting(lines: [
                    'Buscando tu computadora…',
                    'Preguntando por tus agentes…',
                    'Tarda un poco la primera vez. Sigue en ello.',
                  ])
                : ListView.separated(
                    itemCount: _agents.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (c, i) {
                      final a = _agents[i];
                      return ListTile(
                        leading: Face(agent: a, link: _link),
                        title: Text(a.name,
                            style: const TextStyle(fontWeight: FontWeight.w600)),
                        subtitle: _AgentLine(agent: a),
                        trailing: const Icon(Icons.chevron_right),
                        // A new name or a new face comes back as `true`, and the
                        // roster behind has to be asked again: the look it is
                        // holding is the old one, and a picture that did not change
                        // on the screen you came from looks exactly like one that
                        // was not saved. Shared with the team view, which opens an
                        // agent the same way.
                        onTap: () => _open(c, a),
                      );
                    },
                  ),
      ),
    );
  }
}

/// Agent pictures, fetched once per version and kept for the run.
///
/// The roster already says WHETHER an agent has one (`look.kind`) and which version
/// of it (`look.v`); only the bytes are a second request. They are not sent with the
/// roster because seven photos inline would make the first screen a quarter of a
/// megabyte on a connection that may be mobile data - and most agents have no photo
/// at all, so most of that would be nothing.
///
/// Keyed by slug AND version, which is what makes a changed picture appear: a new
/// version is a different key, so the old bytes are never shown and never have to be
/// explicitly thrown away.
/// What an agent is, in one line: whether it is up, and whose team it is on.
///
/// The team is the thing a roster of seven most needs and did not have - the names
/// alone say nothing about who works with whom, and that grouping is the owner's own
/// and lives in the team map. Drawn as a small chip rather than more text, so the
/// line still reads as a status and not as a sentence.
class _AgentLine extends StatelessWidget {
  const _AgentLine({required this.agent});

  final Agent agent;

  @override
  Widget build(BuildContext context) {
    final team = agent.teamName;
    if (team.isEmpty) return Text(agent.state);
    return Row(children: [
      Flexible(child: Text(agent.state, overflow: TextOverflow.ellipsis)),
      const SizedBox(width: 8),
      Flexible(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: context.c.panel2,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: context.c.line),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            // The mark, when the team has one. A circle rather than a bare glyph so
            // an emoji and a letter take the same room and the chips line up.
            Container(
              width: 15,
              height: 15,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: context.c.accent.withValues(alpha: 0.18),
                shape: BoxShape.circle,
              ),
              child: Text(
                agent.teamIcon.isNotEmpty
                    ? agent.teamIcon
                    : team.characters.first.toUpperCase(),
                style: TextStyle(
                    fontSize: 9,
                    height: 1.1,
                    fontWeight: FontWeight.w700,
                    color: context.c.accent),
              ),
            ),
            const SizedBox(width: 5),
            Flexible(
              child: Text(team,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 11, color: context.c.muted)),
            ),
          ]),
        ),
      ),
    ]);
  }
}

class Faces {
  static final Map<String, Uint8List?> _got = {};
  static final Map<String, Future<Uint8List?>> _busy = {};

  static String _key(Agent a) => '${a.slug}@${a.look['v'] ?? 0}';

  /// What is already here, for a first paint with no flicker.
  static Uint8List? ready(Agent a) => _got[_key(a)];

  static bool has(Agent a) => (a.look['kind'] ?? '').toString() == 'image';

  static Future<Uint8List?> of(Link link, Agent a) {
    final k = _key(a);
    if (_got.containsKey(k)) return Future.value(_got[k]);
    return _busy[k] ??= _fetch(link, a, k);
  }

  static Future<Uint8List?> _fetch(Link link, Agent a, String k) async {
    try {
      final res = await link.reach('POST', '/api/avatar/get',
          body: {'slug': a.slug});
      final b64 = (res['data_b64'] ?? '').toString();
      final bytes = b64.isEmpty ? null : base64Decode(b64);
      // Remembered even when it is null: an agent with no photo should not be asked
      // about again every time its row scrolls past.
      _got[k] = bytes;
      return bytes;
    } catch (_) {
      // NOT remembered: a request that failed is a connection problem, not an answer,
      // and the next draw should try again.
      return null;
    } finally {
      _busy.remove(k);
    }
  }
}

class Face extends StatefulWidget {
  const Face({super.key, required this.agent, this.size = 42, this.link});
  final Agent agent;
  final double size;

  /// How to fetch the picture. Without it the face is the colour and the initials,
  /// which is what every face was before photos reached the phone at all.
  final Link? link;

  @override
  State<Face> createState() => _FaceState();
}

class _FaceState extends State<Face> {
  Uint8List? _photo;

  @override
  void initState() {
    super.initState();
    _photo = Faces.ready(widget.agent);
    _load();
  }

  @override
  void didUpdateWidget(Face old) {
    super.didUpdateWidget(old);
    // A renamed or re-faced agent arrives as a new look on the same widget.
    if (old.agent.slug != widget.agent.slug ||
        old.agent.look['v'] != widget.agent.look['v']) {
      _photo = Faces.ready(widget.agent);
      _load();
    }
  }

  Future<void> _load() async {
    final link = widget.link;
    if (link == null || !Faces.has(widget.agent) || _photo != null) return;
    final got = await Faces.of(link, widget.agent);
    if (mounted && got != null) setState(() => _photo = got);
  }

  @override
  Widget build(BuildContext context) {
    final photo = _photo;
    if (photo != null) {
      return ClipOval(
        child: Image.memory(photo,
            width: widget.size,
            height: widget.size,
            fit: BoxFit.cover,
            gaplessPlayback: true),
      );
    }
    return _mark(context);
  }

  Widget _mark(BuildContext context) {
    final agent = widget.agent;
    final size = widget.size;
    // No photo, or none yet. The colour and the initials travel with the roster, so
    // this draws immediately and never waits for anything - and the colour is the
    // same one that agent's Chrome window wears.
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: agent.colour,
        shape: BoxShape.circle,
        // The same hairline the console draws (app.css .av): it keeps a pale face from
        // melting into a pale page, and costs nothing on a dark one.
        border: Border.all(color: Colors.black.withValues(alpha: 0.08), width: 1),
      ),
      child: Text(
        agent.emoji.isNotEmpty ? agent.emoji : agent.initials,
        style: TextStyle(
          // White, always - NOT a theme colour. This text sits on the agent's own
          // colour, which is the same in light and dark, so a token that flips with the
          // theme turned the initials black on their own face. app.css says the same
          // thing in words: white "is legible on all eight palette colours".
          color: Colors.white,
          fontWeight: FontWeight.w700,
          fontSize: size * (agent.emoji.isNotEmpty ? 0.5 : 0.38),
        ),
      ),
    );
  }
}

// ── one agent ─────────────────────────────────────────────────────────────────────────

class AgentScreen extends StatefulWidget {
  const AgentScreen(
      {super.key, required this.link, required this.agent, required this.pairing});
  final Link link;
  final Agent agent;
  final Pairing pairing;

  @override
  State<AgentScreen> createState() => _AgentScreenState();
}

class _AgentScreenState extends State<AgentScreen> {
  int _tab = 0;

  /// The agent as this screen currently knows it. A rename or a new face has to show
  /// in the title straight away, and `widget.agent` is whatever the roster held when
  /// this screen opened.
  late Agent _agent = widget.agent;

  /// Something was changed here, so the roster behind is out of date. Carried back
  /// on the way out rather than acted on immediately: closing the conversation the
  /// moment somebody renames an agent is not what 'back' means anywhere else.
  bool _dirty = false;

  /// Held so the app bar's history button can reach into the open conversation.
  final GlobalKey<ChatTabState> _chat = GlobalKey<ChatTabState>();

  bool _searching = false;
  final TextEditingController _find = TextEditingController();

  @override
  void dispose() {
    _find.dispose();
    super.dispose();
  }

  void _startSearch() => setState(() => _searching = true);

  void _stopSearch() {
    _find.clear();
    _chat.currentState?.search('');
    setState(() => _searching = false);
  }

  void _menu(String what) {
    switch (what) {
      case 'past':
        _chat.currentState?.pickPastConversation();
      case 'media':
        final st = _chat.currentState;
        if (st == null) return;
        // Every file in the conversation, which means the whole of it: the chat screen
        // itself only holds the last page.
        st.wholeThread().then((_) => _gallery(st));
      case 'settings':
        _openSettings();
    }
  }

  void _gallery(ChatTabState st) {
        Navigator.of(context).push<int>(MaterialPageRoute(
          builder: (_) => MediaScreen(
            link: widget.link,
            messages: st.messages,
            body: st.bodyFor,
          ),
        )).then((index) {
          // The gallery hands back the message a file came with, if somebody asked
          // to be taken to it.
          if (index != null) {
            setState(() => _tab = 0);
            _chat.currentState?.jumpToMessage(index);
          }
        });
  }

  Future<void> _openSettings() async {
    final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => AgentSettings(
        link: widget.link,
        slug: _agent.slug,
        name: _agent.name,
        isDefault: _agent.isDefault,
        canEdit: widget.pairing.caps.contains('config'),
        onOpenFiles: _chat.currentState == null ? null : () => _menu('media'),
      ),
    ));
    if (changed != true || !mounted) return;
    // In place. This used to pop the whole conversation and land back at the roster,
    // which is not what 'back' means anywhere else: you came from the chat, so you
    // go back to the chat. The roster is told on the way out instead.
    _dirty = true;
    await _reloadAgent();
  }

  /// The agent's own row, after it was changed from the profile screen.
  Future<void> _reloadAgent() async {
    try {
      final res = await widget.link.reach('POST', '/api/agents/list', body: {});
      if (res['ok'] != true || !mounted) return;
      final rows = <Map<String, dynamic>>[
        if (res['default'] != null) (res['default'] as Map).cast<String, dynamic>(),
        for (final e in (res['extra'] as List? ?? []))
          (e as Map).cast<String, dynamic>(),
      ];
      for (final r in rows) {
        final got = Agent.from(r);
        if (got.slug == _agent.slug) {
          setState(() => _agent = got);
          return;
        }
      }
    } catch (_) {
      // The title keeps the name it had. Worth nothing more than that: the change
      // itself already succeeded, this is only the label catching up.
    }
  }

  @override
  Widget build(BuildContext context) {
    final canBrowse = widget.pairing.caps.contains('navegador');
    final tabs = <Widget>[
      ChatTab(key: _chat, link: widget.link, agent: _agent),
      RoutinesTab(link: widget.link, agent: _agent),
      if (canBrowse) BrowserTab(link: widget.link, agent: _agent),
    ];
    return PopScope(
      canPop: false,
      // The roster behind is only told on the way out. Doing it the moment something
      // changed meant closing the conversation somebody was in the middle of.
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.pop(context, _dirty);
      },
      child: Scaffold(
      appBar: _searching
          ? AppBar(
              titleSpacing: 0,
              leading: IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: _stopSearch,
              ),
              title: TextField(
                controller: _find,
                autofocus: true,
                textInputAction: TextInputAction.search,
                decoration: InputDecoration(
                  hintText: tr('Buscar en la conversación'),
                  border: InputBorder.none,
                  filled: false,
                ),
                onChanged: (q) => setState(() => _chat.currentState?.search(q)),
              ),
              actions: [
                // The count is the whole point of a search bar: 'nothing' and 'you are
                // looking at the first of nine' are different answers and look the same
                // without it.
                Builder(builder: (_) {
                  final st = _chat.currentState;
                  final n = st?.hitCount ?? 0;
                  return Row(children: [
                    Text(n == 0 ? tr('Nada') : '${st!.hitIndex}/$n',
                        style: TextStyle(fontSize: 13, color: context.c.muted)),
                    IconButton(
                      icon: const Icon(Icons.keyboard_arrow_up),
                      onPressed: n == 0 ? null : () => setState(() => st!.nextHit(-1)),
                    ),
                    IconButton(
                      icon: const Icon(Icons.keyboard_arrow_down),
                      onPressed: n == 0 ? null : () => setState(() => st!.nextHit(1)),
                    ),
                  ]);
                }),
              ],
            )
          : AppBar(
              titleSpacing: 0,
              // The name opens the agent, which is where every phone app puts the
              // thing you are looking at: tapping who you are talking to is how you
              // find out about them. The menu entry stays, because a menu is where
              // somebody looks when tapping the title did not occur to them.
              title: InkWell(
                onTap: _openSettings,
                child: Row(children: [
                  Face(agent: _agent, size: 30, link: widget.link),
                  const SizedBox(width: 10),
                  Expanded(
                      child:
                          Text(_agent.name, overflow: TextOverflow.ellipsis)),
                ]),
              ),
              actions: [
                if (_tab == 0)
                  IconButton(
                    icon: const Icon(Icons.search),
                    tooltip: tr('Buscar'),
                    onPressed: _startSearch,
                  ),
                if (_tab == 0)
                  IconButton(
                    icon: const Icon(Icons.edit_square),
                    tooltip: tr('Conversación nueva'),
                    onPressed: () => _chat.currentState?.newConversation(),
                  ),
                // The rest behind a menu: four icons in a row is a toolbar nobody reads.
                PopupMenuButton<String>(
                  onSelected: _menu,
                  itemBuilder: (_) => [
                    PopupMenuItem(
                      value: 'past',
                      child: Text(tr('Conversaciones anteriores')),
                    ),
                    PopupMenuItem(
                      value: 'media',
                      child: Text(tr('Fotos y archivos')),
                    ),
                    PopupMenuItem(
                      value: 'settings',
                      child: Text(tr('Sobre este agente')),
                    ),
                  ],
                ),
              ],
            ),
      body: tabs[_tab.clamp(0, tabs.length - 1)],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab.clamp(0, tabs.length - 1),
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [
          NavigationDestination(
              icon: const Icon(Icons.chat_bubble_outline), label: tr('Hablar')),
          NavigationDestination(
              icon: const Icon(Icons.schedule), label: tr('Rutinas')),
          if (canBrowse)
            NavigationDestination(
                icon: const Icon(Icons.travel_explore), label: tr('Navegador')),
        ],
      ),
      ),
    );
  }
}

/// The conversation currently open with each agent.
///
/// Kept across launches, because closing an app is not the same as finishing a
/// conversation - a question asked on the way out of the house should still be there,
/// with its answer, when the phone comes back out of the pocket. Only the id is saved;
/// the messages stay on the computer.
final Map<String, String> _openThreads = {};

/// Loaded once at startup into [_openThreads].
Future<void> loadOpenThreads() async {
  _openThreads.addAll(await Store.loadThreads());
}

/// Messages that never reached the computer, by agent.
///
/// In memory and nowhere else, deliberately: the conversations live on the computer
/// precisely so that a phone which is lost is not carrying a copy of them, and a
/// message that failed to send is no different - it is the same words. What this map
/// buys is the thing a phone really does lose, which is not a crash but a thumb:
/// backing out of a chat, or opening another agent, while a message is sitting there
/// unsent. Before this, leaving the screen was enough to throw it away.
final Map<String, List<Map<String, dynamic>>> _unsent = {};

/// Whether the end of a conversation already contains this message.
///
/// Asked before anything is sent a SECOND time. A send that times out is the one case
/// where the request went out and only the answer was lost, and these agents do
/// things - a second copy of "send that email" is not a cosmetic duplicate. The link
/// refuses to repeat /api/talk/send for exactly that reason; a retry is the owner's
/// decision, and this is what keeps that decision from quietly costing them twice.
///
/// Compared on the words, because nothing in the protocol hands back an id for a
/// message - and the words are what would be said twice.
/// May the last conversation still be dropped into this screen?
///
/// Not once anything has actually been SAID here: an offer to carry on the previous
/// conversation, landing on top of a message somebody has already sent, is an offer to
/// throw that message away.
///
/// A message that FAILED is not one of those. It was never said to anybody, and the
/// conversation it was written for is the one arriving - so it keeps its place at the
/// bottom and the old turns go in above it. Getting this wrong is quiet in both
/// directions: too strict and a failed message blocks the offer for good, too loose
/// and a sent one is overwritten.
///
/// Words, then, and not a session id: a send that failed after opening the
/// conversation leaves one behind with nothing in it, and refusing the offer over an
/// empty session would mean one lost message costs the owner the thread they were in
/// the middle of.
bool canResumeOver(List<Map<String, dynamic>> msgs) =>
    msgs.every((m) => m['failed'] == true);

bool saidAlready(List<Map<String, dynamic>> tail, String outgoing) {
  final want = outgoing.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (want.isEmpty) return false;
  for (final m in tail) {
    if (m['role'] != 'user') continue;
    final said = (m['text'] ?? '').toString().replaceAll(RegExp(r'\s+'), ' ').trim();
    if (said == want) return true;
  }
  return false;
}

/// The line under "continue the last conversation": the last thing said in it.
///
/// The strip used to offer a conversation and say nothing about which one, so the
/// only way to find out what it was about was to open it - and opening it is the one
/// thing the strip exists to make optional. The computer's own `preview` is no help
/// here: it is the FIRST message of the session, which for a conversation of sixty
/// turns is wherever it started rather than where it was left.
///
/// Markdown is flattened rather than rendered. A hint is one line of plain words, and
/// an agent's last turn is as likely as not to open with a heading or a bullet.
String resumeHint(Map<String, dynamic> last, String agentName) {
  var text = (last['text'] ?? '').toString();
  // A reply quotes what it answers. The quote is not what was last said.
  final r = splitReply(text);
  if (r != null) text = r.$3;
  text = text
      .replaceAll(RegExp(r'^\s*#{1,6}\s+', multiLine: true), '')
      .replaceAll(RegExp(r'^\s*[-*+]\s+', multiLine: true), '')
      .replaceAll(RegExp(r'[*_`>]'), '')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();
  if (text.isEmpty) {
    // A turn can be a file and nothing else - a photo sent without a word, or a
    // recording the computer could not make out.
    final files = (last['files'] as List? ?? []);
    final voice = files.any((f) => f is Map && f['kind'] == 'audio');
    text = voice
        ? tr('Nota de voz')
        : files.isEmpty
            ? ''
            : tr('Un archivo');
  }
  if (text.isEmpty) return '';
  if (text.length > 160) text = '${text.substring(0, 160)}…';
  final who = last['role'] == 'user' ? tr('Tú') : agentName.trim();
  return who.isEmpty ? text : '$who: $text';
}

/// A message that begins with a quote is a reply. Returns (who, what they said,
/// the rest), or null.
///
/// Top level so it can be tested on its own: this is a parser, its input is written
/// by an agent as often as by a person, and the ways it must NOT fire - a message
/// that merely starts with a quotation, a bold word at the front - matter as much as
/// the ways it must.
(String, String, String)? splitReply(String text) {
    // Spaces and tabs, not \s: \s swallowed the blank line that separates the quote
    // from the answer, so '> **Yo:**' followed by a blank line and 'nada' parsed as a
    // reply quoting "nada" - and the answer disappeared into the header. One line for
    // the name, one for what was said, which is the shape this writes and the shape an
    // agent copying it writes back.
    final m = RegExp(r'^>[ \t]*\*\*([^\n]+?):\*\*[ \t]*([^\n]*)(?:\n|$)')
        .firstMatch(text);
    if (m == null) return null;
    final who = (m.group(1) ?? '').trim();
    final quoted = (m.group(2) ?? '').trim();
    if (who.isEmpty || quoted.isEmpty) return null;
    return (who, quoted, text.substring(m.end).trimLeft());
  }

/// What the conversation area is showing.
enum ChatView { off, connecting, loading, nothingYet, silent, messages }

/// Which of them, from the flags the screen holds.
///
/// Written as a function of its inputs rather than as a chain of `else if` inside
/// build() because the bug it exists to prevent is a path that forgets to set a flag:
/// opening a past conversation used to leave `loadingHistory` false for the whole
/// fetch, so a screen that was busy over mobile data rendered as "you have not said
/// anything to each other" - which reads as a crash, not as waiting.
///
/// The distinction that matters is the last one: a conversation with nothing in it is
/// not the same as a conversation that has not arrived. Only a thread that was
/// actually FETCHED and came back with no readable turns is silent - and those exist,
/// a routine's session is all tool calls and no speech.
ChatView chatViewFor({
  required bool talkOff,
  required bool connecting,
  required bool loadingHistory,
  required bool ready,
  required bool hasSession,
  required bool loaded,
  required bool hasMessages,
  required bool hasError,
}) {
  if (talkOff) return ChatView.off;
  if (connecting) return ChatView.connecting;
  if (loadingHistory) return ChatView.loading;
  if (hasMessages) return ChatView.messages;
  // An error already explains itself in the strip above; spinning on top of it would
  // promise something that is not coming.
  if (hasError) return ChatView.nothingYet;
  if (!ready) return ChatView.loading;
  if (hasSession && !loaded) return ChatView.loading;
  return hasSession ? ChatView.silent : ChatView.nothingYet;
}

class ChatTab extends StatefulWidget {
  const ChatTab({super.key, required this.link, required this.agent});
  final Link link;
  final Agent agent;

  @override
  State<ChatTab> createState() => ChatTabState();
}

class ChatTabState extends State<ChatTab> with WidgetsBindingObserver {
  /// Whether the owner is actually looking at this. A reply that lands while they are
  /// reading it does not need a notification; the same reply while the phone is in a
  /// pocket does, and that is the only difference between the two.
  bool _inFront = true;

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _inFront = state == AppLifecycleState.resumed;
  }

  final _input = TextEditingController();
  final _scroll = ScrollController();
  List<Map<String, dynamic>> _msgs = [];

  /// How much of a conversation arrives at once, and where in the whole thread the
  /// part on screen begins.
  ///
  /// A conversation of sixty turns measured 157 KB before the computer started sending
  /// a page of it - most of which was tool calls and reasoning this screen throws away
  /// on arrival. Now it sends the last turns, already filtered, and the ones before
  /// them when the owner scrolls up to look.
  static const int _page = 25;
  int _histStart = 0;
  bool _histMore = false;
  bool _histBusy = false;

  /// Whether the conversation on screen has actually been read from the computer.
  /// An empty list means "not yet" until this is true, and "nothing was said" after.
  bool _histLoaded = false;

  /// The conversation this agent was last having, if there is one to go back to.
  /// Empty once it has been opened, or once this one has become a conversation of its
  /// own - an offer to "continue" over a message somebody has already sent is an
  /// offer to throw it away.
  String _resume = '';

  /// The last thing said in that conversation, once it has been asked for. Empty
  /// until then, and the strip simply says less.
  String _resumeHint = '';
  String _session = '';
  bool _sending = false;
  String _error = '';

  /// Talking is off for this agent. Not an error so much as a thing not done yet,
  /// and it has its own screen because 'Todavía no está activado' told you what was
  /// wrong and then left you to find the switch on a computer you may not be sitting
  /// at - which is the whole point of holding the phone.
  bool _talkOff = false;
  bool _turningOn = false;

  /// Still finding out whether this agent can be talked to and where the
  /// conversation left off. There was no state for this: the screen showed an empty
  /// message list and a composer that could not be typed into, which is a chat that
  /// looks finished and broken at the same time. On mobile data it lasted seconds.
  bool _connecting = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _start();
  }

  /// `profile`, not `slug`: wizard_server resolves the agent from body["profile"]
  /// and ignores anything else, so a slug here reached the DEFAULT agent - this
  /// conversation would have been with the wrong agent, under the right name.
  /// The same body builder, for screens that ask on this chat's behalf.
  Map<String, dynamic> bodyFor([Map<String, dynamic>? extra]) => _body(extra);

  /// Load every turn of this conversation. For the gallery and for search,
  /// which are both about all of it. See _wholeThread.
  Future<void> wholeThread() => _wholeThread();

  Map<String, dynamic> _body([Map<String, dynamic>? extra]) => {
        if (!widget.agent.isDefault) 'profile': widget.agent.slug,
        ...?extra,
      };

  Future<void> _start() async {
    if (!mounted) return;
    setState(() {
      _connecting = true;
      // Anything written to this agent that never left the phone comes back with the
      // screen - and it comes back FIRST, before the computer is asked anything.
      //
      // It used to be restored further down, after the status request had answered,
      // which meant the one time it did not happen was the one time it mattered: a
      // phone that cannot reach the computer is a phone whose messages are not
      // getting through, so that is exactly when there is something waiting. Walking
      // out of the chat and back in, with no signal, lost it. These words are local;
      // they owe the computer nothing.
      _msgs = [...?_unsent[widget.agent.slug]];
      _histLoaded = true;
    });
    try {
      // Whether talking is switched on, first: everything below depends on it.
      // `quick`: this screen reads two booleans out of that answer, and the full
      // one costs the computer about six seconds to produce - which was most of the
      // wait before a conversation appeared, on any network.
      final st = await widget.link
          .reach('POST', '/api/talk/status', body: _body({'quick': true}));
      // The PHONE switch, which is not the same question as whether the computer
      // offers a chat window. An agent can be on here and off there, and the point
      // of splitting them was that one answer could not mean both.
      if (st['ok'] != true || st['phone'] != true) {
        setState(() {
          _talkOff = true;
          _error = '';
        });
        return;
      }
      // Opening an agent opens a BLANK page, and offers the old conversation.
      //
      // Carrying on where you left off is what you usually want, so this used to fetch
      // the last thread and wait for it. But waiting is the cost of the whole screen:
      // the point of opening an agent is almost always to say something, and that is
      // possible the instant the page is blank. So the thread is offered as a strip at
      // the top instead - one tap, fetched while you are already typing - and the one
      // thing it must never do is arrive on top of a message you have started.
      _session = '';
      _histLoaded = true;      // nothing is coming unless it is asked for
      _resume = _openThreads[widget.agent.slug] ?? '';
      setState(() {
        _ready = true;
        _error = '';
      });
      _resumeLast();
      if (_resume.isEmpty) {
        // Which conversation it WOULD be, asked for quietly in the background: the
        // strip says "continue" or it says nothing, and either way the page is
        // already usable.
        widget.link
            .reach('POST', '/api/talk/sessions', body: _body({'limit': 1}))
            .then((sessions) {
          if (!mounted || _session.isNotEmpty) return;
          final rows = sessions['sessions'] as List? ?? [];
          if (rows.isNotEmpty && rows.first is Map) {
            setState(() =>
                _resume = ((rows.first as Map)['id'] ?? '').toString());
            _resumeLast();
          }
        }).catchError((_) {});
      }
    } catch (e) {
      setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _connecting = false);
    }
  }

  /// The rows the computer keeps are a transcript of everything the agent did - tool
  /// calls, its own reasoning, the summaries written when a context window filled up.
  /// Only what somebody actually said belongs in a conversation.
  List<Map<String, dynamic>> _readable(List rows) {
    final out = <Map<String, dynamic>>[];
    for (final r in rows) {
      if (r is! Map) continue;
      final role = (r['role'] ?? '').toString();
      if (role != 'user' && role != 'assistant') continue;   // tool, system: not speech
      if (r['tool_calls'] != null) continue;                 // an action, not a sentence
      // `content` is what Hermes calls it. Reading `text` here is why every bubble in
      // the first build of this screen came out blank.
      final text = (r['content'] ?? r['text'] ?? '').toString().trim();
      // The files come from the computer already lifted out of the text, named, sized
      // and - when they are small enough to fetch - with an id to fetch them by. The
      // app used to pick the `MEDIA:` lines apart itself and could only ever show a
      // name, because a path on somebody else's disk is not something a phone can open.
      final files = [
        for (final f in (r['files'] as List? ?? []))
          if (f is Map) f.cast<String, dynamic>(),
      ];
      if (text.isEmpty && files.isEmpty) continue;
      if (text.startsWith('[CONTEXT COMPACTION')) continue;  // bookkeeping, not a turn
      out.add({
        'role': role,
        'text': text,
        if (files.isNotEmpty) 'files': files,
      });
    }
    return out;
  }

  Future<void> _history() async {
    if (_session.isEmpty) return;
    final h = await widget.link.reach('POST', '/api/talk/history',
        body: _body({'session_id': _session, 'limit': _page}));
    if (h['ok'] != true) {
      setState(() => _error = tr((h['detail'] ?? tr('No se pudo leer la conversación.')).toString()));
      return;
    }
    setState(() {
      _msgs = _readable(h['messages'] as List? ?? []);
      _histStart = (h['start'] as num?)?.toInt() ?? 0;
      _histMore = h['has_more'] == true;
      _histLoaded = true;
      _error = '';
    });
    _toBottom();
  }

  /// The turns before the ones on screen.
  ///
  /// Indices count from the START of the conversation, which is what makes this safe
  /// while the agent is still answering: a page asked for by distance from the end
  /// would shift under the reader every time a new turn arrived.
  Future<void> _earlier() async {
    if (_histBusy || !_histMore || _session.isEmpty) return;
    setState(() => _histBusy = true);
    try {
      final h = await widget.link.reach('POST', '/api/talk/history',
          body: _body({
            'session_id': _session,
            'limit': _page,
            'before': _histStart,
          }));
      if (!mounted || h['ok'] != true) return;
      final older = _readable(h['messages'] as List? ?? []);
      // Where the bottom of the list is now, so the view can be put back on the same
      // message after the list grows upwards underneath it.
      final was = _scroll.hasClients ? _scroll.position.maxScrollExtent : 0.0;
      final at = _scroll.hasClients ? _scroll.offset : 0.0;
      setState(() {
        _msgs = [...older, ..._msgs];
        // A remembered index into the list points at a different message now.
        if (_flash >= 0) _flash += older.length;
        _histStart = (h['start'] as num?)?.toInt() ?? 0;
        _histMore = h['has_more'] == true;
      });
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!_scroll.hasClients) return;
        final grew = _scroll.position.maxScrollExtent - was;
        if (grew > 0) _scroll.jumpTo((at + grew).clamp(0.0, _scroll.position.maxScrollExtent));
      });
    } finally {
      if (mounted) setState(() => _histBusy = false);
    }
  }

  /// The whole conversation, for the two things that are about all of it.
  ///
  /// Searching inside a thread and listing the files in it both mean every turn, not
  /// the last twenty-five - a search that quietly looked at a quarter of the
  /// conversation would be worse than no search. Paid once, when one of them is asked
  /// for, rather than on every opening.
  Future<void> _wholeThread() async {
    if (!_histMore || _session.isEmpty || _histBusy) return;
    setState(() => _histBusy = true);
    try {
      final h = await widget.link
          .reach('POST', '/api/talk/history', body: _body({'session_id': _session}));
      if (!mounted || h['ok'] != true) return;
      setState(() {
        _msgs = _readable(h['messages'] as List? ?? []);
        _histStart = 0;
        _histMore = false;
      });
    } finally {
      if (mounted) setState(() => _histBusy = false);
    }
  }
  /// The offer to go back to the last conversation.
  ///
  /// Not a modal, not a redirect: a line at the top of an empty page that can be
  /// ignored entirely. Tapping it loads the thread while the composer stays live.
  Widget _resumeStrip() {
    // What it was about, when the computer has said. While it is being fetched - and
    // while the thread itself is being opened - the strip says the thing that is
    // true then instead: none of this is in the way of writing.
    final hint = (_resumeHint.isNotEmpty && !_histBusy) ? _resumeHint : '';
    // Material rather than a decorated box: the tile paints its ink on the nearest
    // Material above it, and a box with its own background in between hides the
    // splash entirely. This strip is a thing you tap, and it was answering taps
    // without showing that it had - the border moves onto the tile's own shape, which
    // is also what clips the ink to the rounded corners.
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
      child: Material(
        color: context.c.panel2,
        borderRadius: BorderRadius.circular(12),
        child: ListTile(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: context.c.line),
        ),
        dense: true,
        isThreeLine: hint.isNotEmpty,
        leading: Icon(Icons.history, size: 20, color: context.c.accent),
        title: Text(tr('Seguir la conversación anterior'),
            style: TextStyle(fontSize: 13, color: context.c.ink)),
        subtitle: hint.isEmpty
            ? Text(tr('Mientras tanto puedes escribir o grabar.'),
                style: TextStyle(fontSize: 11, color: context.c.muted))
            : Text(hint,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: context.c.muted)),
        trailing: _histBusy
            ? SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: context.c.muted))
            : null,
        onTap: _histBusy ? null : _resumeThread,
      ),
      ),
    );
  }

  /// The last thing said in the conversation being offered.
  ///
  /// One small request, in the background, after the page is already usable - the
  /// same bargain the offer itself is made on. `limit` is small but not 1: the
  /// computer filters tool calls and reasoning out of a transcript already, and this
  /// side filters again, so asking for a handful leaves something to show if the very
  /// last row is one the phone throws away.
  Future<void> _resumeLast() async {
    final id = _resume;
    if (id.isEmpty) return;
    try {
      final h = await widget.link.reach('POST', '/api/talk/history',
          body: _body({'session_id': id, 'limit': 3}));
      // Not for the conversation being offered any more: the owner has opened one,
      // started another, or this answer simply lost a race with a newer question.
      if (!mounted || h['ok'] != true || _resume != id) return;
      final rows = _readable(h['messages'] as List? ?? []);
      if (rows.isEmpty) return;
      setState(() => _resumeHint = resumeHint(rows.last, widget.agent.name));
    } catch (_) {
      // A hint that could not be fetched is a hint not shown. The offer still works,
      // and an error strip over a line of nicety would be a worse screen.
    }
  }

  /// Fetch the last conversation into this screen, without taking it over.
  Future<void> _resumeThread() async {
    final id = _resume;
    if (id.isEmpty || _histBusy) return;
    setState(() => _histBusy = true);
    try {
      final h = await widget.link.reach('POST', '/api/talk/history',
          body: _body({'session_id': id, 'limit': _page}));
      if (!mounted || h['ok'] != true) return;
      // Anything typed and SENT while this was in flight has started a conversation of
      // its own, and the old one is no longer the one being had. Dropped rather than
      // merged: two threads interleaved by arrival time is nobody's conversation.
      final stuck = _msgs.where((m) => m['failed'] == true).toList();
      if (!canResumeOver(_msgs)) {
        setState(() => _resume = '');
        return;
      }
      setState(() {
        _session = id;
        _msgs = [..._readable(h['messages'] as List? ?? []), ...stuck];
        _histStart = (h['start'] as num?)?.toInt() ?? 0;
        _histMore = h['has_more'] == true;
        _histLoaded = true;
        _resume = '';
        _error = '';
      });
      _openThreads[widget.agent.slug] = id;
      await Store.saveThreads(_openThreads);
      _toBottom();
    } finally {
      if (mounted) setState(() => _histBusy = false);
    }
  }

  /// The top of a conversation that has more behind it.
  Widget _earlierRow() {
    // Asked for as it appears. The guard inside _earlier is what keeps a list that
    // rebuilds often from asking twice.
    WidgetsBinding.instance.addPostFrameCallback((_) => _earlier());
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Center(
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: context.c.muted)),
          const SizedBox(width: 8),
          Text(tr('Trayendo lo de antes…'),
              style: TextStyle(fontSize: 12, color: context.c.muted)),
        ]),
      ),
    );
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });
  }

  /// What the agent is told about the message being answered.
  ///
  /// A quote, because that is what a person would write and what the agent already
  /// understands - there is no 'in reply to' field in the protocol, and inventing one
  /// would mean the agent needing to know about it.
  String _withQuote(String text) {
    final r = _replyTo;
    if (r == null) return text;
    final who = r['role'] == 'user' ? 'Yo' : widget.agent.name;
    var quoted = (r['text'] ?? '').toString().trim().replaceAll('\n', ' ');
    if (quoted.length > 300) quoted = '${quoted.substring(0, 300)}…';
    return '> **$who:** $quoted\n\n$text';
  }

  /// A message that begins with a quote is a reply, and is drawn as one.
  ///
  /// There is no 'in reply to' field in the protocol - the quote IS the protocol,
  /// which is why the agent understands one without being taught anything. That cuts
  /// both ways: an agent that answers by quoting is replying to a particular message,
  /// exactly as Hermes does on Telegram, and this reads it back the same way for
  /// both of them. Nothing was added to the wire; what was missing was the drawing.
  ///
  /// Returns (who, what they said, the rest of the message), or null.
  static (String, String, String)? _asReply(String text) => splitReply(text);

  /// Where the quoted message is, so tapping the quote can go there. Matched on the
  /// text because that is all a quote carries; the newest match wins, since quoting
  /// something said twice almost always means the recent one.
  int _findQuoted(String quoted, int before) {
    final needle = quoted.endsWith('…')
        ? quoted.substring(0, quoted.length - 1)
        : quoted;
    for (var i = (before - 1).clamp(0, _msgs.length - 1); i >= 0; i--) {
      final t = (_msgs[i]['text'] ?? '').toString().replaceAll('\n', ' ');
      if (needle.isNotEmpty && t.contains(needle)) return i;
    }
    return -1;
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    // A message can be files alone - an agent that is sent a screenshot and nothing
    // else has still been asked something.
    if ((text.isEmpty && _attached.isEmpty) || _sending) return;
    final outgoing = _withQuote(text);
    final mine = <String, dynamic>{
      'role': 'user',
      'text': outgoing,
      // What is drawn, and what goes on the wire. The same thing here; not for a
      // voice note, where the bubble shows the transcript and the agent is sent that
      // transcript under whatever is being replied to.
      'sendText': outgoing,
      'files': [for (final f in _attached) (f['name'] ?? '').toString()],
      'sendFiles': [for (final f in _attached) f['id']],
    };
    setState(() {
      _msgs = [..._msgs, mine];
      _input.clear();
      _attached.clear();
      _replyTo = null;
    });
    await _deliver(mine);
  }

  /// Everything except the bubble that says the agent is thinking.
  static bool _notThinking(Map<String, dynamic> m) =>
      m['role'] != 'assistant' || m['pending'] != true;

  /// One message on its way to the computer - the first time, or the fifth.
  ///
  /// `mine` is already the last bubble in the conversation and carries everything
  /// needed to send it again: the words, the files, and the conversation it belongs
  /// to. That is the whole point. A send that failed used to leave the bubble sitting
  /// there looking exactly like one that had arrived, with the words cleared out of
  /// the box underneath and no way to get them back - and on a phone that walks
  /// between wifi and mobile data, which is the phone this is for, that happens often
  /// enough to lose real messages.
  Future<void> _deliver(Map<String, dynamic> mine) async {
    if (_sending) return;
    final outgoing = (mine['sendText'] ?? mine['text'] ?? '').toString();
    final files = [for (final f in (mine['sendFiles'] as List? ?? [])) f];
    setState(() {
      _sending = true;
      _error = '';
      mine['failed'] = false;
      _msgs = [
        ..._msgs.where(_notThinking),
        {'role': 'assistant', 'pending': true},
      ];
    });
    _toBottom();
    try {
      // From here the agent may take minutes. The service keeps this app out of the
      // range where Android freezes it, so putting the phone away does not lose the
      // wait. The computer would finish the turn either way - this is about whether
      // the phone is still listening when it does.
      // Asked here rather than at startup: a permission request makes sense next to
      // the thing it is for, and this is the first moment the answer could matter.
      if (!_askedToNotify) {
        _askedToNotify = true;
        if (!await Native.canNotify()) await Native.askNotify();
      }
      await Native.waitStart(widget.agent.name);
      // The conversation this message belongs to. A retry after the screen was closed
      // and opened again has no session in hand any more, and the thread it was
      // written in is the thread it should still go to.
      if (_session.isEmpty) _session = (mine['session'] ?? '').toString();
      // The conversation is created by the first thing said in it.
      if (_session.isEmpty) {
        final made = await widget.link.reach('POST', '/api/talk/new', body: _body());
        if (made['ok'] != true) {
          throw LinkError(tr((made['detail'] ?? tr('No se pudo abrir la conversación.')).toString()));
        }
        _session = ((made['session'] as Map?)?['id'] ?? made['id'] ?? '').toString();
        if (_session.isEmpty) {
          throw LinkError(tr('La computadora no abrió ninguna conversación.'));
        }
        _openThreads[widget.agent.slug] = _session;
        await Store.saveThreads(_openThreads);
      } else if (mine['tried'] == true) {
        // It may already be there. Silence after a request has gone out is the one
        // failure that can mean "it arrived and the answer was lost", and these
        // agents DO things - a second copy of "send that email" is not a cosmetic
        // duplicate. So before saying it twice, look at what the computer has.
        if (await _alreadyThere(outgoing)) {
          _unqueue(mine);
          // What the computer has IS the conversation now, so it is read back whole -
          // including the answer, if the agent has finished one. Anything else still
          // waiting to be sent is put back underneath afterwards: the reload knows
          // nothing about those, and they would otherwise vanish off a screen that
          // still has them queued.
          final waiting = [...?_unsent[widget.agent.slug]];
          setState(() => _msgs = _msgs.where(_notThinking).toList());
          await _history();
          if (!mounted) return;
          if (waiting.isNotEmpty) {
            setState(() => _msgs = [
                  ..._msgs.where((x) => !waiting.any((y) => identical(x, y))),
                  ...waiting,
                ]);
          }
          // Said out loud, because the owner pressed a button that promised to send
          // it and nothing was sent. Silence here looks like the retry did nothing.
          _say(tr('Ese mensaje ya había llegado.'));
          _toBottom();
          return;
        }
      }
      mine['session'] = _session;
      mine['tried'] = true;
      final res = await widget.link.reach('POST', '/api/talk/send',
          body: _body({
            'session_id': _session,
            'text': outgoing,
            if (files.isNotEmpty) 'files': files,
          }),
          wait: Link.thinkingWait);
      if (res['ok'] != true) throw LinkError(tr((res['detail'] ?? tr('Sin respuesta')).toString()));
      // The reply comes back on the send itself; re-reading the whole transcript to
      // find it would also drag in every tool call the agent made on the way.
      final reply = (res['reply'] ?? '').toString().trim();
      final shown = reply.isEmpty ? tr('(sin respuesta)') : reply;
      _unqueue(mine);
      mine['failed'] = false;
      // And this is a conversation of its own now, so the old one is no longer being
      // offered. canResumeOver has refused to open it from this moment on anyway -
      // the strip simply went on sitting there saying otherwise, above a live thread,
      // showing the last line of a different one. A message that FAILED does not do
      // this: nothing was said to anybody, and the offer still stands.
      _resume = '';
      // The screen may be gone - somebody walked out of the chat while the agent was
      // thinking - and the answer still arrived. Drawing it is optional; telling them
      // about it is not.
      if (mounted) {
        setState(() {
          // Whatever the status request said, or failed to say when the screen was
          // opened with no signal: a turn that just came back from this agent is a
          // better answer to "can I talk to it" than any question about it. Without
          // this, rescuing a message offline left the owner looking at the reply with
          // a composer that still said «no disponible» underneath.
          _ready = true;
          _msgs = [
            ..._msgs.where(_notThinking),
            {
              'role': 'assistant',
              'text': shown,
              if (res['files'] is List) 'files': res['files'],
            },
          ];
        });
        _toBottom();
      }
      // Only when they are not already looking at it.
      if (!_inFront) {
        await Native.notify(widget.agent.name, shown);
      }
    } catch (e) {
      // Filed first and redrawn second, in that order and deliberately: leaving the
      // chat is precisely what somebody does while a message refuses to go, and the
      // words have to survive that. They are plain data in a map; only the redraw
      // needs a screen.
      _queue(mine);
      mine['failed'] = true;
      mine['pending'] = false;
      if (mounted) {
        setState(() {
          _msgs = _msgs.where(_notThinking).toList();
          _error = humanError(e);
        });
      }
    } finally {
      await Native.waitStop();
      if (mounted) setState(() => _sending = false);
    }
  }

  /// Is this message already in the conversation on the computer?
  ///
  /// A failure to ask is not a reason to refuse: silence here means only that we do
  /// not know, and between a message said twice and a message lost, the owner pressed
  /// a button that says "send it again".
  Future<bool> _alreadyThere(String outgoing) async {
    if (_session.isEmpty) return false;
    try {
      final h = await widget.link.reach('POST', '/api/talk/history',
          body: _body({'session_id': _session, 'limit': 4}));
      if (h['ok'] != true) return false;
      return saidAlready(_readable(h['messages'] as List? ?? []), outgoing);
    } catch (_) {
      return false;
    }
  }

  /// Send it again, picking up at whichever step failed.
  Future<void> _retry(Map<String, dynamic> m) async {
    if (_sending) return;
    // A recording whose words never came back has to be listened to again first.
    if (m['needsVoice'] == true) return _deliverVoice(m);
    return _deliver(m);
  }

  /// Take a message that never left back out of the conversation.
  ///
  /// The words go back into the box rather than nowhere. Discarding is how somebody
  /// rewrites a message that is not going to go through as it stands, and it must not
  /// be the one gesture on this screen that loses it. A recording is the exception -
  /// there is nothing to put back in a box - so that one really does go.
  void _discard(Map<String, dynamic> m) {
    _unqueue(m);
    final back = m['voice'] == true ? '' : (m['text'] ?? '').toString();
    setState(() {
      _msgs = _msgs.where((x) => !identical(x, m)).toList();
      if (back.isNotEmpty) {
        // Without the quote: the reply it was answering is chosen again by swiping,
        // and a composer full of markdown is not a message anybody wrote.
        _input.text = splitReply(back)?.$3 ?? back;
        _input.selection = TextSelection.collapsed(offset: _input.text.length);
      }
    });
  }

  void _queue(Map<String, dynamic> m) {
    final list = _unsent.putIfAbsent(widget.agent.slug, () => []);
    if (!list.any((x) => identical(x, m))) list.add(m);
  }

  void _unqueue(Map<String, dynamic> m) {
    final list = _unsent[widget.agent.slug];
    if (list == null) return;
    list.removeWhere((x) => identical(x, m));
    if (list.isEmpty) _unsent.remove(widget.agent.slug);
  }

  /// What a message that did not get through offers.
  ///
  /// On the bubble rather than in the strip at the top: the strip says what went
  /// wrong, and this says which message it went wrong for. In a conversation where
  /// two things were sent and one of them failed, the top of the screen cannot tell
  /// you that - and the thing the owner wants to press is next to their own words.
  Widget _failedRow(Map<String, dynamic> m, Color ink) {
    final faded = ink.withValues(alpha: 0.85);
    // A Wrap rather than a Row: the sentence and the two buttons stay side by side
    // while they fit and drop onto two lines when they do not, as a whole block each.
    // As one Row the sentence was squeezed into whatever the buttons left over - on a
    // German phone "Wurde nicht gesendet." came out as three stacked words beside the
    // button, which is what a real phone showed and no test had thought to ask.
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 6,
        children: [
          // Flexible, because a Row measures a child that is not flexible at its
          // natural width - so a sentence in a language longer than Spanish runs out
          // of the bubble instead of wrapping inside it.
          Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.error_outline, size: 15, color: faded),
            const SizedBox(width: 5),
            Flexible(
              child: Text(tr('No se envió.'),
                  style: TextStyle(fontSize: 12, color: faded)),
            ),
          ]),
          Row(mainAxisSize: MainAxisSize.min, children: [
            Flexible(
                child: TextButton(
              onPressed: _sending ? null : () => _retry(m),
              style: TextButton.styleFrom(
                foregroundColor: ink,
                padding: const EdgeInsets.symmetric(horizontal: 8),
                minimumSize: const Size(0, 32),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: Text(tr('Reintentar'),
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
            )),
            IconButton(
              onPressed: _sending ? null : () => _discard(m),
              icon: Icon(Icons.close, size: 16, color: faded),
              tooltip: tr('Descartar'),
              visualDensity: VisualDensity.compact,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            ),
          ]),
        ],
      ),
    );
  }

  /// Talking is switched on for this agent, so there is somewhere for a message to go.
  /// A session is opened on the first send, not on arrival.
  bool _ready = false;

  /// Files chosen but not yet sent. Uploaded as they are picked, so the wait happens
  /// while the owner is still writing rather than after they press send.
  final List<Map<String, dynamic>> _attached = [];
  bool _attaching = false;

  /// The message being answered, if any - Telegram's swipe, same gesture.
  Map<String, dynamic>? _replyTo;

  /// Once per run. Android only shows the dialog the first time anyway, and nagging
  /// afterwards would be asking a question that has already been answered.
  bool _askedToNotify = false;

  /// What is being looked for in this conversation, if anything.
  String _query = '';

  /// Which match is in view, so the arrows can walk through them.
  int _hit = 0;

  /// Message indices that contain the query, oldest first.
  List<int> get _hits {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return const [];
    final out = <int>[];
    for (var i = 0; i < _msgs.length; i++) {
      if ((_msgs[i]['text'] ?? '').toString().toLowerCase().contains(q)) out.add(i);
    }
    return out;
  }

  /// Called from the title bar's search field.
  void search(String q) {
    // The screen holds the last page of a long conversation; a search is about the
    // whole of it. Fetched once, the first time somebody actually searches.
    if (q.trim().isNotEmpty && _histMore) _wholeThread();
    setState(() {
      _query = q;
      _hit = 0;
    });
    _toHit();
  }

  void nextHit(int step) {
    final hits = _hits;
    if (hits.isEmpty) return;
    setState(() => _hit = (_hit + step) % hits.length);
    _toHit();
  }

  int get hitCount => _hits.length;
  int get hitIndex => _hits.isEmpty ? 0 : _hit + 1;

  /// Bring the current match into view.
  ///
  /// By a fraction of the list rather than a pixel offset: the bubbles are different
  /// heights and there is no cheap way to know where one starts, and landing near a
  /// match is enough when it is also highlighted.
  void _toHit() {
    final hits = _hits;
    if (hits.isEmpty || !_scroll.hasClients || _msgs.isEmpty) return;
    final at = hits[_hit.clamp(0, hits.length - 1)] / _msgs.length;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        (_scroll.position.maxScrollExtent * at).clamp(
            0.0, _scroll.position.maxScrollExtent),
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  /// What the selection toolbar offers for one message, beyond copy and select-all.
  ///
  /// Partial selection is the default and stays the default: a long press selects, and
  /// copying half an answer is the thing people actually do with an agent's reply.
  /// These are the message-level actions, in the menu that is already open.
  List<(String, VoidCallback)> _actionsFor(Map<String, dynamic> m, bool mine) {
    final text = (m['text'] ?? '').toString();
    return [
      (tr('Copiar todo'), () async {
        await Clipboard.setData(ClipboardData(text: text));
        _say(tr('Copiado'));
      }),
      (tr('Responder'), () => setState(() => _replyTo = m)),
      if (mine && _ready)
        (tr('Volver a enviar'), () {
          _input.text = text;
          _input.selection = TextSelection.collapsed(offset: text.length);
          setState(() {});
        }),
    ];
  }

  /// A message somebody asked to be taken to, from the gallery. Rings for a moment
  /// and then stops: a permanent mark would still be there next time, meaning
  /// nothing, and the point is to answer 'which one' at the instant of arriving.
  int _flash = -1;

  /// Take me to the message this file came with.
  ///
  /// By a fraction of the list, the same approximation the search arrows use and for
  /// the same reason: the bubbles are different heights and there is no cheap way to
  /// know where one starts. Landing near it is enough when it is also ringed.
  void jumpToMessage(int index) {
    if (index < 0 || index >= _msgs.length) return;
    setState(() => _flash = index);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final at = index / _msgs.length;
      _scroll.animateTo(
        (_scroll.position.maxScrollExtent * at)
            .clamp(0.0, _scroll.position.maxScrollExtent),
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    });
    Future.delayed(const Duration(milliseconds: 2200), () {
      if (mounted && _flash == index) setState(() => _flash = -1);
    });
  }

  bool _isFlashed(Map<String, dynamic> m) =>
      _flash >= 0 && _flash < _msgs.length && identical(_msgs[_flash], m);

  /// Is this the match the arrows are currently pointing at?
  bool _isCurrentHit(Map<String, dynamic> m) {
    final hits = _hits;
    if (hits.isEmpty) return false;
    final at = hits[_hit.clamp(0, hits.length - 1)];
    return at >= 0 && at < _msgs.length && identical(_msgs[at], m);
  }

  /// The messages currently on screen, for the media list.
  List<Map<String, dynamic>> get messages => List.unmodifiable(_msgs);

  /// A restored conversation is being fetched. Distinct from 'there is nothing here':
  /// showing the empty state during the fetch told me, convincingly, that persistence
  /// was broken when it was working - and it would tell the owner the same lie.
  bool _loadingHistory = false;

  bool _recording = false;

  /// Held, not ended. Somebody interrupted mid-sentence wants to carry on, not to
  /// start again and not to send the interruption.
  bool _paused = false;

  /// How long has actually been captured, which is not how long the button has been
  /// held: the clock stops while paused, because the gap does not appear in the note.
  Duration _captured = Duration.zero;

  /// A note was playing when this recording started, and is waiting to be let back.
  /// Only set when there WAS one, so nothing is 'resumed' that was never going.
  bool _duckedNote = false;
  DateTime? _recordingSince;
  Timer? _tick;
  final bool _transcribing = false;

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    Native.waitStop();
    _tick?.cancel();
    if (_recording) Native.recordCancel();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _say(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openLink(String url) async {
    try {
      await Native.openUrl(url);
    } on NativeError catch (e) {
      _say(e.message);
    }
  }

  // ── attachments ──────────────────────────────────────────────────────────────────

  Future<void> _pick(String mime) async {
    if (_attaching) return;
    setState(() => _attaching = true);
    try {
      final files = await Native.pick(mime: mime);
      for (final f in files) {
        if (_attached.length >= 10) {
          _say(tr('Diez archivos por mensaje es el máximo.'));
          break;
        }
        final res = await widget.link.reach('POST', '/api/talk/attach',
            body: _body({'name': f.name, 'data': base64Encode(f.bytes)}),
            wait: Link.thinkingWait);
        if (res['ok'] != true) {
          _say(tr((res['detail'] ?? trp('No pude subir {0}.', [f.name])).toString()));
          continue;
        }
        final rec = (res['file'] as Map).cast<String, dynamic>();
        setState(() => _attached.add(rec));
      }
    } on NativeError catch (e) {
      _say(e.message);
    } catch (e) {
      _say(humanError(e));
    } finally {
      if (mounted) setState(() => _attaching = false);
    }
  }

  void _attachMenu() {
    showModalBottomSheet<void>(
      context: context,
      builder: (sheet) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.image_outlined),
            title: Text(tr('Foto o imagen')),
            onTap: () {
              Navigator.pop(sheet);
              _pick('image/*');
            },
          ),
          ListTile(
            leading: const Icon(Icons.attach_file),
            title: Text(tr('Archivo')),
            onTap: () {
              Navigator.pop(sheet);
              _pick('*/*');
            },
          ),
        ]),
      ),
    );
  }

  // ── voice notes ──────────────────────────────────────────────────────────────────

  Future<void> _recordToggle() async {
    if (_transcribing) return;
    if (!_recording) {
      // The speaker first. A microphone and a speaker in the same room is feedback,
      // and the note would have whatever was playing underneath it. Paused rather
      // than stopped, so a two-minute note somebody was halfway through keeps its
      // place and can be let back afterwards.
      final ducked = await Native.playPause();
      try {
        await Native.recordStart();
      } on NativeError catch (e) {
        if (ducked) await Native.playResume();
        _say(e.message);
        return;
      }
      setState(() {
        _recording = true;
        _paused = false;
        _duckedNote = ducked;
        _captured = Duration.zero;
        _recordingSince = DateTime.now();
      });
      // Only to redraw the running time; the recording itself is Android's.
      _tick = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
      return;
    }
    _tick?.cancel();
    setState(() {
      _recording = false;
      _paused = false;
    });
    // Whatever was playing gets its turn back, whether the note is sent or not.
    if (_duckedNote) {
      _duckedNote = false;
      await Native.playResume();
    }
    PickedFile? note;
    try {
      note = await Native.recordStop();
    } on NativeError catch (e) {
      _say(e.message);
      return;
    }
    if (note == null) {
      _say(tr('Demasiado corto.'));
      return;
    }
    await _sendVoice(note);
  }

  /// A recording becomes a message in its own right.
  ///
  /// It appears in the conversation straight away as a note you can play back, the way
  /// it does in any messaging app - what you SENT was a recording, so that is what the
  /// conversation shows. The computer does the listening, and the words it heard are
  /// what the agent reads and what gets folded away under the note for checking.
  Future<void> _sendVoice(PickedFile note) async {
    if (_sending) return;
    final mine = <String, dynamic>{
      'role': 'user',
      'text': '',
      'voice': true,
      // Kept apart from the bubble's own `files`, which is what the computer said
      // about the note once it had filed it. These two are what a retry needs.
      'voiceName': note.name,
      'audioBytes': note.bytes,
      'needsVoice': true,
      'files': [
        {'name': note.name, 'kind': 'audio'}
      ],
      'pending': true,
    };
    setState(() => _msgs = [..._msgs, mine]);
    _toBottom();
    await _deliverVoice(mine);
  }

  /// A recording on its way up: first the words, then the message.
  ///
  /// Two requests, and either of them can fail. Split from _deliver so that sending
  /// again picks up at the step that failed - a note that was transcribed and then
  /// failed to send does not need listening to twice, and the owner should not be
  /// paying a second transcription for a lost wifi.
  Future<void> _deliverVoice(Map<String, dynamic> mine) async {
    if (mine['needsVoice'] != true) return _deliver(mine);
    if (_sending) return;
    final bytes = mine['audioBytes'] as Uint8List?;
    final name = (mine['voiceName'] ?? '').toString();
    if (bytes == null || name.isEmpty) return;
    setState(() {
      _sending = true;
      _error = '';
      mine['failed'] = false;
      mine['pending'] = true;
    });
    _toBottom();
    try {
      if (!_askedToNotify) {
        _askedToNotify = true;
        if (!await Native.canNotify()) await Native.askNotify();
      }
      await Native.waitStart(widget.agent.name);
      // One call does both: the recording is kept in the agent's folder AND read out
      // into words. Keeping it is the point of showing a transcript at all - a name
      // Whisper got wrong can still be checked against what was actually said.
      //
      // Sending this one twice can leave two copies of the recording in the agent's
      // folder, and that is the right side to err on: a duplicate file costs disk,
      // and the alternative costs the owner the only copy of what they said.
      final heard = await widget.link.reach('POST', '/api/talk/voice',
          body: _body({'name': name, 'data': base64Encode(bytes)}),
          wait: Link.thinkingWait);
      if (heard['ok'] != true) {
        throw LinkError(
            tr((heard['detail'] ?? tr('No pude transcribir la nota.')).toString()));
      }
      final text = (heard['text'] ?? '').toString().trim();
      if (text.isEmpty) throw LinkError(tr('No entendí nada en esa nota.'));
      final rec = (heard['file'] as Map?)?.cast<String, dynamic>() ??
          <String, dynamic>{'name': name, 'kind': 'audio'};
      setState(() {
        mine['text'] = text;
        // The quote is consumed here, where the words first exist - not on arrival of
        // the reply, which is after the owner may have swiped at something else.
        mine['sendText'] = _withQuote(text);
        mine['sendFiles'] = rec['id'] != null ? [rec['id']] : <dynamic>[];
        mine['files'] = [rec];
        mine['pending'] = false;
        mine['needsVoice'] = false;
        _replyTo = null;
      });
    } catch (e) {
      await Native.waitStop();
      _queue(mine);
      mine['pending'] = false;
      mine['failed'] = true;
      if (mounted) {
        setState(() {
          _error = humanError(e);
          _sending = false;
        });
      }
      return;
    }
    // The service stays up across the two requests rather than stopping between them:
    // the gap is where Android would be free to freeze the app, and the wait is one
    // wait as far as the owner is concerned.
    if (!mounted) {
      await Native.waitStop();
      return;
    }
    setState(() => _sending = false);
    await _deliver(mine);
  }

  Future<void> _recordCancel() async {
    _tick?.cancel();
    await Native.recordCancel();
    // Whatever was playing when this started gets its turn back - a discarded note
    // is no reason to leave somebody's music stopped.
    if (_duckedNote) {
      _duckedNote = false;
      await Native.playResume();
    }
    if (mounted) {
      setState(() {
        _recording = false;
        _paused = false;
        _captured = Duration.zero;
      });
    }
  }

  /// Start a clean thread with this agent.
  ///
  /// No session is created here: an empty conversation nobody held is litter in the
  /// list. The next thing said makes one.
  void newConversation() {
    setState(() {
      _session = '';
      _msgs = [];
      _histStart = 0;
      _histMore = false;
      // Nothing to fetch: this one is empty because it is new, not because it is
      // still coming.
      _histLoaded = true;
      _replyTo = null;
      _attached.clear();
      _error = '';
    });
    _openThreads.remove(widget.agent.slug);
    Store.saveThreads(_openThreads);
  }

  // ── past conversations ───────────────────────────────────────────────────────────

  /// Offer the conversations this agent has already had, and open the chosen one.
  ///
  /// A new thread is still what you get by default - an agent's memory is not the
  /// transcript, and starting clean is what the desktop console does. This is for the
  /// other case: picking up something from yesterday, or reading back a thread that
  /// happened on Telegram, which appears here because it is the same agent.
  /// Offer the conversations this agent has already had, and open the chosen one.
  ///
  /// A new thread is still what you get by default - an agent's memory is not the
  /// transcript, and starting clean is what the desktop console does. This is for the
  /// other case: picking up something from yesterday, or reading back a thread that
  /// happened on Telegram, which appears here because it is the same agent.
  ///
  /// The sheet itself lives in conversations.dart: it pages, and it searches inside
  /// the conversations rather than at their names.
  Future<void> pickPastConversation() async {
    final chosen = await pickConversation(context, link: widget.link, body: _body);
    if (chosen == null || !mounted) return;
    if (chosen['new'] == true) {
      newConversation();
      return;
    }
    final id = (chosen['id'] ?? '').toString();
    if (id.isEmpty) return;
    // In the SAME frame the old conversation is cleared: there was an await between
    // the two before this - saving which thread is open - and one frame of an empty
    // chat is exactly the flash that reads as a crash.
    setState(() {
      _session = id;
      _msgs = [];
      _histStart = 0;
      _histMore = false;
      _histLoaded = false;
      _loadingHistory = true;
      _replyTo = null;
      _error = '';
    });
    _openThreads[widget.agent.slug] = id;
    await Store.saveThreads(_openThreads);
    try {
      await _history();
    } finally {
      if (mounted) setState(() => _loadingHistory = false);
    }
  }

  /// The message being answered, above the box, with a way out.
  Widget _replyStrip() {
    final r = _replyTo!;
    final who = r['role'] == 'user' ? 'Tú' : widget.agent.name;
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
      color: context.c.panel2,
      child: Row(children: [
        Container(width: 3, height: 32, color: context.c.accent),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(who,
                  style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      color: context.c.accent)),
              Text((r['text'] ?? '').toString().replaceAll('\n', ' '),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, color: context.c.muted)),
            ],
          ),
        ),
        IconButton(
          icon: const Icon(Icons.close, size: 18),
          onPressed: () => setState(() => _replyTo = null),
        ),
      ]),
    );
  }

  /// What is going with the next message.
  Widget _attachedStrip() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 2),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          for (final f in _attached)
            Chip(
              avatar: Icon(
                  (f['kind'] == 'image')
                      ? Icons.image_outlined
                      : (f['kind'] == 'audio')
                          ? Icons.mic_none
                          : Icons.insert_drive_file_outlined,
                  size: 16),
              label: Text((f['name'] ?? '').toString(),
                  overflow: TextOverflow.ellipsis),
              onDeleted: () => setState(() => _attached.remove(f)),
            ),
        ],
      ),
    );
  }

  Widget _composerRow() {
    final hasText = _input.text.trim().isNotEmpty;
    return Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
      IconButton(
        onPressed: (_ready && !_attaching && !_sending) ? _attachMenu : null,
        icon: _attaching
            ? const SizedBox(
                width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.add),
        tooltip: tr('Adjuntar'),
      ),
      Expanded(
        child: TextField(
          controller: _input,
          minLines: 1,
          maxLines: 4,
          // A box you can type into is a promise. Without a conversation open there is
          // nowhere for the words to go, so it does not make one.
          enabled: _ready && !_transcribing,
          textInputAction: TextInputAction.newline,
          decoration: InputDecoration(
            hintText: _transcribing
                ? tr('Transcribiendo…')
                : _ready
                    ? tr('Escribe…')
                    : tr('No disponible'),
            isDense: true,
          ),
          // Redrawn on every keystroke so the mic turns into a send button the moment
          // there is something to send.
          onChanged: (_) => setState(() {}),
        ),
      ),
      const SizedBox(width: 6),
      if (hasText || _attached.isNotEmpty)
        IconButton.filled(
          onPressed: (_sending || !_ready) ? null : _send,
          icon: const Icon(Icons.send),
        )
      else
        IconButton.filled(
          onPressed: (_ready && !_transcribing) ? _recordToggle : null,
          icon: _transcribing
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.mic),
          tooltip: tr('Nota de voz'),
        ),
    ]);
  }

  /// While a note is being recorded the composer becomes the recorder: a way out on the
  /// left, how long it has been running in the middle, and the button that ends it.
  /// The time actually captured so far: frozen while paused, running otherwise.
  Duration get _recordedSoFar => _paused
      ? _captured
      : _captured + DateTime.now().difference(_recordingSince ?? DateTime.now());

  Future<void> _pauseToggle() async {
    if (!_recording) return;
    if (_paused) {
      if (!await Native.recordResume()) {
        _say(tr('Este teléfono no sabe pausar la grabación.'));
        return;
      }
      setState(() {
        _paused = false;
        _recordingSince = DateTime.now();
      });
      return;
    }
    if (!await Native.recordPause()) {
      _say(tr('Este teléfono no sabe pausar la grabación.'));
      return;
    }
    setState(() {
      // Banked, so the clock carries on from here rather than from zero.
      _captured = _recordedSoFar;
      _paused = true;
    });
  }

  Widget _recordingRow() {
    final secs = _recordedSoFar.inSeconds;
    final mm = (secs ~/ 60).toString();
    final ss = (secs % 60).toString().padLeft(2, '0');
    return Row(children: [
      IconButton(
        onPressed: _recordCancel,
        icon: Icon(Icons.delete_outline, color: context.c.err),
        tooltip: tr('Descartar'),
      ),
      IconButton(
        onPressed: _pauseToggle,
        // Play, not a record dot: the question this button answers while paused is
        // 'carry on?', and a red dot is what you press to START something.
        icon: Icon(_paused ? Icons.play_arrow : Icons.pause, color: context.c.ink),
        tooltip: _paused ? tr('Seguir grabando') : tr('Pausa'),
      ),
      Expanded(
        child: Row(children: [
          // The red dot belongs to recording, not to paused: a blinking light over a
          // stopped microphone is the app telling you it is listening when it is not.
          Icon(_paused ? Icons.pause_circle_outline : Icons.fiber_manual_record,
              size: _paused ? 14 : 12,
              color: _paused ? context.c.muted : context.c.err),
          const SizedBox(width: 8),
          Flexible(
            child: Text('${_paused ? tr('En pausa') : tr('Grabando')}  $mm:$ss',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    color: context.c.ink,
                    fontWeight: FontWeight.w600,
                    fontFeatures: const [FontFeature.tabularFigures()])),
          ),
        ]),
      ),
      // An arrow, not a stop. Stop is what you press when you want the recording to
      // end; this ends it AND sends it, and every messaging app draws that as send.
      IconButton.filled(
        onPressed: _recordToggle,
        icon: const Icon(Icons.arrow_forward),
        tooltip: tr('Enviar'),
      ),
    ]);
  }

  /// Turn it on from here, which this phone may do only if it was given `config`
  /// when it was paired. Without that the server would refuse - so rather than
  /// offer a button that cannot work, the screen says where the switch is instead.
  Future<void> _turnTalkOn() async {
    setState(() => _turningOn = true);
    try {
      final res = await widget.link.reach('POST', '/api/talk/access',
          body: _body({'where': 'phone', 'on': true}), wait: Link.thinkingWait);
      if (res['ok'] != true) {
        setState(() => _error = tr((res['detail'] ?? '').toString()));
        return;
      }
      setState(() => _talkOff = false);
      await _start();
    } catch (e) {
      if (mounted) setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _turningOn = false);
    }
  }

  Widget _talkOffScreen(BuildContext context) {
    final mayTurnOn = widget.link.pairing.caps.contains('config');
    return Expanded(
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.chat_bubble_outline, size: 40, color: context.c.muted),
              const SizedBox(height: 14),
              Text(
                tr('Este agente todavía no está puesto para hablar desde aquí.'),
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 15, color: context.c.ink),
              ),
              const SizedBox(height: 8),
              Text(
                mayTurnOn
                    ? tr('Se enciende una sola vez y queda encendido.')
                    : tr('Enciéndelo en la computadora: elige este agente y abre '
                        '«Háblale desde aquí».'),
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: context.c.muted),
              ),
              if (mayTurnOn) ...[
                const SizedBox(height: 18),
                FilledButton(
                  onPressed: _turningOn ? null : _turnTalkOn,
                  child: Text(_turningOn ? tr('Encendiendo…') : tr('Encender')),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// What this screen is showing right now. See chatViewFor.
  ChatView get _view => chatViewFor(
        talkOff: _talkOff,
        connecting: _connecting,
        loadingHistory: _loadingHistory,
        ready: _ready,
        hasSession: _session.isNotEmpty,
        loaded: _histLoaded,
        hasMessages: _msgs.isNotEmpty,
        hasError: _error.isNotEmpty,
      );

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      if (_error.isNotEmpty)
        Container(
          width: double.infinity,
          color: context.c.warnBg,
          padding: const EdgeInsets.all(10),
          child: Text(_error,
              style: TextStyle(fontSize: 12, color: context.c.ink)),
        ),
      if (_view == ChatView.off)
        _talkOffScreen(context)
      else if (_view == ChatView.connecting)
        const Expanded(
          child: Waiting(lines: [
            'Conectando con el agente…',
            'Viendo dónde os quedasteis…',
            'Sigue en ello. Fuera de casa tarda un poco más.',
          ]),
        )
      else if (_view == ChatView.loading)
        const Expanded(
          child: Waiting(lines: [
            'Abriendo la conversación…',
            'Trayendo lo que os dijisteis…',
          ]),
        )
      else if (_view != ChatView.messages)
        Expanded(
          child: Column(children: [
            if (_resume.isNotEmpty) _resumeStrip(),
            Expanded(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Text(
                  // One tr() per sentence, never tr() around a choice between two:
                  // the check that every sentence has English and German reads call
                  // sites, and a ternary inside the brackets hides BOTH from it.
                  _view == ChatView.silent
                      ? tr('Esta conversación no tiene nada que leer.')
                      : tr('Todavía no os habéis dicho nada.'),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: context.c.muted)),
            ),
          )),
          ]),
        )
      else
      Expanded(
        child: Column(children: [
          if (_resume.isNotEmpty) _resumeStrip(),
          Expanded(
        child: ListView.builder(
          controller: _scroll,
          padding: const EdgeInsets.all(14),
          // One row above the first message while there is more conversation behind
          // it. Building that row IS the request: reaching the top of what you have
          // is the moment you want what came before, and a button to press first
          // would only be in the way.
          itemCount: _msgs.length + (_histMore ? 1 : 0),
          itemBuilder: (c, i) {
            if (_histMore && i == 0) return _earlierRow();
            final m = _msgs[_histMore ? i - 1 : i];
            final mine = m['role'] == 'user';
            if (m['pending'] == true && m['voice'] != true) return _bubble(c, m, mine);
            // Telegram's gesture, because it is the one thumbs already know: drag the
            // message a little way and let go. A threshold rather than any movement at
            // all, so scrolling a long conversation does not keep arming a reply.
            return _SwipeToReply(
              onReply: () {
                setState(() => _replyTo = m);
                HapticFeedback.selectionClick();
              },
              child: _bubble(c, m, mine),
            );
          },
        ),
      )]),
      ),
      if (_replyTo != null && !_talkOff && !_connecting) _replyStrip(),
      if (_attached.isNotEmpty && !_talkOff) _attachedStrip(),
      // No box to type in while there is nothing listening: a composer under a
      // screen that says it is switched off is an invitation to lose a message.
      if (!_talkOff && !_connecting)
      SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
          child: _recording ? _recordingRow() : _composerRow(),
        ),
      ),
    ]);
  }

  /// The strip above a reply: who was answered and what they said. Tapping it goes
  /// there, which is the whole reason anybody taps a quote.
  Widget _replyHead(BuildContext c, String who, String quoted,
      Map<String, dynamic> m, Color ink, bool mine) {
    final tint = ink.withValues(alpha: mine ? 0.22 : 0.08);
    return InkWell(
      onTap: () {
        final at = _findQuoted(quoted, _msgs.indexOf(m));
        if (at >= 0) jumpToMessage(at);
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 6),
        padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
        decoration: BoxDecoration(
          color: tint,
          borderRadius: BorderRadius.circular(8),
          border: Border(
            left: BorderSide(color: ink.withValues(alpha: 0.55), width: 3),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(who,
                style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: ink.withValues(alpha: 0.9))),
            Text(quoted,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontSize: 11, color: ink.withValues(alpha: 0.75))),
          ],
        ),
      ),
    );
  }

  Widget _bubble(BuildContext c, Map<String, dynamic> m, bool mine) {
            return Align(
              alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
              child: Container(
                margin: const EdgeInsets.symmetric(vertical: 4),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(c).size.width * 0.78),
                decoration: BoxDecoration(
                  color: mine ? context.c.accent : context.c.panel,
                  borderRadius: BorderRadius.circular(16),
                  // The match being looked at gets a ring. Without it, scrolling to a
                  // result lands you in a wall of bubbles with no idea which one it is.
                  // So does a message that never left: the one other bubble on this
                  // screen that must not read as an ordinary, delivered one.
                  border: (_isCurrentHit(m) ||
                          _isFlashed(m) ||
                          m['failed'] == true)
                      ? Border.all(color: context.c.warn, width: 2)
                      : mine
                          ? null
                          : Border.all(color: context.c.line),
                ),
                child: Builder(builder: (bc) {
                  final ink = mine ? context.c.accentInk : context.c.ink;
                  // A message that never left says so underneath itself, next to the
                  // button that sends it again. Wrapped round the ordinary body
                  // rather than replacing it: what failed is still a message, still
                  // the one the owner wrote, and still worth reading back.
                  if (m['failed'] == true) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [_bubbleBody(bc, m, mine, ink), _failedRow(m, ink)],
                    );
                  }
                  return _bubbleBody(bc, m, mine, ink);
                }),
              ),
            );
  }

  /// What is drawn inside a bubble: the words, the quote above them when it is a
  /// reply, the files under them - or the single widget that is all three at once,
  /// which is what a voice note is.
  Widget _bubbleBody(
      BuildContext bc, Map<String, dynamic> m, bool mine, Color ink) {
                  // A voice note on its way up is pending too, but it is a note, not an
                  // agent thinking - it has a recording to show and a spinner of its own.
                  if (m['pending'] == true && m['voice'] != true) {
                    return Text(tr('escribiendo…'),
                        style: TextStyle(color: ink, fontStyle: FontStyle.italic));
                  }
                  final body = (m['text'] ?? '').toString();
                  // Only what the agent wrote is markdown. What the owner typed is
                  // shown exactly as typed: an asterisk in their own message is an
                  // asterisk, and their text should never be reinterpreted back at them.
                  // Each file as the computer described it: name, kind, size, and an id
                  // when it is small enough to fetch. An image fetches itself and shows;
                  // anything else is a row you can tap to save.
                  final files = [
                    for (final f in (m['files'] as List? ?? []))
                      if (f is Map)
                        f.cast<String, dynamic>()
                      else
                        <String, dynamic>{'name': f.toString()},
                  ];
                  // A message carrying exactly one recording IS a voice note: the
                  // recording on top, and the words the computer heard folded away
                  // underneath where they can be checked. That shape covers a note
                  // just recorded and one read back out of a transcript months later,
                  // because by then it is the same thing - a file and the text that
                  // was sent with it.
                  final audio = files.where((f) => f['kind'] == 'audio').toList();
                  if (audio.length == 1 && files.length == 1) {
                    return VoiceNote(
                      link: widget.link,
                      file: audio.first,
                      body: _body,
                      ink: ink,
                      accent: context.c.accent,
                      transcript: body,
                      localBytes: m['audioBytes'] as Uint8List?,
                      pending: m['pending'] == true,
                    );
                  }
                  // A leading quote is a reply, from either side, and is lifted out
                  // of the text so the bubble shows what was actually said rather
                  // than a block of markdown with the answer buried under it.
                  final reply = _asReply(body);
                  final rest = reply?.$3 ?? body;
                  final head = reply == null
                      ? null
                      : _replyHead(bc, reply.$1, reply.$2, m, ink, mine);
                  final body2 = mine
                      ? SelectableText(rest,
                          style: TextStyle(color: ink),
                          contextMenuBuilder: menuFor(_actionsFor(m, mine)))
                      : Markdown(rest,
                          baseStyle: TextStyle(color: ink, height: 1.35),
                          onLink: _openLink,
                          highlight: _query.trim(),
                          actions: _actionsFor(m, mine));
                  if (files.isEmpty && head == null) return body2;
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (head != null) head,
                      if (rest.isNotEmpty) body2,
                      for (final f in files)
                        Attachment(
                          link: widget.link,
                          file: f,
                          body: _body,
                          ink: ink,
                        ),
                    ],
                  );
  }
}

class RoutinesTab extends StatefulWidget {
  const RoutinesTab({super.key, required this.link, required this.agent});
  final Link link;
  final Agent agent;

  @override
  State<RoutinesTab> createState() => _RoutinesTabState();
}

/// The jobs for one agent, whichever shape the computer answered in.
///
/// A computer that has not updated yet still replies with the machine-wide
/// {agents: [...]}, and the phone used to read `jobs` off the top of that, find
/// nothing, and draw an empty routines tab - no error, just nothing, on a machine
/// with seventeen of them. So it looks in both places rather than trusting one.
List _jobsIn(Map<String, dynamic> res, String slug) {
  final direct = res['jobs'] ?? res['rows'];
  if (direct is List) return direct;
  final agents = res['agents'];
  if (agents is List) {
    for (final a in agents) {
      if (a is Map && ((a['profile'] ?? a['slug']) == slug)) {
        final jobs = a['jobs'];
        if (jobs is List) return jobs;
      }
    }
  }
  return const [];
}

class _RoutinesTabState extends State<RoutinesTab> {
  List<Map<String, dynamic>> _rows = [];
  String _error = '';
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      // Always named, including 'default'. Leaving it out for the main agent meant
      // the computer could not tell a phone asking about one agent from the console
      // asking about the machine.
      final res = await widget.link.reach('POST', '/api/cron/list',
          body: {'profile': widget.agent.slug});
      setState(() {
        _rows = _jobsIn(res, widget.agent.slug)
            .map((r) => (r as Map).cast<String, dynamic>())
            .toList();
        _loading = false;
        _error = res['ok'] == false ? tr((res['detail'] ?? '').toString()) : '';
      });
    } catch (e) {
      setState(() {
        _error = humanError(e);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error.isNotEmpty) {
      return Center(
          child: Padding(padding: const EdgeInsets.all(24), child: Text(_error)));
    }
    if (_rows.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(tr('Este agente no hace nada por su cuenta todavía.'),
              textAlign: TextAlign.center),
        ),
      );
    }
    return ListView.separated(
      itemCount: _rows.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (c, i) {
        final r = _rows[i];
        return SwitchListTile(
          title: Text((r['name'] ?? r['id'] ?? '?').toString()),
          subtitle: Text((r['schedule_text'] ?? r['schedule'] ?? '').toString()),
          value: r['enabled'] != false,
          onChanged: (v) async {
            // Taken BEFORE the await, not after: reaching for the messenger through a
            // BuildContext once the request has come back is a use of a context that may
            // no longer be in the tree. The switch flips straight away and flips back if
            // the computer refuses, so the row never lies about what the agent is doing.
            final messenger = ScaffoldMessenger.of(context);
            setState(() => r['enabled'] = v);
            final res = await widget.link.reach('POST', '/api/cron/flags', body: {
              // Same again: set_flags opens THIS agent's jobs.json, and a slug here
              // would have switched a routine off on the default agent instead.
              if (!widget.agent.isDefault) 'profile': widget.agent.slug,
              'id': r['id'],
              'enabled': v,
            });
            if (!mounted) return;
            if (res['ok'] != true) {
              setState(() => r['enabled'] = !v);
              messenger.showSnackBar(SnackBar(
                  content: Text(tr((res['detail'] ?? tr('No se pudo')).toString()))));
            }
          },
        );
      },
    );
  }
}

// ── the browser ───────────────────────────────────────────────────────────────────────

class BrowserTab extends StatefulWidget {
  const BrowserTab({super.key, required this.link, required this.agent});
  final Link link;
  final Agent agent;

  @override
  State<BrowserTab> createState() => _BrowserTabState();
}

class _BrowserTabState extends State<BrowserTab> {
  BrowserStream? _stream;
  Uint8List? _frame;
  Map<String, dynamic> _state = {};
  String _error = '';
  bool _armed = false;
  final _url = TextEditingController();
  final _keys = FocusNode();

  /// Getting closer to the page.
  ///
  /// A 1440-pixel desktop window drawn across a phone makes most text too small to
  /// read and most links too small to hit - and the agent's browser is a DESKTOP
  /// browser, so there is no mobile layout coming to rescue it. This zooms the
  /// picture rather than the page: nothing is asked of the computer, and what is on
  /// screen stays exactly what the agent is looking at.
  final TransformationController _view = TransformationController();
  double _zoom = 1;

  /// The size the picture is being drawn at, so zooming can keep the middle of the
  /// screen still. Set by the LayoutBuilder below rather than measured here.
  Size _box = Size.zero;

  static const double _zoomMin = 1, _zoomMax = 5;

  void _setZoom(double want, [Offset? around]) {
    final next = want.clamp(_zoomMin, _zoomMax);
    setState(() {
      _zoom = next;
      if (next <= 1) {
        _view.value = Matrix4.identity();
      } else {
        // Around the middle of what is on screen, because that is where somebody
        // peering at something small is already looking. Zooming from the corner -
        // which is what scaling the identity does - throws the thing they were
        // reading off the screen and makes the button feel like it did the wrong
        // thing.
        final at = around ??
            (_box == Size.zero
                ? Offset.zero
                : Offset(_box.width / 2, _box.height / 2));
        final now = _view.value;
        final was = now.getMaxScaleOnAxis();
        final shift = Offset(now.storage[12], now.storage[13]);
        final scene = (at - shift) / was;      // the picture point under that spot
        final keep = at - scene * next;        // put it back where it was
        _view.value = Matrix4.identity()
          ..translateByDouble(keep.dx, keep.dy, 0, 1)
          ..scaleByDouble(next, next, next, 1);
      }
    });
  }

  @override
  void initState() {
    super.initState();
    _open();
    super.initState();
    // Pinching changes the scale without going through _setZoom, and the one-finger
    // rule below depends on knowing the current one.
    _view.addListener(() {
      final now = _view.value.getMaxScaleOnAxis();
      if ((now - _zoom).abs() > 0.01 && mounted) setState(() => _zoom = now);
    });
  }

  @override
  void dispose() {
    _stream?.close();
    _view.dispose();
    _keys.dispose();
    super.dispose();
  }

  Future<void> _open() async {
    try {
      final s = await widget.link.watchBrowser(widget.agent.slug);
      s.frames.listen((f) {
        if (mounted) setState(() => _frame = f);
      });
      s.events.listen((e) {
        if (!mounted) return;
        if (e['t'] == 'state') {
          setState(() {
            _state = e;
            _armed = e['armed'] == true;
          });
        } else if (e['t'] == 'error') {
          setState(() => _error = tr((e['detail'] ?? '').toString()));
        } else if (e['t'] == 'closed') {
          setState(() => _error = _error.isEmpty ? 'La sesión se cerró.' : _error);
        }
      });
      setState(() => _stream = s);
    } catch (e) {
      setState(() => _error = humanError(e));
    }
  }

  void _tapAt(Offset local, Size box) {
    final s = _stream;
    if (s == null || !_armed) return;
    s.tap(local.dx / box.width, local.dy / box.height);
  }

  @override
  Widget build(BuildContext context) {
    if (_error.isNotEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            FilledButton(
              onPressed: () {
                setState(() {
                  _error = '';
                  _frame = null;
                });
                _open();
              },
              child: Text(tr('Reintentar')),
            ),
          ]),
        ),
      );
    }
    final frame = _frame;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
        child: Row(children: [
          Expanded(
            child: TextField(
              controller: _url,
              enabled: _armed,
              decoration: InputDecoration(
                hintText: _armed ? tr('Ir a…') : tr('Toma el control para navegar'),
                border: const OutlineInputBorder(),
                isDense: true,
              ),
              onSubmitted: (v) => _stream?.navigate(v),
            ),
          ),
          // Buttons as well as pinch: pinching a live video of somebody else's
          // browser is not an obvious thing to try, and these say it can be done.
          IconButton(
            tooltip: tr('Alejar'),
            onPressed: _zoom <= 1.01 ? null : () => _setZoom(_zoom - 0.5),
            icon: const Icon(Icons.zoom_out),
          ),
          IconButton(
            tooltip: tr('Acercar'),
            onPressed: _zoom >= _zoomMax ? null : () => _setZoom(_zoom + 0.5),
            icon: const Icon(Icons.zoom_in),
          ),
          IconButton(
            tooltip: 'Actualizar la imagen',
            onPressed: () => _stream?.refresh(),
            icon: const Icon(Icons.refresh),
          ),
        ]),
      ),
      Expanded(
        child: Container(
          color: Colors.black,   // the browser frame sits on black on purpose
          child: frame == null
              ? Center(
                  child: Text(tr('Esperando la primera imagen…'),
                      style: const TextStyle(color: Colors.white70)))
              : LayoutBuilder(builder: (c, box) {
                  _box = Size(box.maxWidth, box.maxHeight);
                  // The gesture detector is INSIDE, so its coordinates are the
                  // picture's own however far it has been zoomed or panned - the tap
                  // that reaches the computer needs no correcting.
                  return InteractiveViewer(
                    transformationController: _view,
                    minScale: _zoomMin,
                    maxScale: _zoomMax,
                    // One finger drags the PAGE at 1x and the VIEW once zoomed in.
                    // Both are what you would expect at the time, and the two cannot
                    // both have one finger.
                    panEnabled: _zoom > 1.01,
                    child: GestureDetector(
                    onTapDown: (d) =>
                        _tapAt(d.localPosition, Size(box.maxWidth, box.maxHeight)),
                    onVerticalDragUpdate: (d) {
                      if (!_armed || _zoom > 1.01) return;
                      _stream?.scroll(
                        d.localPosition.dx / box.maxWidth,
                        d.localPosition.dy / box.maxHeight,
                        -d.delta.dy * 2,
                      );
                    },
                    child: Center(
                      child: Image.memory(
                        frame,
                        gaplessPlayback: true,
                        fit: BoxFit.contain,
                        width: box.maxWidth,
                      ),
                    ),
                    ),
                  );
                }),
        ),
      ),
      if (_state['visible'] == false)
        Container(
          width: double.infinity,
          color: context.c.warnBg,
          padding: const EdgeInsets.all(8),
          child: const Text(
            'La ventana está tapada en la computadora, así que puede no refrescarse.',
            style: TextStyle(fontSize: 12),
          ),
        ),
      SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(10, 6, 10, 8),
          child: Row(children: [
            // Control is taken on purpose and given back on its own: that window is
            // signed into the owner's accounts, so a session that merely CAN click is a
            // session that eventually clicks something nobody meant.
            Expanded(
              child: FilledButton.icon(
                style: _armed
                    // The dark palette's warn is a LIGHT amber and the light one's is a
                    // dark ochre, so neither black nor white is right for both.
                    ? FilledButton.styleFrom(
                        backgroundColor: context.c.warn,
                        foregroundColor: inkOn(context.c.warn))
                    : null,
                onPressed: () {
                  if (_armed) {
                    _stream?.disarm();
                  } else {
                    _stream?.arm();
                  }
                },
                icon: Icon(_armed ? Icons.lock_open : Icons.lock),
                label: Text(_armed
                    ? trp('Soltar el control ({0}s)', [_state['armed_for'] ?? 0])
                    : tr('Tomar el control')),
              ),
            ),
            if (_armed) ...[
              const SizedBox(width: 8),
              IconButton.outlined(
                onPressed: () => _stream?.key('Enter'),
                icon: const Icon(Icons.keyboard_return),
              ),
              IconButton.outlined(
                onPressed: () async {
                  final text = await showDialog<String>(
                    context: context,
                    builder: (c) {
                      final t = TextEditingController();
                      return AlertDialog(
                        title: Text(tr('Escribir en la página')),
                        content: TextField(controller: t, autofocus: true),
                        actions: [
                          TextButton(
                              onPressed: () => Navigator.pop(c),
                              child: Text(tr('Cancelar'))),
                          FilledButton(
                              onPressed: () => Navigator.pop(c, t.text),
                              child: Text(tr('Escribir'))),
                        ],
                      );
                    },
                  );
                  if (text != null && text.isNotEmpty) _stream?.type(text);
                },
                icon: const Icon(Icons.keyboard),
              ),
            ],
          ]),
        ),
      ),
    ]);
  }
}

/// Drag a message sideways to answer it.
///
/// Telegram's gesture, because it is the one thumbs already know. Two details are what
/// make it feel right rather than twitchy: the bubble follows the finger only part of
/// the way (so it reads as resistance, not as a thing coming loose), and it only arms
/// past a threshold, so scrolling a long conversation does not keep catching it.
class _SwipeToReply extends StatefulWidget {
  const _SwipeToReply({required this.child, required this.onReply});

  final Widget child;
  final VoidCallback onReply;

  @override
  State<_SwipeToReply> createState() => _SwipeToReplyState();
}

class _SwipeToReplyState extends State<_SwipeToReply> {
  static const _arm = 56.0;     // far enough to be meant
  static const _drag = 0.4;     // how much of the finger the bubble follows

  double _dx = 0;
  bool _armed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      // Horizontal only: the vertical drag belongs to the list, and taking it would
      // make the conversation impossible to scroll.
      onHorizontalDragUpdate: (d) {
        setState(() {
          _dx = (_dx + d.delta.dx * _drag).clamp(0.0, _arm);
          if (!_armed && _dx >= _arm) {
            _armed = true;
            HapticFeedback.selectionClick();
          }
        });
      },
      onHorizontalDragEnd: (_) {
        if (_armed) widget.onReply();
        setState(() {
          _dx = 0;
          _armed = false;
        });
      },
      onHorizontalDragCancel: () => setState(() {
        _dx = 0;
        _armed = false;
      }),
      child: Stack(children: [
        // The arrow fades in as the bubble moves, so the gesture explains itself the
        // first time somebody does it by accident.
        Positioned.fill(
          child: Align(
            alignment: Alignment.centerLeft,
            child: Opacity(
              opacity: (_dx / _arm).clamp(0.0, 1.0),
              child: Padding(
                padding: const EdgeInsets.only(left: 6),
                child: Icon(Icons.reply,
                    size: 18,
                    color: _armed ? context.c.accent : context.c.muted),
              ),
            ),
          ),
        ),
        Transform.translate(offset: Offset(_dx, 0), child: widget.child),
      ]),
    );
  }
}
