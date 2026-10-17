// What an agent is, on a phone: what it is for, what it knows how to do, who it talks to.
//
// The console can afford to send somebody to a folder on disk. A phone cannot, and the
// three questions an owner actually asks about an agent - why does it answer like that,
// what can it do, who else does it speak to - were only answerable by walking to the
// computer. They are all files; this reads them.
//
// One request fetches the lot (`agent/info`), because three round trips on mobile data
// are three waits. A skill's full instructions are fetched only when one is opened: the
// list needs a name and a line, and the bodies together are a few hundred kilobytes.

import 'package:flutter/material.dart';

import 'link.dart';
import 'loading.dart';
import 'markdown.dart';
import 'strings.dart';
import 'theme.dart';

/// One skill, as the list shows it.
class SkillRow {
  SkillRow(this.id, this.name, this.description, this.scope);

  final String id, name, description, scope;

  /// Its own, rather than every agent's. Worth saying: a skill the owner gave to THIS
  /// agent is a decision they made, and one of the machine's is just furniture.
  bool get isOwn => scope == 'agent';

  static SkillRow from(Map m) => SkillRow(
        (m['id'] ?? '').toString(),
        (m['name'] ?? '').toString(),
        (m['description'] ?? '').toString(),
        (m['scope'] ?? '').toString(),
      );
}

/// The instructions behind one skill.
class SkillScreen extends StatefulWidget {
  const SkillScreen({
    super.key,
    required this.link,
    required this.body,
    required this.row,
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final SkillRow row;

  @override
  State<SkillScreen> createState() => _SkillScreenState();
}

class _SkillScreenState extends State<SkillScreen> {
  String _md = '';
  String _error = '';
  bool _cut = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final res = await widget.link
          .reach('POST', '/api/agent/skill', body: widget.body({'id': widget.row.id}));
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (res['ok'] == true) {
          _md = (res['markdown'] ?? '').toString();
          _cut = res['truncated'] == true;
        } else {
          _error = tr((res['detail'] ?? '').toString());
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = humanError(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.row.name, overflow: TextOverflow.ellipsis)),
      body: _loading
          ? const Waiting(lines: ['Buscando las instrucciones…'])
          : _error.isNotEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(_error, textAlign: TextAlign.center),
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (widget.row.description.isNotEmpty) ...[
                      Text(widget.row.description,
                          style: TextStyle(fontSize: 13, color: context.c.muted)),
                      const Divider(height: 28),
                    ],
                    Markdown(_md),
                    if (_cut) ...[
                      const SizedBox(height: 16),
                      Text(
                        tr('Son más largas de lo que cabe aquí; esto es el principio.'),
                        style: TextStyle(fontSize: 11, color: context.c.muted),
                      ),
                    ],
                  ],
                ),
    );
  }
}

/// What the agent is for. Read by anybody who may look; edited only with `config`.
class PurposeScreen extends StatefulWidget {
  const PurposeScreen({
    super.key,
    required this.link,
    required this.body,
    required this.markdown,
    required this.base,
    required this.canEdit,
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final String markdown;
  final String base;
  final bool canEdit;

  @override
  State<PurposeScreen> createState() => _PurposeScreenState();
}

class _PurposeScreenState extends State<PurposeScreen> {
  late final TextEditingController _text =
      TextEditingController(text: widget.markdown);
  bool _editing = false;
  bool _busy = false;
  String _error = '';
  String _saved = '';

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = '';
      _saved = '';
    });
    try {
      final res = await widget.link.reach('POST', '/api/agent/purpose',
          body: widget.body({'markdown': _text.text}), wait: Link.thinkingWait);
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() => _error = tr((res['detail'] ?? '').toString()));
        return;
      }
      setState(() {
        _editing = false;
        _saved = tr((res['detail'] ?? '').toString());
      });
    } catch (e) {
      if (mounted) setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Para qué es')),
        actions: [
          if (widget.canEdit && !_editing)
            IconButton(
              icon: const Icon(Icons.edit_outlined),
              tooltip: tr('Editar'),
              onPressed: () => setState(() => _editing = true),
            ),
          if (_editing)
            IconButton(
              icon: const Icon(Icons.check),
              tooltip: tr('Guardar'),
              onPressed: _busy ? null : _save,
            ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error.isNotEmpty)
            _note(context, _error, context.c.warnBg),
          if (_saved.isNotEmpty) _note(context, _saved, context.c.okBg),
          if (_editing)
            TextField(
              controller: _text,
              maxLines: null,
              minLines: 14,
              enabled: !_busy,
              style: const TextStyle(fontSize: 13, height: 1.45),
              decoration: InputDecoration(
                border: const OutlineInputBorder(),
                hintText: tr('Lo que este agente es y cómo debe comportarse.'),
              ),
            )
          else if (widget.markdown.trim().isEmpty)
            Text(tr('Todavía no tiene uno escrito.'),
                style: TextStyle(color: context.c.muted))
          else
            Markdown(_text.text),
          if (_editing) ...[
            const SizedBox(height: 12),
            Text(
              tr('Lo usa desde el siguiente mensaje: no hace falta reiniciar nada.'),
              style: TextStyle(fontSize: 11, color: context.c.muted),
            ),
            const SizedBox(height: 12),
            if (_busy) const Center(child: Busy(label: 'Guardando…')),
          ],
          if (!_editing && widget.base.trim().isNotEmpty) ...[
            const Divider(height: 36),
            Text(tr('Debajo de eso'),
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 6),
            Text(
              tr('Lo que trae Hermes de fábrica, igual para todos los agentes. No se '
                  'edita desde aquí.'),
              style: TextStyle(fontSize: 11, color: context.c.muted),
            ),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: context.c.panel2,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(widget.base,
                  style: TextStyle(fontSize: 12, color: context.c.muted)),
            ),
          ],
        ],
      ),
    );
  }

  Widget _note(BuildContext context, String text, Color bg) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.all(10),
        decoration:
            BoxDecoration(color: bg, borderRadius: BorderRadius.circular(10)),
        child: Text(text, style: TextStyle(fontSize: 12, color: context.c.ink)),
      );
}

/// Who else this agent may talk to, and the instructions it follows when it does.
class PeersScreen extends StatelessWidget {
  const PeersScreen({
    super.key,
    required this.agents,
    required this.how,
    required this.howName,
  });

  final List<Map<String, dynamic>> agents;
  final String how;
  final String howName;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(tr('Con quién habla'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (agents.isEmpty)
            Text(tr('Es el único agente de esta computadora.'),
                style: TextStyle(color: context.c.muted))
          else ...[
            Text(
              tr('Puede preguntarles algo y esperar su respuesta, sin que tú estés '
                  'delante.'),
              style: TextStyle(fontSize: 12, color: context.c.muted),
            ),
            const SizedBox(height: 12),
            for (final a in agents)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: CircleAvatar(
                  backgroundColor: context.c.panel2,
                  child: Text(
                    (a['name'] ?? '?').toString().characters.take(2).toString()
                        .toUpperCase(),
                    style: TextStyle(fontSize: 12, color: context.c.ink),
                  ),
                ),
                title: Text((a['name'] ?? '').toString()),
                subtitle: a['reachable'] == false
                    ? Text(tr('Ahora mismo no contesta'),
                        style: TextStyle(fontSize: 11, color: context.c.muted))
                    : null,
              ),
          ],
          if (how.trim().isNotEmpty) ...[
            const Divider(height: 36),
            Text(howName.isEmpty ? tr('Cómo les habla') : howName,
                style: TextStyle(
                    fontWeight: FontWeight.w700, color: context.c.muted, fontSize: 12)),
            const SizedBox(height: 10),
            Markdown(how),
          ],
        ],
      ),
    );
  }
}
