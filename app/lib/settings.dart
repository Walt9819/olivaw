// What this agent is called, what it looks like, and whether it can be talked to.
//
// Only the settings that make sense from a phone. Renaming is here because a name is
// the thing you most often get wrong first and most want to change later; the face is
// here because it is how you find the agent in a list of seven. Switching talking on is
// here because without it the agent cannot be reached at all, and discovering that from
// a train with no way to fix it is the worst version of that.
//
// Everything on this screen needs the `config` permission, which is off until the owner
// grants it to this phone from the computer. Without it the screen is not reachable -
// the button that opens it is not drawn.

import 'dart:convert';

import 'package:flutter/material.dart';

import 'link.dart';
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
  });

  final Link link;
  final String slug;
  final String name;
  final bool isDefault;

  @override
  State<AgentSettings> createState() => _AgentSettingsState();
}

class _AgentSettingsState extends State<AgentSettings> {
  late final TextEditingController _name = TextEditingController(text: widget.name);
  bool _busy = false;
  bool _dirty = false;
  String _error = '';

  bool? _talkOn;
  String _talkDetail = '';

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

  Future<void> _loadTalk() async {
    try {
      final st = await widget.link
          .reach('POST', '/api/talk/status', body: _profileBody());
      if (!mounted) return;
      setState(() {
        _talkOn = st['enabled'] == true;
        _talkDetail = tr((st['detail'] ?? '').toString());
      });
    } catch (_) {
      // Not knowing is not an error worth a banner; the switch simply stays blank.
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

  Future<void> _toggleTalk(bool on) async {
    await _run(() => widget.link.reach(
        'POST', on ? '/api/talk/enable' : '/api/talk/disable',
        body: _profileBody(), wait: Link.thinkingWait));
    if (mounted) {
      setState(() => _talkOn = on);
      // Enabling queues a restart on the computer, so the truth arrives a moment later.
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

            Text(tr('Hablar con él'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 4),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _talkOn ?? false,
              onChanged: (_busy || _talkOn == null) ? null : _toggleTalk,
              title: Text(tr('Conversaciones desde aquí')),
              subtitle: Text(
                _talkDetail.isNotEmpty
                    ? _talkDetail
                    : tr('Abre el canal del agente en la computadora, solo para ella.'),
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
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
