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
import 'link.dart';
import 'sha256.dart';
import 'markdown.dart';
import 'native.dart';
import 'theme.dart';
import 'voice.dart';
import 'strings.dart';

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
      if (cert == null) throw LinkError('No pude ver el certificado del computadora.');
      final der = Uint8List.fromList(cert.der);
      final words = shortCode(hex(sha256(der)));
      if (!mounted) return;
      final agreed = await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
              title: Text(tr('¿Dice esto tu computadora?')),
              content: Column(mainAxisSize: MainAxisSize.min, children: [
                const Text('En la pantalla del computadora, debajo del código, hay dos palabras. Tienen que ser exactamente estas:'),
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
          _status = (res['detail'] ?? 'No se pudo enlazar.').toString();
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
        const Text('En el computadora, abre Olivaw y ve a «Tu teléfono» → «Enlazar un teléfono». Te dará una dirección y un código.'),
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
            labelText: 'Dirección del computadora',
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
  Agent(this.slug, this.name, this.running, this.look, this.isDefault);
  final String slug;
  final String name;
  final bool? running;
  final Map<String, dynamic> look;
  final bool isDefault;

  static Agent from(Map<String, dynamic> row) => Agent(
        (row['slug'] ?? 'default').toString(),
        (row['name'] ?? '?').toString(),
        row['gateway_running'] as bool?,
        (row['look'] as Map?)?.cast<String, dynamic>() ?? const {},
        row['is_default'] == true,
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
          : 'sin saber';
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

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _link.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() => _loading = true);
    try {
      final res = await _link.reach('POST', '/api/agents/list');
      if (res['ok'] != true) throw LinkError((res['detail'] ?? tr('Sin respuesta')).toString());
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
        _error = humanError(e);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Tus agentes')),
        actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: RefreshIndicator(
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
                ? const Center(child: CircularProgressIndicator())
                : ListView.separated(
                    itemCount: _agents.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (c, i) {
                      final a = _agents[i];
                      return ListTile(
                        leading: Face(agent: a),
                        title: Text(a.name,
                            style: const TextStyle(fontWeight: FontWeight.w600)),
                        subtitle: Text(a.state),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.push(
                          c,
                          MaterialPageRoute(
                            builder: (_) =>
                                AgentScreen(link: _link, agent: a, pairing: widget.pairing),
                          ),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}

class Face extends StatelessWidget {
  const Face({super.key, required this.agent, this.size = 42});
  final Agent agent;
  final double size;

  @override
  Widget build(BuildContext context) {
    // The picture itself is not fetched: that route is a GET on the desktop console's
    // own token. The colour and the initials travel with the roster, and they are what
    // make the face here match the colour that agent's Chrome window wears.
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

  /// Held so the app bar's history button can reach into the open conversation.
  final GlobalKey<ChatTabState> _chat = GlobalKey<ChatTabState>();

  @override
  Widget build(BuildContext context) {
    final canBrowse = widget.pairing.caps.contains('navegador');
    final tabs = <Widget>[
      ChatTab(key: _chat, link: widget.link, agent: widget.agent),
      RoutinesTab(link: widget.link, agent: widget.agent),
      if (canBrowse) BrowserTab(link: widget.link, agent: widget.agent),
    ];
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Row(children: [
          Face(agent: widget.agent, size: 30),
          const SizedBox(width: 10),
          Expanded(
              child: Text(widget.agent.name, overflow: TextOverflow.ellipsis)),
        ]),
        actions: [
          // Only over the conversation: on the routines or the browser it would be a
          // button about a screen you are not looking at.
          if (_tab == 0) ...[
            IconButton(
              icon: const Icon(Icons.edit_square),
              tooltip: tr('Conversación nueva'),
              onPressed: () => _chat.currentState?.newConversation(),
            ),
            IconButton(
              icon: const Icon(Icons.history),
              tooltip: tr('Conversaciones anteriores'),
              onPressed: () => _chat.currentState?.pickPastConversation(),
            ),
          ],
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
  String _session = '';
  bool _sending = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _start();
  }

  /// `profile`, not `slug`: wizard_server resolves the agent from body["profile"]
  /// and ignores anything else, so a slug here reached the DEFAULT agent - this
  /// conversation would have been with the wrong agent, under the right name.
  Map<String, dynamic> _body([Map<String, dynamic>? extra]) => {
        if (!widget.agent.isDefault) 'profile': widget.agent.slug,
        ...?extra,
      };

  Future<void> _start() async {
    try {
      // Whether talking is switched on, first: everything below depends on it.
      final st = await widget.link.reach('POST', '/api/talk/status', body: _body());
      if (st['ok'] != true || st['enabled'] != true) {
        setState(() => _error = (st['detail'] ??
                tr('Todavía no está activado para este agente.'))
            .toString());
        return;
      }
      // Where you left off. The thread this phone had open comes first; failing that,
      // the most recent conversation this agent had anywhere - including on Telegram,
      // because it is the same agent and picking up mid-thought is the common case.
      // A clean start is still one tap away, which is the right way round: carrying on
      // is what you usually want, and starting over is a decision.
      _session = _openThreads[widget.agent.slug] ?? '';
      if (_session.isEmpty) {
        final sessions = await widget.link
            .reach('POST', '/api/talk/sessions', body: _body({'limit': 1}));
        if (sessions['ok'] == true) {
          final rows = sessions['sessions'] as List? ?? [];
          if (rows.isNotEmpty && rows.first is Map) {
            _session = ((rows.first as Map)['id'] ?? '').toString();
            if (_session.isNotEmpty) {
              _openThreads[widget.agent.slug] = _session;
              await Store.saveThreads(_openThreads);
            }
          }
        }
      }
      setState(() {
        _ready = true;
        _error = '';
      });
      if (_session.isNotEmpty) {
        setState(() => _loadingHistory = true);
        try {
          await _history();
        } finally {
          if (mounted) setState(() => _loadingHistory = false);
        }
      }
    } catch (e) {
      setState(() => _error = humanError(e));
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
    final h = await widget.link
        .reach('POST', '/api/talk/history', body: _body({'session_id': _session}));
    if (h['ok'] != true) {
      setState(() => _error = (h['detail'] ?? tr('No se pudo leer la conversación.'))
          .toString());
      return;
    }
    setState(() {
      _msgs = _readable(h['messages'] as List? ?? []);
      _error = '';
    });
    _toBottom();
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

  Future<void> _send() async {
    final text = _input.text.trim();
    // A message can be files alone - an agent that is sent a screenshot and nothing
    // else has still been asked something.
    if ((text.isEmpty && _attached.isEmpty) || _sending) return;
    final files = [for (final f in _attached) f['id']];
    final shownNames = [for (final f in _attached) (f['name'] ?? '').toString()];
    final outgoing = _withQuote(text);
    setState(() {
      _sending = true;
      _msgs = [
        ..._msgs,
        {'role': 'user', 'text': outgoing, 'files': shownNames},
        {'role': 'assistant', 'pending': true},
      ];
      _input.clear();
      _attached.clear();
      _replyTo = null;
    });
    _toBottom();
    try {
      // The conversation is created by the first thing said in it.
      if (_session.isEmpty) {
        final made = await widget.link.reach('POST', '/api/talk/new', body: _body());
        if (made['ok'] != true) {
          throw LinkError((made['detail'] ?? tr('No se pudo abrir la conversación.'))
              .toString());
        }
        _session = ((made['session'] as Map?)?['id'] ?? made['id'] ?? '').toString();
        if (_session.isEmpty) {
          throw LinkError('El computadora no abrió ninguna conversación.');
        }
        _openThreads[widget.agent.slug] = _session;
        await Store.saveThreads(_openThreads);
      }
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
      final res = await widget.link.reach('POST', '/api/talk/send',
          body: _body({
            'session_id': _session,
            'text': outgoing,
            if (files.isNotEmpty) 'files': files,
          }),
          wait: Link.thinkingWait);
      if (res['ok'] != true) throw LinkError((res['detail'] ?? tr('Sin respuesta')).toString());
      // The reply comes back on the send itself; re-reading the whole transcript to
      // find it would also drag in every tool call the agent made on the way.
      final reply = (res['reply'] ?? '').toString().trim();
      final shown = reply.isEmpty ? tr('(sin respuesta)') : reply;
      setState(() {
        _msgs = [
          ..._msgs.where((m) => m['pending'] != true),
          {
            'role': 'assistant',
            'text': shown,
            if (res['files'] is List) 'files': res['files'],
          },
        ];
      });
      _toBottom();
      // Only when they are not already looking at it.
      if (!_inFront) {
        await Native.notify(widget.agent.name, shown);
      }
    } catch (e) {
      setState(() {
        _msgs = _msgs.where((m) => m['pending'] != true).toList();
        _error = humanError(e);
      });
    } finally {
      await Native.waitStop();
      if (mounted) setState(() => _sending = false);
    }
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

  /// A restored conversation is being fetched. Distinct from 'there is nothing here':
  /// showing the empty state during the fetch told me, convincingly, that persistence
  /// was broken when it was working - and it would tell the owner the same lie.
  bool _loadingHistory = false;

  bool _recording = false;
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
          _say((res['detail'] ?? trp('No pude subir {0}.', [f.name])).toString());
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
      try {
        await Native.recordStart();
      } on NativeError catch (e) {
        _say(e.message);
        return;
      }
      setState(() {
        _recording = true;
        _recordingSince = DateTime.now();
      });
      // Only to redraw the running time; the recording itself is Android's.
      _tick = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
      return;
    }
    _tick?.cancel();
    setState(() => _recording = false);
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
    final mine = <String, dynamic>{
      'role': 'user',
      'text': '',
      'voice': true,
      'audioBytes': note.bytes,
      'files': [
        {'name': note.name, 'kind': 'audio'}
      ],
      'pending': true,
    };
    setState(() {
      _sending = true;
      _msgs = [..._msgs, mine];
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
      final heard = await widget.link.reach('POST', '/api/talk/voice',
          body: _body({'name': note.name, 'data': base64Encode(note.bytes)}),
          wait: Link.thinkingWait);
      if (heard['ok'] != true) {
        throw LinkError(
            (heard['detail'] ?? tr('No pude transcribir la nota.')).toString());
      }
      final text = (heard['text'] ?? '').toString().trim();
      if (text.isEmpty) throw LinkError(tr('No entendí nada en esa nota.'));
      final rec = (heard['file'] as Map?)?.cast<String, dynamic>() ??
          <String, dynamic>{'name': note.name, 'kind': 'audio'};
      setState(() {
        mine['text'] = text;
        mine['files'] = [rec];
        mine['pending'] = false;
        _msgs = [..._msgs, {'role': 'assistant', 'pending': true}];
      });
      _toBottom();

      if (_session.isEmpty) {
        final made = await widget.link.reach('POST', '/api/talk/new', body: _body());
        if (made['ok'] != true) {
          throw LinkError(
              (made['detail'] ?? tr('No se pudo abrir la conversación.')).toString());
        }
        _session = ((made['session'] as Map?)?['id'] ?? made['id'] ?? '').toString();
        if (_session.isEmpty) {
          throw LinkError(tr('La computadora no abrió ninguna conversación.'));
        }
        _openThreads[widget.agent.slug] = _session;
        await Store.saveThreads(_openThreads);
      }
      // The agent gets the words - it cannot listen - with the recording attached so
      // its own tools can open the thing that was actually said.
      final res = await widget.link.reach('POST', '/api/talk/send',
          body: _body({
            'session_id': _session,
            'text': _withQuote(text),
            if (rec['id'] != null) 'files': [rec['id']],
          }),
          wait: Link.thinkingWait);
      if (res['ok'] != true) {
        throw LinkError((res['detail'] ?? tr('Sin respuesta')).toString());
      }
      final reply = (res['reply'] ?? '').toString().trim();
      final shown = reply.isEmpty ? tr('(sin respuesta)') : reply;
      setState(() {
        _replyTo = null;
        _msgs = [
          ..._msgs.where((m) => m['pending'] != true || m == mine),
          {
            'role': 'assistant',
            'text': shown,
            if (res['files'] is List) 'files': res['files'],
          },
        ];
      });
      _toBottom();
      if (!_inFront) await Native.notify(widget.agent.name, shown);
    } catch (e) {
      setState(() {
        _msgs = _msgs.where((m) => m['pending'] != true || m == mine).toList();
        mine['pending'] = false;
        _error = humanError(e);
      });
    } finally {
      await Native.waitStop();
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _recordCancel() async {
    _tick?.cancel();
    await Native.recordCancel();
    if (mounted) setState(() => _recording = false);
  }

  /// Start a clean thread with this agent.
  ///
  /// No session is created here: an empty conversation nobody held is litter in the
  /// list. The next thing said makes one.
  void newConversation() {
    setState(() {
      _session = '';
      _msgs = [];
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
  Future<void> pickPastConversation() async {
    final loading = showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const Center(child: CircularProgressIndicator()),
    );
    List<Map<String, dynamic>> rows = [];
    String problem = '';
    try {
      final res = await widget.link
          .reach('POST', '/api/talk/sessions', body: _body({'limit': 30}));
      if (res['ok'] != true) {
        problem = (res['detail'] ?? tr('No pude leer las conversaciones.')).toString();
      } else {
        rows = [
          for (final r in (res['sessions'] as List? ?? []))
            (r as Map).cast<String, dynamic>(),
        ];
      }
    } catch (e) {
      problem = humanError(e);
    }
    if (!mounted) return;
    Navigator.of(context, rootNavigator: true).pop();
    await loading.then((_) {});
    if (!mounted) return;
    if (problem.isNotEmpty) {
      _say(problem);
      return;
    }
    if (rows.isEmpty) {
      _say(tr('Todavía no hay conversaciones anteriores.'));
      return;
    }
    final chosen = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      // Capped, and not merely 'as tall as it needs': a scrollable sheet with thirty
      // conversations in it grows to the full height of the screen and puts its own
      // heading under the clock.
      constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.72),
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (sheet) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 16, 18, 8),
            child: Row(children: [
              Text(tr('Conversaciones anteriores'),
                  style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                      color: context.c.ink)),
            ]),
          ),
          // Somebody who opens this list looking for 'a different conversation' is
          // often after a new one.
          ListTile(
            leading: Icon(Icons.edit_square, color: context.c.accent),
            title: Text(tr('Conversación nueva'),
                style: TextStyle(
                    color: context.c.accent, fontWeight: FontWeight.w600)),
            onTap: () => Navigator.pop(sheet, <String, dynamic>{'new': true}),
          ),
          Divider(height: 1, color: context.c.line),
          Flexible(
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: rows.length,
              itemBuilder: (c, i) {
                final r = rows[i];
                final telegram = (r['source'] ?? '') == 'telegram';
                return ListTile(
                  leading: Icon(
                      telegram ? Icons.send : Icons.chat_bubble_outline,
                      color: context.c.muted),
                  title: Text(_sessionTitle(r),
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text(_sessionWhen(r),
                      style: TextStyle(fontSize: 12, color: context.c.muted)),
                  onTap: () => Navigator.pop(sheet, r),
                );
              },
            ),
          ),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (chosen == null || !mounted) return;
    if (chosen['new'] == true) {
      newConversation();
      return;
    }
    final id = (chosen['id'] ?? '').toString();
    if (id.isEmpty) return;
    setState(() {
      _session = id;
      _msgs = [];
      _replyTo = null;
      _error = '';
    });
    _openThreads[widget.agent.slug] = id;
    await Store.saveThreads(_openThreads);
    await _history();
  }

  /// What to call a conversation nobody named.
  String _sessionTitle(Map<String, dynamic> r) {
    final t = (r['title'] ?? '').toString().trim();
    if (t.isNotEmpty) return t;
    final p = (r['preview'] ?? '').toString().trim().replaceAll(RegExp(r'\s+'), ' ');
    if (p.isNotEmpty) return p.length > 70 ? '${p.substring(0, 70)}…' : p;
    return tr('Sin título');
  }

  String _sessionWhen(Map<String, dynamic> r) {
    final secs = (r['last_active'] ?? r['started_at']);
    final n = (r['message_count'] as num?)?.toInt() ?? 0;
    final where = (r['source'] ?? '') == 'telegram' ? 'Telegram' : 'Olivaw';
    if (secs is! num) return where;
    final when = DateTime.fromMillisecondsSinceEpoch((secs * 1000).round());
    final ago = DateTime.now().difference(when);
    String rel;
    if (ago.inMinutes < 60) {
      rel = trp('hace {0} min', [ago.inMinutes]);
    } else if (ago.inHours < 24) {
      rel = trp('hace {0} h', [ago.inHours]);
    } else if (ago.inDays < 30) {
      rel = trp('hace {0} d', [ago.inDays]);
    } else {
      rel = '${when.day}/${when.month}/${when.year}';
    }
    return n > 0
        ? '$where · $rel · ${trp('{0} mensajes', [n])}'
        : '$where · $rel';
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
  Widget _recordingRow() {
    final secs = DateTime.now().difference(_recordingSince ?? DateTime.now()).inSeconds;
    final mm = (secs ~/ 60).toString();
    final ss = (secs % 60).toString().padLeft(2, '0');
    return Row(children: [
      IconButton(
        onPressed: _recordCancel,
        icon: Icon(Icons.delete_outline, color: context.c.err),
        tooltip: tr('Descartar'),
      ),
      Expanded(
        child: Row(children: [
          Icon(Icons.fiber_manual_record, size: 12, color: context.c.err),
          const SizedBox(width: 8),
          Text('${tr('Grabando')}  $mm:$ss',
              style: TextStyle(
                  color: context.c.ink,
                  fontWeight: FontWeight.w600,
                  fontFeatures: const [FontFeature.tabularFigures()])),
        ]),
      ),
      IconButton.filled(
        onPressed: _recordToggle,
        icon: const Icon(Icons.stop),
        tooltip: tr('Terminar'),
      ),
    ]);
  }

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
      if (_loadingHistory)
        const Expanded(child: Center(child: CircularProgressIndicator()))
      else if (_msgs.isEmpty && _error.isEmpty && _ready)
        Expanded(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Text(tr('Todavía no os habéis dicho nada.'),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: context.c.muted)),
            ),
          ),
        )
      else
      Expanded(
        child: ListView.builder(
          controller: _scroll,
          padding: const EdgeInsets.all(14),
          itemCount: _msgs.length,
          itemBuilder: (c, i) {
            final m = _msgs[i];
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
      ),
      if (_replyTo != null) _replyStrip(),
      if (_attached.isNotEmpty) _attachedStrip(),
      SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
          child: _recording ? _recordingRow() : _composerRow(),
        ),
      ),
    ]);
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
                  border: mine ? null : Border.all(color: context.c.line),
                ),
                child: Builder(builder: (bc) {
                  final ink = mine ? context.c.accentInk : context.c.ink;
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
                  final body2 = mine
                      ? Text(body, style: TextStyle(color: ink))
                      : Markdown(body,
                          baseStyle: TextStyle(color: ink, height: 1.35),
                          onLink: _openLink);
                  if (files.isEmpty) return body2;
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (body.isNotEmpty) body2,
                      for (final f in files)
                        Attachment(
                          link: widget.link,
                          file: f,
                          body: _body,
                          ink: ink,
                        ),
                    ],
                  );
                }),
              ),
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
      final res = await widget.link.reach('POST', '/api/cron/list',
          body: {if (!widget.agent.isDefault) 'profile': widget.agent.slug});
      setState(() {
        _rows = ((res['jobs'] ?? res['rows'] ?? []) as List)
            .map((r) => (r as Map).cast<String, dynamic>())
            .toList();
        _loading = false;
        _error = res['ok'] == false ? (res['detail'] ?? '').toString() : '';
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
                  content: Text((res['detail'] ?? tr('No se pudo')).toString())));
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

  @override
  void initState() {
    super.initState();
    _open();
  }

  @override
  void dispose() {
    _stream?.close();
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
          setState(() => _error = (e['detail'] ?? '').toString());
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
                  return GestureDetector(
                    onTapDown: (d) =>
                        _tapAt(d.localPosition, Size(box.maxWidth, box.maxHeight)),
                    onVerticalDragUpdate: (d) {
                      if (!_armed) return;
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
            'La ventana está tapada en el computadora, así que puede no refrescarse.',
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
