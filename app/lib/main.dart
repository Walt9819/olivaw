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

import 'link.dart';
import 'sha256.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // A hash that is quietly wrong would make every computer look like an impostor.
  assert(sha256SelfTest(), 'SHA-256 self test failed');
  runApp(const OlivawApp());
}

const _ink = Color(0xFF14151A);
const _paper = Color(0xFFF7F7F5);
const _accent = Color(0xFF2F6F4E);

class OlivawApp extends StatelessWidget {
  const OlivawApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Olivaw',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: _accent),
        scaffoldBackgroundColor: _paper,
        fontFamily: 'Roboto',
      ),
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
      if (cert == null) throw LinkError('No pude ver el certificado del ordenador.');
      final der = Uint8List.fromList(cert.der);
      final words = shortCode(hex(sha256(der)));
      if (!mounted) return;
      final agreed = await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
              title: const Text('¿Dice esto tu ordenador?'),
              content: Column(mainAxisSize: MainAxisSize.min, children: [
                const Text('En la pantalla del ordenador, debajo del código, hay dos '
                    'palabras. Tienen que ser exactamente estas:'),
                const SizedBox(height: 14),
                SelectableText(words,
                    style: const TextStyle(
                        fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: 1)),
                const SizedBox(height: 14),
                const Text('Si no coinciden, no continúes: no estarías hablando con tu '
                    'ordenador.', style: TextStyle(fontSize: 12)),
              ]),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(c, false),
                    child: const Text('No coinciden')),
                FilledButton(
                    onPressed: () => Navigator.pop(c, true),
                    child: const Text('Son estas')),
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
      draft.name = 'Mi ordenador';
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
            ? 'El ordenador presentó otro certificado. No he seguido.'
            : 'No pude conectar: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Enlazar tu ordenador')),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        const Text('En el ordenador, abre Olivaw y ve a «Tu teléfono» → '
            '«Enlazar un teléfono». Te dará una dirección y un código.'),
        const SizedBox(height: 20),
        if (_scanning)
          const Row(children: [
            SizedBox(
                width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
            SizedBox(width: 10),
            Text('Buscando en tu wifi…'),
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
            labelText: 'Dirección del ordenador',
            hintText: '192.168.1.40',
            border: OutlineInputBorder(),
          ),
          keyboardType: TextInputType.url,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _pin,
          decoration: const InputDecoration(
            labelText: 'Código de seis cifras',
            border: OutlineInputBorder(),
          ),
          keyboardType: TextInputType.number,
          maxLength: 6,
          style: const TextStyle(fontSize: 24, letterSpacing: 6),
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: _busy ? null : _pair,
          child: Text(_busy ? 'Enlazando…' : 'Enlazar'),
        ),
        TextButton(onPressed: _scanning ? null : _scan, child: const Text('Buscar otra vez')),
        if (_status.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(_status, style: const TextStyle(color: Colors.redAccent)),
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
      ? 'encendido'
      : running == false
          ? 'apagado'
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
      if (res['ok'] != true) throw LinkError((res['detail'] ?? 'Sin respuesta').toString());
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
        title: const Text('Tus agentes'),
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
                    const Icon(Icons.wifi_off, size: 42, color: Colors.black26),
                    const SizedBox(height: 12),
                    Text(_error, textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    const Text(
                      'Si estás fuera de casa, tu ordenador necesita tener configurado '
                      'el punto de encuentro en «Tu teléfono».',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 12, color: Colors.black54),
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
      decoration: BoxDecoration(color: agent.colour, shape: BoxShape.circle),
      child: Text(
        agent.emoji.isNotEmpty ? agent.emoji : agent.initials,
        style: TextStyle(
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

  @override
  Widget build(BuildContext context) {
    final canBrowse = widget.pairing.caps.contains('navegador');
    final tabs = <Widget>[
      ChatTab(link: widget.link, agent: widget.agent),
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
      ),
      body: tabs[_tab.clamp(0, tabs.length - 1)],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab.clamp(0, tabs.length - 1),
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [
          const NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline), label: 'Hablar'),
          const NavigationDestination(
              icon: Icon(Icons.schedule), label: 'Rutinas'),
          if (canBrowse)
            const NavigationDestination(
                icon: Icon(Icons.travel_explore), label: 'Navegador'),
        ],
      ),
    );
  }
}

/// The conversation currently open with each agent, for as long as the app is running.
/// Deliberately not saved to disk: a new launch starts clean, the way the desktop
/// console does. Reopening past conversations is a separate thing, with its own screen.
final Map<String, String> _openThreads = {};

class ChatTab extends StatefulWidget {
  const ChatTab({super.key, required this.link, required this.agent});
  final Link link;
  final Agent agent;

  @override
  State<ChatTab> createState() => _ChatTabState();
}

class _ChatTabState extends State<ChatTab> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  List<Map<String, dynamic>> _msgs = [];
  String _session = '';
  bool _sending = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
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
      // Only whether talking is switched on for this agent. No session is opened here:
      // the desktop console starts a clean thread rather than reopening the last one,
      // and creating one on every tap into an agent would litter the list with empty
      // conversations nobody held. One is made when there is something to put in it.
      final st = await widget.link.reach('POST', '/api/talk/status', body: _body());
      if (st['ok'] != true || st['enabled'] != true) {
        setState(() => _error = (st['detail'] ??
                'Todavía no está activado para este agente.')
            .toString());
        return;
      }
      // Coming back to an agent you were just talking to should find the conversation
      // where you left it, not an empty screen - leaving the chat for the routines tab
      // and returning is not the same as starting again.
      _session = _openThreads[widget.agent.slug] ?? '';
      setState(() {
        _ready = true;
        _error = '';
      });
      if (_session.isNotEmpty) await _history();
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
      if (text.isEmpty) continue;
      if (text.startsWith('[CONTEXT COMPACTION')) continue;  // bookkeeping, not a turn
      out.add({'role': role, 'text': text});
    }
    return out;
  }

  Future<void> _history() async {
    if (_session.isEmpty) return;
    final h = await widget.link
        .reach('POST', '/api/talk/history', body: _body({'session_id': _session}));
    if (h['ok'] != true) {
      setState(() => _error = (h['detail'] ?? 'No se pudo leer la conversación.')
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

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() {
      _sending = true;
      _msgs = [
        ..._msgs,
        {'role': 'user', 'text': text},
        {'role': 'assistant', 'pending': true},
      ];
      _input.clear();
    });
    _toBottom();
    try {
      // The conversation is created by the first thing said in it.
      if (_session.isEmpty) {
        final made = await widget.link.reach('POST', '/api/talk/new', body: _body());
        if (made['ok'] != true) {
          throw LinkError((made['detail'] ?? 'No se pudo abrir la conversación.')
              .toString());
        }
        _session = ((made['session'] as Map?)?['id'] ?? made['id'] ?? '').toString();
        if (_session.isEmpty) {
          throw LinkError('El ordenador no abrió ninguna conversación.');
        }
        _openThreads[widget.agent.slug] = _session;
      }
      final res = await widget.link.reach('POST', '/api/talk/send',
          body: _body({'session_id': _session, 'text': text}),
          wait: Link.thinkingWait);
      if (res['ok'] != true) throw LinkError((res['detail'] ?? 'Sin respuesta').toString());
      // The reply comes back on the send itself; re-reading the whole transcript to
      // find it would also drag in every tool call the agent made on the way.
      final reply = (res['reply'] ?? '').toString().trim();
      setState(() {
        _msgs = [
          ..._msgs.where((m) => m['pending'] != true),
          {'role': 'assistant', 'text': reply.isEmpty ? '(sin respuesta)' : reply},
        ];
      });
      _toBottom();
    } catch (e) {
      setState(() {
        _msgs = _msgs.where((m) => m['pending'] != true).toList();
        _error = humanError(e);
      });
    } finally {
      setState(() => _sending = false);
    }
  }

  /// Talking is switched on for this agent, so there is somewhere for a message to go.
  /// A session is opened on the first send, not on arrival.
  bool _ready = false;

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      if (_error.isNotEmpty)
        Container(
          width: double.infinity,
          color: const Color(0xFFFFF4E5),
          padding: const EdgeInsets.all(10),
          child: Text(_error, style: const TextStyle(fontSize: 12)),
        ),
      if (_msgs.isEmpty && _error.isEmpty && _ready)
        const Expanded(
          child: Center(
            child: Padding(
              padding: EdgeInsets.all(32),
              child: Text('Todavía no os habéis dicho nada.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.black54)),
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
            return Align(
              alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
              child: Container(
                margin: const EdgeInsets.symmetric(vertical: 4),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(c).size.width * 0.78),
                decoration: BoxDecoration(
                  color: mine ? _accent : Colors.white,
                  borderRadius: BorderRadius.circular(16),
                  border: mine ? null : Border.all(color: Colors.black12),
                ),
                child: Text(
                  m['pending'] == true ? 'escribiendo…' : (m['text'] ?? '').toString(),
                  style: TextStyle(
                    color: mine ? Colors.white : _ink,
                    fontStyle:
                        m['pending'] == true ? FontStyle.italic : FontStyle.normal,
                  ),
                ),
              ),
            );
          },
        ),
      ),
      SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(10, 0, 10, 8),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: _input,
                minLines: 1,
                maxLines: 4,
                // A box you can type into is a promise. Without a conversation open
                // there is nowhere for the words to go, so it does not make one.
                enabled: _ready,
                decoration: InputDecoration(
                  hintText: _ready ? 'Escribe…' : 'No disponible',
                  border: const OutlineInputBorder(),
                  isDense: true,
                ),
                onSubmitted: (_) => _send(),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: (_sending || !_ready) ? null : _send,
              icon: const Icon(Icons.send),
            ),
          ]),
        ),
      ),
    ]);
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
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(28),
          child: Text('Este agente no hace nada por su cuenta todavía.',
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
                  content: Text((res['detail'] ?? 'No se pudo').toString())));
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
              child: const Text('Reintentar'),
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
                hintText: _armed ? 'Ir a…' : 'Toma el control para navegar',
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
          color: Colors.black,
          child: frame == null
              ? const Center(
                  child: Text('Esperando la primera imagen…',
                      style: TextStyle(color: Colors.white54)))
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
          color: const Color(0xFFFFF4E5),
          padding: const EdgeInsets.all(8),
          child: const Text(
            'La ventana está tapada en el ordenador, así que puede no refrescarse.',
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
                    ? FilledButton.styleFrom(backgroundColor: const Color(0xFFB45309))
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
                    ? 'Soltar el control (${_state['armed_for'] ?? 0}s)'
                    : 'Tomar el control'),
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
                        title: const Text('Escribir en la página'),
                        content: TextField(controller: t, autofocus: true),
                        actions: [
                          TextButton(
                              onPressed: () => Navigator.pop(c),
                              child: const Text('Cancelar')),
                          FilledButton(
                              onPressed: () => Navigator.pop(c, t.text),
                              child: const Text('Escribir')),
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
