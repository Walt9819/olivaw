// What this agent is called, what it looks like, and whether it can be talked to.
//
// Only the settings that make sense from a phone. Renaming is here because a name is
// the thing you most often get wrong first and most want to change later; the face is
// here because it is how you find the agent in a list of seven. Switching talking on is
// here because without it the agent cannot be reached at all, and discovering that from
// a train with no way to fix it is the worst version of that.
//
// It is also where the agent explains itself: what it is for, what it knows how to
// do, and who else it talks to. Those were only answerable by walking to the
// computer and opening a folder, which is no use to somebody holding a phone.
//
// CHANGING things needs the `config` permission; READING them does not, so the
// screen adapts rather than being hidden. A phone that may only look still gets to
// find out why its agent answers the way it does.

import 'dart:convert';

import 'package:flutter/material.dart';

import 'agent_info.dart';
import 'link.dart';
import 'loading.dart';
import 'native.dart';
import 'strings.dart';
import 'theme.dart';

class AgentSettings extends StatefulWidget {
  const AgentSettings({
    super.key,
    required this.link,
    required this.slug,
    required this.name,
    required this.isDefault,
    required this.canEdit,
    this.onOpenFiles,
  });

  final Link link;
  final String slug;
  final String name;
  final bool isDefault;

  /// Whether this phone holds `config`. Decides which halves of the screen are
  /// switches and which are statements.
  final bool canEdit;

  /// Opens the photos-and-files screen, which needs the open conversation and so
  /// cannot be built from here. Null when there is no conversation to show.
  final VoidCallback? onOpenFiles;

  @override
  State<AgentSettings> createState() => _AgentSettingsState();
}

/// The first line of the purpose, for a row that has one line to spare.
String _firstLine(String md) {
  for (final raw in md.split('\n')) {
    final line = raw.replaceAll(RegExp(r'^#+\s*'), '').trim();
    if (line.isNotEmpty) return line;
  }
  return '';
}

/// The skills, as a list. Its own first: a skill the owner gave to THIS agent
/// is a decision they made, and one of the machine's is furniture.
class _SkillsScreen extends StatelessWidget {
  const _SkillsScreen({
    required this.link,
    required this.body,
    required this.skills,
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final List<SkillRow> skills;

  @override
  Widget build(BuildContext context) {
    final own = skills.where((s) => s.isOwn).toList();
    final shared = skills.where((s) => !s.isOwn).toList();
    return Scaffold(
      appBar: AppBar(title: Text(tr('Lo que sabe hacer'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (own.isNotEmpty) ...[
            Text(tr('Suyas'),
                style: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: context.c.muted,
                    fontSize: 12)),
            for (final sk in own) _tile(context, sk),
          ],
          if (shared.isNotEmpty) ...[
            const SizedBox(height: 18),
            Text(tr('De la computadora'),
                style: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: context.c.muted,
                    fontSize: 12)),
            const SizedBox(height: 2),
            Text(tr('Las tienen todos los agentes de esta máquina.'),
                style: TextStyle(fontSize: 11, color: context.c.muted)),
            for (final sk in shared) _tile(context, sk),
          ],
        ],
      ),
    );
  }

  Widget _tile(BuildContext context, SkillRow sk) => ListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(sk.name),
        subtitle: sk.description.isEmpty
            ? null
            : Text(sk.description,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: context.c.muted)),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
              builder: (_) =>
                  SkillScreen(link: link, body: body, row: sk),
            )),
      );
}

class _AgentSettingsState extends State<AgentSettings> {
  late final TextEditingController _name = TextEditingController(text: widget.name);
  bool _busy = false;
  bool _dirty = false;
  String _error = '';

  /// Two switches, because they are two questions. The gateway underneath runs
  /// while either wants it; neither of these is 'the real one'.
  bool? _onDesktop;
  bool? _onPhone;
  String _talkDetail = '';

  bool _loading = true;
  String _purpose = '';
  String _purposeBase = '';
  List<SkillRow> _skills = const [];
  List<Map<String, dynamic>> _peers = const [];
  String _how = '';
  String _howName = '';

  /// Whether anything was actually changed, so the roster behind can reload.
  bool _changed = false;

  Map<String, dynamic> _agentBody([Map<String, dynamic>? extra]) => {
        'slug': widget.slug,
        ...?extra,
      };

  /// `profile`, not `slug` - the talk routes are scoped to one agent and
  /// wizard_server resolves those from body["profile"].
  Map<String, dynamic> _profileBody([Map<String, dynamic>? extra]) => {
        if (!widget.isDefault) 'profile': widget.slug,
        ...?extra,
      };

  @override
  void initState() {
    super.initState();
    _loadTalk();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  /// One request for the whole screen. Three would be three waits on mobile data,
  /// and this screen is mostly read.
  Future<void> _loadTalk() async {
    try {
      final res = await widget.link
          .reach('POST', '/api/agent/info', body: _profileBody());
      if (!mounted) return;
      // An answer that says no is not an answer with nothing in it. Without this,
      // a refusal - or an older computer that has never heard of this route -
      // painted an agent with no purpose, no skills and no colleagues, which is a
      // confident lie about an agent that has all three.
      if (res['ok'] != true) {
        setState(() {
          _loading = false;
          _error = tr((res['detail'] ??
                  tr('Esta computadora todavía no sabe contarme sobre el agente.'))
              .toString());
        });
        return;
      }
      final talk = (res['talk'] as Map?) ?? const {};
      final purpose = (res['purpose'] as Map?) ?? const {};
      final peers = (res['peers'] as Map?) ?? const {};
      setState(() {
        _loading = false;
        _onDesktop = talk['desktop'] == true;
        _onPhone = talk['phone'] == true;
        _talkDetail = tr((talk['detail'] ?? '').toString());
        _purpose = (purpose['markdown'] ?? '').toString();
        _purposeBase = (purpose['base'] ?? '').toString();
        _skills = ((res['skills'] as List?) ?? const [])
            .whereType<Map>()
            .map(SkillRow.from)
            .toList();
        _peers = ((peers['agents'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList();
        _how = (peers['how'] ?? '').toString();
        _howName = (peers['how_name'] ?? '').toString();
      });
    } catch (e) {
      // Said out loud. This used to fail silently on the grounds that not knowing is
      // not worth a banner - and the screen then showed an agent with no purpose, no
      // skills and no colleagues, which is not 'nothing', it is a confident lie about
      // an agent that has all three.
      if (mounted) {
        setState(() {
          _loading = false;
          _error = humanError(e);
        });
      }
    }
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() call) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final res = await call();
      if (res['ok'] != true) {
        setState(() => _error = tr((res['detail'] ?? tr('No se pudo')).toString()));
        return;
      }
      _changed = true;
    } catch (e) {
      setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _rename() async {
    final wanted = _name.text.trim();
    if (wanted.isEmpty || wanted == widget.name) return;
    await _run(() => widget.link.reach('POST', '/api/agent/rename',
        body: _agentBody({'name': wanted}), wait: Link.thinkingWait));
    if (mounted && _error.isEmpty) setState(() => _dirty = false);
  }

  Future<void> _pickPicture() async {
    try {
      final picked = await Native.pick(mime: 'image/*');
      if (picked.isEmpty) return;
      await _run(() => widget.link.reach('POST', '/api/avatar/set',
          body: _agentBody({'data': base64Encode(picked.first.bytes)}),
          wait: Link.thinkingWait));
    } on NativeError catch (e) {
      setState(() => _error = e.message);
    }
  }

  Future<void> _setEmoji(String emoji) =>
      _run(() => widget.link.reach('POST', '/api/avatar/set',
          body: _agentBody({'emoji': emoji})));

  Future<void> _clearFace() => _run(() =>
      widget.link.reach('POST', '/api/avatar/set', body: _agentBody({'clear': true})));

  Future<void> _toggle(String where, bool on) async {
    await _run(() => widget.link.reach('POST', '/api/talk/access',
        body: _profileBody({'where': where, 'on': on}), wait: Link.thinkingWait));
    if (mounted) {
      setState(() {
        if (where == 'phone') _onPhone = on;
        if (where == 'desktop') _onDesktop = on;
      });
      // Switching one on may start the gateway, which queues a restart on the
      // computer, so the truth arrives a moment later.
      await _loadTalk();
    }
  }

  void _emojiSheet() {
    // A short list rather than a picker: these are the ones that read at 30 pixels, and
    // a full emoji keyboard on a 30-pixel circle is a way to choose something illegible.
    const faces = [
      '🤖', '🧠', '📚', '🔭', '🛠️', '📨', '🗂️', '🎯',
      '🧪', '🪄', '📊', '🧭', '🔔', '💡', '🌱', '⚙️',
    ];
    showModalBottomSheet<void>(
      context: context,
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              for (final e in faces)
                InkWell(
                  onTap: () {
                    Navigator.pop(sheet);
                    _setEmoji(e);
                  },
                  child: Container(
                    width: 48,
                    height: 48,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: context.c.panel2,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(e, style: const TextStyle(fontSize: 24)),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.pop(context, _changed);
      },
      child: Scaffold(
        appBar: AppBar(title: Text(tr('Ajustes del agente'))),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error.isNotEmpty)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: 14),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: context.c.warnBg,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(_error,
                    style: TextStyle(fontSize: 12, color: context.c.ink)),
              ),

            if (_loading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 30),
                child: Waiting(lines: ['Preguntándole al agente…']),
              ),

            if (widget.canEdit) ...[
            Text(tr('La cara'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 8),
            Wrap(spacing: 10, runSpacing: 10, children: [
              OutlinedButton.icon(
                onPressed: _busy ? null : _pickPicture,
                icon: const Icon(Icons.image_outlined, size: 18),
                label: Text(tr('Foto')),
              ),
              OutlinedButton.icon(
                onPressed: _busy ? null : _emojiSheet,
                icon: const Icon(Icons.emoji_emotions_outlined, size: 18),
                label: Text(tr('Símbolo')),
              ),
              OutlinedButton.icon(
                onPressed: _busy ? null : _clearFace,
                icon: const Icon(Icons.restart_alt, size: 18),
                label: Text(tr('Iniciales')),
              ),
            ]),
            const SizedBox(height: 24),

            Text(tr('El nombre'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 8),
            TextField(
              controller: _name,
              enabled: !_busy,
              textInputAction: TextInputAction.done,
              decoration: InputDecoration(hintText: widget.name),
              onChanged: (v) => setState(() => _dirty = v.trim() != widget.name),
              onSubmitted: (_) => _rename(),
            ),
            const SizedBox(height: 6),
            Text(
              tr('Cambiarlo aquí también cambia cómo se presenta el agente.'),
              style: TextStyle(fontSize: 11, color: context.c.muted),
            ),
            if (_dirty) ...[
              const SizedBox(height: 10),
              FilledButton(
                onPressed: _busy ? null : _rename,
                child: Text(tr('Guardar el nombre')),
              ),
            ],
            const SizedBox(height: 24),
            ],

            Text(tr('Dónde se le puede hablar'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 4),
            // Two switches, because they are two questions. One used to answer both,
            // which meant turning on a thing labelled 'desde aquí' on a computer you
            // were not sitting at in order to use your phone.
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _onPhone ?? false,
              onChanged: (_busy || _onPhone == null || !widget.canEdit)
                  ? null
                  : (v) => _toggle('phone', v),
              title: Text(tr('En este teléfono')),
              subtitle: Text(
                tr('Si está apagado, el agente no aparece para hablar desde el '
                    'teléfono.'),
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _onDesktop ?? false,
              onChanged: (_busy || _onDesktop == null || !widget.canEdit)
                  ? null
                  : (v) => _toggle('desktop', v),
              title: Text(tr('En la computadora')),
              subtitle: Text(
                _talkDetail.isNotEmpty
                    ? _talkDetail
                    : tr('La ventana de conversación de la computadora.'),
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
            ),
            if (!widget.canEdit)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  tr('Para cambiar estos interruptores, este teléfono necesita el '
                      'permiso de configuración.'),
                  style: TextStyle(fontSize: 11, color: context.c.muted),
                ),
              ),
            const SizedBox(height: 24),

            // What it IS. Readable by anybody who may look at the agent at all.
            Text(tr('Quién es'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 4),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.flag_outlined),
              title: Text(tr('Para qué es')),
              subtitle: Text(
                _purpose.trim().isEmpty
                    ? tr('Todavía no tiene uno escrito.')
                    : _firstLine(_purpose),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: _loading
                  ? null
                  : () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PurposeScreen(
                          link: widget.link,
                          body: _profileBody,
                          markdown: _purpose,
                          base: _purposeBase,
                          canEdit: widget.canEdit,
                        ),
                      )).then((_) => _loadTalk()),
            ),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.auto_awesome_outlined),
              title: Text(tr('Lo que sabe hacer')),
              subtitle: Text(
                _skills.isEmpty
                    ? tr('Nada especial todavía.')
                    : (_skills.length == 1
                        ? tr('1 habilidad')
                        : trp('{0} habilidades', [_skills.length])),
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: _loading || _skills.isEmpty
                  ? null
                  : () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => _SkillsScreen(
                          link: widget.link,
                          body: _profileBody,
                          skills: _skills,
                        ),
                      )),
            ),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.forum_outlined),
              title: Text(tr('Con quién habla')),
              subtitle: Text(
                _peers.isEmpty
                    ? tr('Es el único agente de esta computadora.')
                    : _peers.map((a) => (a['name'] ?? '').toString()).join(', '),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: _loading
                  ? null
                  : () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PeersScreen(
                          agents: _peers, how: _how, howName: _howName),
                      )),
            ),
            if (widget.onOpenFiles != null)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.perm_media_outlined),
                title: Text(tr('Fotos y archivos')),
                subtitle: Text(tr('Todo lo que os habéis mandado.'),
                    style: TextStyle(fontSize: 11, color: context.c.muted)),
                trailing: const Icon(Icons.chevron_right),
                onTap: widget.onOpenFiles,
              ),
            if (_busy)
              const Padding(
                padding: EdgeInsets.only(top: 20),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }
}
