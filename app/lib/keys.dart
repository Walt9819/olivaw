// The keys an agent may use, managed from the phone.
//
// Same store as the console - the computer keeps one vault, and this is a second window
// onto it. What travels over this link is a password on its way IN, inside the pinned
// TLS session, and nothing on its way out: there is no route anywhere that returns a
// value, so a key can be added and replaced from here and never read, not by this
// screen, not by the agent, not by anybody holding this phone.
//
// Which is why the list shows that a key EXISTS and what it opens, and the password box
// is always empty: leaving it alone keeps what is stored. "Shown as dots" would be a
// value this phone had been sent; empty is a value it never had.

import 'package:flutter/material.dart';

import 'link.dart';
import 'loading.dart';
import 'strings.dart';
import 'theme.dart';

/// Sites people actually log into, as a starting point and nothing more.
///
/// A suggestion fills the address in; the box underneath stays a free text field,
/// because the whole point is that this works for the panel of a supplier nobody has
/// heard of as well as it does for Google.
const List<List<String>> kKnownSites = [
  ['google', 'Google', 'https://accounts.google.com/'],
  ['microsoft', 'Microsoft', 'https://login.microsoftonline.com/'],
  ['github', 'GitHub', 'https://github.com/login'],
  ['apple', 'Apple', 'https://account.apple.com/'],
  ['meta', 'Meta / Facebook', 'https://www.facebook.com/login'],
  ['instagram', 'Instagram', 'https://www.instagram.com/accounts/login/'],
  ['linkedin', 'LinkedIn', 'https://www.linkedin.com/login'],
  ['x', 'X', 'https://x.com/login'],
  ['notion', 'Notion', 'https://www.notion.so/login'],
  ['openai', 'OpenAI', 'https://platform.openai.com/login'],
];

class KeysScreen extends StatefulWidget {
  const KeysScreen({
    super.key,
    required this.link,
    required this.body,
    required this.agentName,
    this.canEdit = true,
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final String agentName;
  final bool canEdit;

  @override
  State<KeysScreen> createState() => _KeysScreenState();
}

class _KeysScreenState extends State<KeysScreen> {
  List<Map<String, dynamic>> _rows = [];
  String _keeper = '';
  bool _canKeep = true;
  int _perHour = 12;
  bool _loading = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final res = await widget.link
          .reach('POST', '/api/creds/list', body: widget.body());
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() => _error =
            tr((res['detail'] ?? tr('No pude leer las llaves.')).toString()));
        return;
      }
      setState(() {
        _rows = [
          for (final r in (res['credentials'] as List? ?? []))
            if (r is Map) r.cast<String, dynamic>(),
        ];
        _keeper = (res['keeper_label'] ?? '').toString();
        _canKeep = (res['keeper'] ?? '').toString().isNotEmpty;
        _perHour = (res['uses_per_hour'] as num?)?.toInt() ?? 12;
        _error = '';
      });
    } catch (e) {
      if (mounted) setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _forget(Map<String, dynamic> row) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(tr('¿Borrar esta llave?')),
        content: Text(trp('{0} dejará de poder entrar ahí.', [widget.agentName])),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false), child: Text(tr('No'))),
          TextButton(
              onPressed: () => Navigator.pop(c, true), child: Text(tr('Borrar'))),
        ],
      ),
    );
    if (yes != true) return;
    final res = await widget.link.reach('POST', '/api/creds/forget',
        body: widget.body({'platform': row['platform']}));
    if (!mounted) return;
    if (res['ok'] != true) {
      _say(tr((res['detail'] ?? tr('No pude borrarla.')).toString()));
    }
    _load();
  }

  Future<void> _allow(Map<String, dynamic> row) async {
    final res = await widget.link.reach('POST', '/api/creds/allow',
        body: widget.body({'platform': row['platform']}));
    if (!mounted) return;
    _say(res['ok'] == true
        ? tr('Permitida una vez, durante los próximos minutos.')
        : tr((res['detail'] ?? tr('No pude permitirla.')).toString()));
    _load();
  }

  void _say(String what) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(what)));
  }

  Future<void> _edit(Map<String, dynamic>? row) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      constraints:
          BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.92),
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (_) => _KeyForm(
          link: widget.link, body: widget.body, row: row,
          agentName: widget.agentName),
    );
    if (saved == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.c.bg,
      appBar: AppBar(title: Text(tr('Llaves y contraseñas'))),
      floatingActionButton: (!widget.canEdit || !_canKeep)
          ? null
          : FloatingActionButton.extended(
              onPressed: () => _edit(null),
              icon: const Icon(Icons.add),
              label: Text(tr('Añadir')),
            ),
      body: _loading && _rows.isEmpty
          ? const Center(child: Waiting(lines: ['Mirando qué llaves tiene…']))
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
              children: [
                if (_error.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(_error,
                        style: TextStyle(color: context.c.err, fontSize: 13)),
                  ),
                if (!_canKeep)
                  _note(context,
                      tr('Esa computadora no tiene dónde guardarlas a salvo, '
                          'así que no guarda ninguna.')),
                Text(
                  trp('Para que {0} pueda entrar en sitios por ti sin que la '
                      'contraseña pase nunca por la conversación.',
                      [widget.agentName]),
                  style: TextStyle(color: context.c.muted, fontSize: 13),
                ),
                const SizedBox(height: 14),
                if (_rows.isEmpty && !_loading)
                  _note(context, tr('Todavía no le has dejado ninguna llave.'))
                else
                  for (final r in _rows) _tile(context, r),
                const SizedBox(height: 18),
                Text(
                  trp('Se guardan en el llavero de la computadora ({0}). Las '
                      'escribe en la página él solo, como mucho {1} veces por '
                      'hora, y sólo en el sitio de cada una. Nadie puede verlas '
                      'después: ni él, ni esta app, ni tú.',
                      [_keeper.isEmpty ? '—' : _keeper, _perHour]),
                  style: TextStyle(color: context.c.muted, fontSize: 11),
                ),
              ],
            ),
    );
  }

  Widget _note(BuildContext context, String text) => Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
            color: context.c.panel2,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: context.c.line)),
        child: Text(text, style: TextStyle(color: context.c.muted, fontSize: 13)),
      );

  Widget _tile(BuildContext context, Map<String, dynamic> r) {
    final fields = [for (final f in (r['fields'] as List? ?? [])) f.toString()];
    final allowed = ((r['allowed_until'] as num?)?.toInt() ?? 0) * 1000 >
        DateTime.now().millisecondsSinceEpoch;
    final bits = <String>[];
    if ((r['username'] ?? '').toString().isNotEmpty) {
      bits.add(r['username'].toString());
    } else if (r['secret_username'] == true) {
      bits.add(tr('cuenta guardada'));
    }
    if ((r['url'] ?? '').toString().isNotEmpty) {
      bits.add(r['url'].toString().replaceFirst(RegExp(r'^https?://'), ''));
    }
    // What it holds, never what it is: "guarda contraseña" is the most this screen
    // ever says about a value.
    bits.add(trp('guarda {0}', [fields.join(', ')]));
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
          color: context.c.panel,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: context.c.line)),
      child: Column(children: [
        ListTile(
          leading: Icon(Icons.vpn_key_outlined, color: context.c.accent),
          title: Text((r['label'] ?? r['platform'] ?? '').toString(),
              style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Text(bits.join(' · '),
              style: TextStyle(fontSize: 12, color: context.c.muted)),
          trailing: widget.canEdit
              ? IconButton(
                  icon: const Icon(Icons.edit_outlined),
                  onPressed: () => _edit(r))
              : null,
        ),
        if (widget.canEdit)
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 0, 8, 6),
            child: Row(children: [
              if (r['confirm'] == true)
                allowed
                    ? Padding(
                        padding: const EdgeInsets.only(left: 8),
                        child: Text(tr('permitida ahora'),
                            style: TextStyle(
                                fontSize: 12, color: context.c.accent)),
                      )
                    : TextButton(
                        onPressed: () => _allow(r),
                        child: Text(tr('Permitir una vez')),
                      ),
              const Spacer(),
              TextButton(
                onPressed: () => _forget(r),
                child: Text(tr('Borrar'),
                    style: TextStyle(color: context.c.err)),
              ),
            ]),
          ),
      ]),
    );
  }
}

/// Adding or replacing one. The password box starts empty and stays empty.
class _KeyForm extends StatefulWidget {
  const _KeyForm({
    required this.link,
    required this.body,
    required this.row,
    required this.agentName,
  });

  final Link link;
  final Map<String, dynamic> Function([Map<String, dynamic>?]) body;
  final Map<String, dynamic>? row;
  final String agentName;

  @override
  State<_KeyForm> createState() => _KeyFormState();
}

class _KeyFormState extends State<_KeyForm> {
  late final TextEditingController _plat =
      TextEditingController(text: (widget.row?['platform'] ?? '').toString());
  late final TextEditingController _label =
      TextEditingController(text: (widget.row?['label'] ?? '').toString());
  late final TextEditingController _url =
      TextEditingController(text: (widget.row?['url'] ?? '').toString());
  late final TextEditingController _user =
      TextEditingController(text: (widget.row?['username'] ?? '').toString());
  final _pass = TextEditingController();
  final _totp = TextEditingController();
  late bool _confirm = widget.row?['confirm'] == true;
  bool _saving = false;
  String _error = '';

  bool get _known => widget.row != null;

  @override
  void dispose() {
    // Out of memory as soon as the screen goes, which is the most a phone can do
    // about a value it had to hold for the length of one form.
    _pass.text = '';
    _totp.text = '';
    for (final c in [_plat, _label, _url, _user, _pass, _totp]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final platform = _plat.text.trim();
    if (platform.isEmpty) {
      setState(() => _error = tr('¿De qué sitio es?'));
      return;
    }
    if (!_known && _pass.text.isEmpty) {
      setState(() => _error = tr('Escribe la contraseña que quieres guardar.'));
      return;
    }
    setState(() {
      _saving = true;
      _error = '';
    });
    try {
      final res = await widget.link.reach('POST', '/api/creds/save',
          body: widget.body({
            'platform': platform,
            'label': _label.text.trim(),
            'url': _url.text.trim(),
            'username': _user.text.trim(),
            'password': _pass.text,
            'totp': _totp.text.trim(),
            'confirm': _confirm,
          }));
      // Cleared the moment it is handed over, whatever the answer was.
      _pass.text = '';
      _totp.text = '';
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() => _error =
            tr((res['detail'] ?? tr('No pude guardarla.')).toString()));
        return;
      }
      Navigator.pop(context, true);
    } catch (e) {
      if (mounted) setState(() => _error = humanError(e));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
            18, 16, 18, 16 + MediaQuery.of(context).viewInsets.bottom),
        child: ListView(shrinkWrap: true, children: [
          Text(_known ? tr('Cambiar esta llave') : tr('Añadir una llave'),
              style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: context.c.ink)),
          const SizedBox(height: 12),
          if (!_known) ...[
            Text(tr('¿De qué sitio?'),
                style: TextStyle(fontSize: 12, color: context.c.muted)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final site in kKnownSites)
                  ActionChip(
                    label: Text(site[1]),
                    onPressed: () => setState(() {
                      _plat.text = site[0];
                      if (_label.text.trim().isEmpty) _label.text = site[1];
                      _url.text = site[2];
                    }),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(tr('…o escribe cualquier otro abajo: sirve para cualquier sitio '
                'con usuario y contraseña.'),
                style: TextStyle(fontSize: 11, color: context.c.muted)),
            const SizedBox(height: 10),
          ],
          _field(tr('Sitio (una palabra)'), _plat,
              hint: 'google, github, banco', enabled: !_known),
          _field(tr('Cómo la llamas'), _label, hint: tr('Google de trabajo')),
          _field(tr('Página de acceso'), _url,
              hint: 'https://accounts.google.com/',
              help: tr('La llave sólo se escribe en este sitio. Es lo que impide '
                  'que una página falsa se la lleve.')),
          _field(tr('Usuario o correo'), _user, hint: 'tu@correo.com'),
          _field(
              _known ? tr('Contraseña nueva') : tr('Contraseña'), _pass,
              secret: true,
              help: _known
                  ? tr('Déjala vacía si no la cambias. No se puede ver la que hay.')
                  : tr('Se guarda en el llavero de la computadora. Nadie la ve '
                      'después, tampoco tú.')),
          _field(tr('Clave de 2 pasos (opcional)'), _totp,
              secret: true,
              help: tr('La clave larga que da el sitio al activar la verificación '
                  'en dos pasos. Con ella escribe los 6 dígitos él solo.')),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _confirm,
            onChanged: (v) => setState(() => _confirm = v),
            title: Text(tr('Pedirme permiso cada vez'),
                style: const TextStyle(fontSize: 14)),
            subtitle: Text(
                tr('Te lo pedirá por su canal, y tú lo das aquí con «Permitir '
                    'una vez».'),
                style: TextStyle(fontSize: 11, color: context.c.muted)),
          ),
          if (_error.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child:
                  Text(_error, style: TextStyle(color: context.c.err, fontSize: 13)),
            ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(_saving ? tr('Guardando…') : tr('Guardar')),
          ),
          TextButton(
            onPressed: _saving ? null : () => Navigator.pop(context, false),
            child: Text(tr('Cancelar')),
          ),
        ]),
      ),
    );
  }

  Widget _field(String label, TextEditingController c,
      {String hint = '', String help = '', bool secret = false,
      bool enabled = true}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: TextStyle(fontSize: 12, color: context.c.muted)),
        const SizedBox(height: 4),
        TextField(
          controller: c,
          enabled: enabled,
          obscureText: secret,
          autocorrect: false,
          enableSuggestions: false,
          style: TextStyle(color: context.c.ink),
          decoration: InputDecoration(
            isDense: true,
            hintText: hint,
            hintStyle: TextStyle(color: context.c.muted),
            filled: true,
            fillColor: context.c.panel2,
            border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: context.c.line)),
            enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: context.c.line)),
          ),
        ),
        if (help.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(help,
                style: TextStyle(fontSize: 11, color: context.c.muted)),
          ),
      ]),
    );
  }
}
