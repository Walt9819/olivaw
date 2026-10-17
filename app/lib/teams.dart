// Teams, and how much work has actually been done.
//
// The roster answers "which agents are there". Neither of the two questions underneath
// it had anywhere to live: who works with whom, and who has been doing the work. The
// first is the owner's own grouping and already exists in the team map; the second is
// in Hermes' session rows, which it keeps the same way whichever brain is behind the
// bridge - so counting Claude Code and Codex needs no special case, and the engine is a
// label on the AGENT rather than something inferred from a session.
//
// Both screens read one request (`stats/usage`), which already carries the teams, their
// members and their icons alongside the numbers.

import 'package:flutter/material.dart';

import 'link.dart';
import 'loading.dart';
import 'main.dart' show Agent, Face;
import 'strings.dart';
import 'theme.dart';

/// 85197147 -> "85,2 M". Tokens run to hundreds of millions and the exact digit has
/// never once been the point; what a reader wants is the order of magnitude and a
/// number they can compare to the row above.
String compact(num n) {
  if (n < 1000) return '$n';
  if (n < 1000000) {
    final v = n / 1000;
    return '${v.toStringAsFixed(v >= 100 ? 0 : 1).replaceAll('.', ',')} k';
  }
  final v = n / 1000000;
  return '${v.toStringAsFixed(v >= 100 ? 0 : 1).replaceAll('.', ',')} M';
}

/// "hace 3 días", or empty when nothing ever happened.
String since(num epochSeconds) {
  if (epochSeconds <= 0) return '';
  final d = DateTime.now()
      .difference(DateTime.fromMillisecondsSinceEpoch((epochSeconds * 1000).round()));
  if (d.inMinutes < 60) return trp('hace {0} min', [d.inMinutes.clamp(1, 59)]);
  if (d.inHours < 24) return trp('hace {0} h', [d.inHours]);
  return trp('hace {0} d', [d.inDays]);
}

/// One fetch of `stats/usage`, shared by both screens.
class Usage {
  Usage(this.agents, this.teams, this.unassigned, this.days, this.costed);

  final List<Map<String, dynamic>> agents;
  final List<Map<String, dynamic>> teams;
  final List<String> unassigned;
  final int days;
  final bool costed;

  static Usage from(Map<String, dynamic> res) => Usage(
        ((res['agents'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList(),
        ((res['teams'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList(),
        ((res['unassigned'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        (res['days'] as num?)?.toInt() ?? 30,
        res['costed'] == true,
      );

  Map<String, dynamic>? agent(String slug) {
    for (final a in agents) {
      if (a['slug'] == slug) return a;
    }
    return null;
  }
}

/// The teams, who is on them, and what they have been doing.
class TeamsTab extends StatefulWidget {
  const TeamsTab({
    super.key,
    required this.link,
    required this.agents,
    required this.onOpenAgent,
  });

  final Link link;
  final List<Agent> agents;
  final void Function(Agent) onOpenAgent;

  @override
  State<TeamsTab> createState() => _TeamsTabState();
}

class _TeamsTabState extends State<TeamsTab> {
  Usage? _usage;
  String _error = '';
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (_usage == null) setState(() => _loading = true);
    try {
      final res = await widget.link
          .reach('POST', '/api/stats/usage', body: {'days': 30});
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() {
          _loading = false;
          _error = tr((res['detail'] ?? '').toString());
        });
        return;
      }
      setState(() {
        _usage = Usage.from(res);
        _loading = false;
        _error = '';
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          if (_usage == null) _error = humanError(e);
        });
      }
    }
  }

  Agent? _agentFor(String slug) {
    for (final a in widget.agents) {
      if (a.slug == slug) return a;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final u = _usage;
    if (_loading && u == null) {
      return const Waiting(lines: ['Mirando los equipos…']);
    }
    if (u == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(_error.isEmpty ? tr('Sin respuesta') : _error,
              textAlign: TextAlign.center),
        ),
      );
    }
    if (u.teams.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            tr('Todavía no has agrupado a nadie. Los equipos se crean en la '
                'computadora, en «El equipo».'),
            textAlign: TextAlign.center,
            style: TextStyle(color: context.c.muted),
          ),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          for (final t in u.teams) _card(context, t, u),
          if (u.unassigned.isNotEmpty) _loose(context, u),
        ],
      ),
    );
  }

  Widget _card(BuildContext context, Map<String, dynamic> t, Usage u) {
    final members = ((t['members'] as List?) ?? const [])
        .map((e) => e.toString())
        .toList();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: context.c.panel,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: context.c.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            TeamMark(icon: (t['icon'] ?? '').toString(),
                name: (t['name'] ?? '').toString(), size: 34),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text((t['name'] ?? '').toString(),
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.w700)),
                  Text(
                    members.length == 1
                        ? tr('1 agente')
                        : trp('{0} agentes', [members.length]),
                    style: TextStyle(fontSize: 11, color: context.c.muted),
                  ),
                ],
              ),
            ),
          ]),
          const SizedBox(height: 12),
          // The faces, because a team is its people and a list of slugs is not.
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final slug in members)
                if (_agentFor(slug) != null)
                  InkWell(
                    onTap: () => widget.onOpenAgent(_agentFor(slug)!),
                    borderRadius: BorderRadius.circular(20),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Face(agent: _agentFor(slug)!, size: 26, link: widget.link),
                      const SizedBox(width: 6),
                      Text(_agentFor(slug)!.name,
                          style: const TextStyle(fontSize: 12)),
                      const SizedBox(width: 6),
                    ]),
                  ),
            ],
          ),
          const Divider(height: 24),
          UsageLine(row: t, days: u.days, costed: u.costed),
        ],
      ),
    );
  }

  Widget _loose(BuildContext context, Usage u) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: context.c.panel2,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: context.c.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(tr('Sin equipo'),
              style: const TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(
            tr('Trabajan igual que los demás; sólo no están agrupados.'),
            style: TextStyle(fontSize: 11, color: context.c.muted),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final slug in u.unassigned)
                if (_agentFor(slug) != null)
                  InkWell(
                    onTap: () => widget.onOpenAgent(_agentFor(slug)!),
                    borderRadius: BorderRadius.circular(20),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Face(agent: _agentFor(slug)!, size: 26, link: widget.link),
                      const SizedBox(width: 6),
                      Text(_agentFor(slug)!.name,
                          style: const TextStyle(fontSize: 12)),
                      const SizedBox(width: 6),
                    ]),
                  ),
            ],
          ),
        ],
      ),
    );
  }
}

/// A team's mark: its emoji, or the first letter of its name.
class TeamMark extends StatelessWidget {
  const TeamMark({super.key, required this.icon, required this.name, this.size = 24});

  final String icon;
  final String name;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: context.c.accent.withValues(alpha: 0.18),
        shape: BoxShape.circle,
      ),
      child: Text(
        icon.isNotEmpty
            ? icon
            : (name.isEmpty ? '?' : name.characters.first.toUpperCase()),
        style: TextStyle(
            fontSize: size * (icon.isNotEmpty ? 0.5 : 0.42),
            height: 1.1,
            fontWeight: FontWeight.w700,
            color: context.c.accent),
      ),
    );
  }
}

/// Sessions, messages, tool calls and tokens, on one line.
class UsageLine extends StatelessWidget {
  const UsageLine({
    super.key,
    required this.row,
    required this.days,
    required this.costed,
  });

  final Map<String, dynamic> row;
  final int days;
  final bool costed;

  int _n(String k) => (row[k] as num?)?.toInt() ?? 0;

  @override
  Widget build(BuildContext context) {
    final last = since((row['last_active'] as num?) ?? 0);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(spacing: 14, runSpacing: 6, children: [
          _Stat(label: tr('Conversaciones'), value: compact(_n('sessions'))),
          _Stat(label: tr('Mensajes'), value: compact(_n('message_count'))),
          _Stat(label: tr('Herramientas'), value: compact(_n('tool_call_count'))),
          _Stat(
              label: tr('Tokens'),
              value: compact(_n('input_tokens') + _n('output_tokens'))),
          if (costed && ((row['cost_usd'] as num?) ?? 0) > 0)
            _Stat(
                label: tr('Coste'),
                value: '\$${((row['cost_usd'] as num).toDouble()).toStringAsFixed(2)}'),
        ]),
        if (last.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(trp('Último movimiento {0}', [last]),
              style: TextStyle(fontSize: 11, color: context.c.muted)),
        ],
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label, value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(value,
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
        Text(label, style: TextStyle(fontSize: 10, color: context.c.muted)),
      ],
    );
  }
}

/// Everything, by agent, over a window you can change.
class UsageTab extends StatefulWidget {
  const UsageTab({super.key, required this.link, required this.agents});

  final Link link;
  final List<Agent> agents;

  @override
  State<UsageTab> createState() => _UsageTabState();
}

class _UsageTabState extends State<UsageTab> {
  Usage? _usage;
  String _error = '';
  bool _loading = true;
  int _days = 30;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (_usage == null) setState(() => _loading = true);
    try {
      final res = await widget.link
          .reach('POST', '/api/stats/usage', body: {'days': _days});
      if (!mounted) return;
      if (res['ok'] != true) {
        setState(() {
          _loading = false;
          _error = tr((res['detail'] ?? '').toString());
        });
        return;
      }
      setState(() {
        _usage = Usage.from(res);
        _loading = false;
        _error = '';
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          if (_usage == null) _error = humanError(e);
        });
      }
    }
  }

  Agent? _agentFor(String slug) {
    for (final a in widget.agents) {
      if (a.slug == slug) return a;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final u = _usage;
    if (_loading && u == null) {
      return const Waiting(lines: ['Contando el trabajo…', 'Un agente por vez.']);
    }
    if (u == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(_error.isEmpty ? tr('Sin respuesta') : _error,
              textAlign: TextAlign.center),
        ),
      );
    }
    // Busiest first: a list of seven sorted by name makes you read all of it to find
    // out who is doing the work.
    final rows = [...u.agents]..sort((a, b) =>
        ((b['input_tokens'] as num?) ?? 0)
            .compareTo((a['input_tokens'] as num?) ?? 0));
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          Row(children: [
            Expanded(
              child: Text(tr('Los últimos'),
                  style: TextStyle(fontSize: 12, color: context.c.muted)),
            ),
            for (final d in const [7, 30, 90])
              Padding(
                padding: const EdgeInsets.only(left: 6),
                child: ChoiceChip(
                  label: Text(trp('{0} d', [d]),
                      style: const TextStyle(fontSize: 12)),
                  selected: _days == d,
                  onSelected: (_) {
                    setState(() => _days = d);
                    _load();
                  },
                ),
              ),
          ]),
          const SizedBox(height: 6),
          if (!u.costed)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Text(
                // Said rather than left as a zero beside two hundred million tokens,
                // which reads as free instead of as not-measured.
                tr('Con una suscripción no hay precio por token, así que esto mide '
                    'trabajo hecho, no dinero.'),
                style: TextStyle(fontSize: 11, color: context.c.muted),
              ),
            ),
          for (final a in rows) _agentCard(context, a, u),
        ],
      ),
    );
  }

  Widget _agentCard(BuildContext context, Map<String, dynamic> a, Usage u) {
    final agent = _agentFor((a['slug'] ?? '').toString());
    final known = a['known'] == true;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: context.c.panel,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: context.c.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            if (agent != null) ...[
              Face(agent: agent, size: 28, link: widget.link),
              const SizedBox(width: 9),
            ],
            Expanded(
              child: Text((a['name'] ?? a['slug'] ?? '').toString(),
                  style: const TextStyle(fontWeight: FontWeight.w700)),
            ),
            // The brain, from Olivaw's own registry. A number is only worth labelling
            // if the label is the thing that actually produced it.
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: context.c.panel2,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: context.c.line),
              ),
              child: Text((a['engine'] ?? '').toString(),
                  style: TextStyle(fontSize: 10, color: context.c.muted)),
            ),
          ]),
          const SizedBox(height: 12),
          if (!known)
            Text(
              // Not zero. Nobody measured this one.
              tr('Sin datos: su canal no está encendido ahora mismo.'),
              style: TextStyle(fontSize: 11, color: context.c.muted),
            )
          else
            UsageLine(row: a, days: u.days, costed: u.costed),
          if (known && (a['by_source'] as Map?)?.isNotEmpty == true) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final e in (a['by_source'] as Map).entries)
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: context.c.panel2,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text('${_whereFrom(e.key.toString())} ${e.value}',
                        style:
                            TextStyle(fontSize: 10, color: context.c.muted)),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  /// Hermes' own words for where a conversation came from, in the owner's.
  String _whereFrom(String source) {
    switch (source) {
      case 'telegram':
        return tr('Telegram');
      case 'cron':
        return tr('Rutinas');
      case 'cli':
        return tr('Consola');
      case 'api_server':
        return tr('Esta app');
      default:
        return source;
    }
  }
}
