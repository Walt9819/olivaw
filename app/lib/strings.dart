// Spanish, English and German.
//
// The Spanish IS the key. `tr('No encuentro tu ordenador…')` looks the sentence up and
// returns the English or German one when the phone is set to those, and the Spanish
// itself when it is set to anything else or when a translation is missing. Two reasons
// for that shape rather than symbolic keys like `S.computerNotFound`:
//
//   * the call site still reads as the sentence it will show, which is how you notice
//     that a message is wrong or rude or too long, and
//   * a missing translation degrades to Spanish rather than to "computerNotFound".
//
// Spanish is also the default for an unrecognised locale, not English: this is built for
// a Spanish-speaking owner first, and a phone in Portuguese or French belongs nearer to
// Spanish than to English.
//
// The variant is Latin American: "ordenador" would be the Spain word, and the strings
// here say "computadora" where the two differ... except they mostly do not, and the
// existing copy already reads neutrally. Where a choice had to be made it went the LATAM
// way - "celular" over "móvil", "computadora" over "ordenador".
//
// NOT translated, deliberately: the two-word pairing code. Those words are compared
// against what the computer shows, and the computer's list is in selfcert.py in Spanish.
// Translating one side would make every honest pairing look like an impostor.

import 'dart:ui' show PlatformDispatcher;

enum Lang { es, en, de }

Lang _lang = Lang.es;
Lang get lang => _lang;

/// Pick the language from the phone, once, at startup.
void chooseLanguage() {
  final code = PlatformDispatcher.instance.locale.languageCode.toLowerCase();
  _lang = switch (code) {
    'en' => Lang.en,
    'de' => Lang.de,
    _ => Lang.es,
  };
}

/// For tests, and for a future "idioma" setting.
void setLanguage(Lang l) => _lang = l;

/// The sentence, in whatever language this phone is in.
String tr(String es) {
  if (_lang == Lang.es) return es;
  final row = _table[es];
  if (row == null) return es; // untranslated: Spanish beats a key name
  return (_lang == Lang.en ? row.$1 : row.$2);
}

/// The same, with `{0}`, `{1}` … filled in afterwards, so a translation can put the
/// pieces in a different order than Spanish does.
String trp(String es, List<Object?> args) {
  var out = tr(es);
  for (var i = 0; i < args.length; i++) {
    out = out.replaceAll('{$i}', '${args[i]}');
  }
  return out;
}

/// (English, German) for each Spanish sentence.
const Map<String, (String, String)> _table = {
  // ── linking ──────────────────────────────────────────────────────────────────────
  'Enlazar tu computadora': ('Link your computer', 'Deinen Computer verbinden'),
  'Buscando en tu wifi…': ('Looking on your wifi…', 'Im WLAN suchen…'),
  'Buscar otra vez': ('Look again', 'Erneut suchen'),
  'Dirección de la computadora': ('Computer address', 'Adresse des Computers'),
  'Código de seis cifras': ('Six-digit code', 'Sechsstelliger Code'),
  'Enlazar': ('Link', 'Verbinden'),
  'En la computadora, abre Olivaw y ve a «Tu teléfono» → «Enlazar un teléfono». '
      'Te dará una dirección y un código.': (
    'On the computer, open Olivaw and go to «Tu teléfono» → «Enlazar un teléfono». '
        'It will give you an address and a code.',
    'Öffne Olivaw auf dem Computer und gehe zu «Tu teléfono» → «Enlazar un teléfono». '
        'Dort bekommst du eine Adresse und einen Code.'
  ),
  '¿Dice esto tu computadora?': (
    'Does your computer say this?',
    'Steht das auf deinem Computer?'
  ),
  'En la pantalla de la computadora, debajo del código, hay dos palabras. '
      'Tienen que ser exactamente estas:': (
    'On the computer screen, under the code, there are two words. '
        'They must be exactly these:',
    'Auf dem Bildschirm des Computers stehen unter dem Code zwei Wörter. '
        'Sie müssen genau diese sein:'
  ),
  'Si no coinciden, no continúes: no estarías hablando con tu computadora.': (
    'If they do not match, stop: you would not be talking to your computer.',
    'Wenn sie nicht übereinstimmen, brich ab: dann sprichst du nicht mit deinem Computer.'
  ),
  'No coinciden': ('They do not match', 'Stimmen nicht'),
  'Son estas': ('That is them', 'Genau diese'),
  'No pude ver el certificado de la computadora.': (
    'I could not see the computer\'s certificate.',
    'Ich konnte das Zertifikat des Computers nicht sehen.'
  ),
  'Esa computadora presentó un certificado distinto. No he enviado nada. '
      'Vuelve a enlazar el teléfono desde la computadora.': (
    'That computer presented a different certificate. I sent nothing. '
        'Link the phone again from the computer.',
    'Dieser Computer hat ein anderes Zertifikat gezeigt. Es wurde nichts gesendet. '
        'Verbinde das Telefon erneut vom Computer aus.'
  ),

  // ── the roster ───────────────────────────────────────────────────────────────────
  'Tus agentes': ('Your agents', 'Deine Agenten'),
  'encendido': ('on', 'an'),
  'apagado': ('off', 'aus'),
  'Reintentar': ('Try again', 'Erneut versuchen'),
  'Si estás fuera de casa, tu computadora necesita tener configurado '
      'el punto de encuentro en «Tu teléfono».': (
    'If you are away from home, your computer needs a meeting point set up '
        'under «Tu teléfono».',
    'Wenn du unterwegs bist, braucht dein Computer einen eingerichteten Treffpunkt '
        'unter «Tu teléfono».'
  ),

  // ── talking ──────────────────────────────────────────────────────────────────────
  'Hablar': ('Talk', 'Sprechen'),
  'Rutinas': ('Routines', 'Routinen'),
  'Navegador': ('Browser', 'Browser'),
  'Escribe…': ('Write…', 'Schreiben…'),
  'No disponible': ('Not available', 'Nicht verfügbar'),
  'escribiendo…': ('typing…', 'schreibt…'),
  'Todavía no os habéis dicho nada.': (
    'You have not said anything to each other yet.',
    'Ihr habt euch noch nichts gesagt.'
  ),
  'Todavía no está activado para este agente.': (
    'Not switched on for this agent yet.',
    'Für diesen Agenten noch nicht aktiviert.'
  ),
  'No se pudo abrir la conversación.': (
    'The conversation could not be opened.',
    'Das Gespräch konnte nicht geöffnet werden.'
  ),
  'No se pudo leer la conversación.': (
    'The conversation could not be read.',
    'Das Gespräch konnte nicht gelesen werden.'
  ),
  'La computadora no abrió ninguna conversación.': (
    'The computer did not open a conversation.',
    'Der Computer hat kein Gespräch geöffnet.'
  ),
  'Sin respuesta': ('No answer', 'Keine Antwort'),
  '(sin respuesta)': ('(no answer)', '(keine Antwort)'),
  'Adjuntar': ('Attach', 'Anhängen'),
  'Foto o imagen': ('Photo or image', 'Foto oder Bild'),
  'Archivo': ('File', 'Datei'),
  'Diez archivos por mensaje es el máximo.': (
    'Ten files per message is the maximum.',
    'Zehn Dateien pro Nachricht sind das Maximum.'
  ),

  // ── voice notes ──────────────────────────────────────────────────────────────────
  'Nota de voz': ('Voice note', 'Sprachnachricht'),
  'Grabando': ('Recording', 'Aufnahme'),
  'Descartar': ('Discard', 'Verwerfen'),
  'Terminar': ('Finish', 'Beenden'),
  'Transcribiendo…': ('Transcribing…', 'Transkribieren…'),
  'Demasiado corto.': ('Too short.', 'Zu kurz.'),
  'No entendí nada en esa nota.': (
    'I did not understand anything in that note.',
    'Ich habe in dieser Nachricht nichts verstanden.'
  ),
  'No pude transcribir la nota.': (
    'I could not transcribe the note.',
    'Ich konnte die Nachricht nicht transkribieren.'
  ),
  'No pude grabar.': ('I could not record.', 'Ich konnte nicht aufnehmen.'),
  'Concede el permiso del micrófono y vuelve a pulsar.': (
    'Grant the microphone permission and press again.',
    'Erlaube das Mikrofon und drücke erneut.'
  ),

  // ── files ────────────────────────────────────────────────────────────────────────
  'Guardar': ('Save', 'Speichern'),
  'Guardado en {0}': ('Saved to {0}', 'Gespeichert unter {0}'),
  'No pude guardarlo.': ('I could not save it.', 'Ich konnte es nicht speichern.'),
  'No pude traer ese archivo.': (
    'I could not fetch that file.',
    'Ich konnte diese Datei nicht holen.'
  ),
  'Ese archivo llegó vacío.': ('That file arrived empty.', 'Diese Datei kam leer an.'),
  'No pude abrir el selector.': (
    'I could not open the picker.',
    'Ich konnte die Auswahl nicht öffnen.'
  ),
  'ya no está en la computadora': (
    'no longer on the computer',
    'nicht mehr auf dem Computer'
  ),
  'en la computadora': ('on the computer', 'auf dem Computer'),

  // ── past conversations ───────────────────────────────────────────────────────────
  'Conversaciones anteriores': ('Past conversations', 'Frühere Gespräche'),
  'Todavía no hay conversaciones anteriores.': (
    'There are no past conversations yet.',
    'Es gibt noch keine früheren Gespräche.'
  ),
  'No pude leer las conversaciones.': (
    'I could not read the conversations.',
    'Ich konnte die Gespräche nicht lesen.'
  ),
  'Sin título': ('Untitled', 'Ohne Titel'),
  'hace {0} min': ('{0} min ago', 'vor {0} Min.'),
  'hace {0} h': ('{0} h ago', 'vor {0} Std.'),
  'hace {0} d': ('{0} d ago', 'vor {0} T.'),
  '{0} mensajes': ('{0} messages', '{0} Nachrichten'),

  // ── routines ─────────────────────────────────────────────────────────────────────
  'Este agente no hace nada por su cuenta todavía.': (
    'This agent does nothing on its own yet.',
    'Dieser Agent macht noch nichts von selbst.'
  ),
  'No se pudo': ('It did not work', 'Hat nicht geklappt'),

  // ── the browser ──────────────────────────────────────────────────────────────────
  'Toma el control para navegar': (
    'Take control to browse',
    'Übernimm die Steuerung zum Surfen'
  ),
  'Ir a…': ('Go to…', 'Gehe zu…'),
  'Tomar el control': ('Take control', 'Steuerung übernehmen'),
  'Soltar el control ({0}s)': ('Release control ({0}s)', 'Steuerung abgeben ({0}s)'),
  'Esperando la primera imagen…': (
    'Waiting for the first picture…',
    'Warte auf das erste Bild…'
  ),
  'Escribir': ('Type', 'Tippen'),
  'Escribir en la página': ('Type on the page', 'Auf der Seite tippen'),
  'Cancelar': ('Cancel', 'Abbrechen'),
  'El control no está activo.': (
    'Control is not active.',
    'Die Steuerung ist nicht aktiv.'
  ),

  // ── things going wrong ───────────────────────────────────────────────────────────
  'No encuentro tu computadora. Comprueba que está encendida y que Olivaw '
      'está abierto, y que «Tu teléfono» sigue activado.': (
    'I cannot find your computer. Check that it is on, that Olivaw is open, '
        'and that «Tu teléfono» is still switched on.',
    'Ich finde deinen Computer nicht. Prüfe, ob er an ist, ob Olivaw läuft '
        'und ob «Tu teléfono» noch aktiviert ist.'
  ),
  'La computadora tardó demasiado en responder.': (
    'The computer took too long to answer.',
    'Der Computer hat zu lange gebraucht.'
  ),
  'La computadora respondió algo que no entendí.': (
    'The computer answered something I did not understand.',
    'Der Computer hat etwas geantwortet, das ich nicht verstanden habe.'
  ),
  'Respuesta incompleta de la computadora.': (
    'Incomplete answer from the computer.',
    'Unvollständige Antwort vom Computer.'
  ),
  'Respuesta ilegible.': ('Unreadable answer.', 'Unlesbare Antwort.'),
  'Algo no ha salido bien.': ('Something went wrong.', 'Etwas ist schiefgelaufen.'),
  'Este teléfono ya no está enlazado con esa computadora.': (
    'This phone is no longer linked to that computer.',
    'Dieses Telefon ist nicht mehr mit diesem Computer verbunden.'
  ),
  'Ese permiso no está activado para este teléfono.': (
    'That permission is not switched on for this phone.',
    'Diese Berechtigung ist für dieses Telefon nicht aktiviert.'
  ),
  'Esta computadora no tiene punto de encuentro configurado.': (
    'This computer has no meeting point set up.',
    'Für diesen Computer ist kein Treffpunkt eingerichtet.'
  ),
  'Solo se abren enlaces http y https.': (
    'Only http and https links are opened.',
    'Es werden nur http- und https-Links geöffnet.'
  ),
  'No pude abrir ese enlace.': (
    'I could not open that link.',
    'Ich konnte diesen Link nicht öffnen.'
  ),
  'No pude conectar: {0}': ('I could not connect: {0}', 'Verbindung fehlgeschlagen: {0}'),
  'No pude subir {0}.': ('I could not upload {0}.', 'Ich konnte {0} nicht hochladen.'),
  'La computadora no abrió el vídeo: {0}': (
    'The computer did not open the video: {0}',
    'Der Computer hat das Video nicht geöffnet: {0}'
  ),
  'Conversación nueva': ('New conversation', 'Neues Gespräch'),
  'Enviando…': ('Sending…', 'Senden…'),
  'Ver transcripción': ('Show transcript', 'Transkript anzeigen'),
  'Ocultar transcripción': ('Hide transcript', 'Transkript ausblenden'),
  'Ajustes del agente': ('Agent settings', 'Agenten-Einstellungen'),
  'La cara': ('The face', 'Das Gesicht'),
  'Foto': ('Photo', 'Foto'),
  'Símbolo': ('Symbol', 'Symbol'),
  'Iniciales': ('Initials', 'Initialen'),
  'El nombre': ('The name', 'Der Name'),
  'Guardar el nombre': ('Save the name', 'Namen speichern'),
  'Cambiarlo aquí también cambia cómo se presenta el agente.': (
    'Changing it here also changes how the agent introduces itself.',
    'Das ändert auch, wie sich der Agent vorstellt.'
  ),
  'Hablar con él': ('Talking to it', 'Mit ihm sprechen'),
  'Conversaciones desde aquí': ('Conversations from here', 'Gespräche von hier'),
  'Abre el canal del agente en la computadora, solo para ella.': (
    'Opens the agent channel on the computer, for it alone.',
    'Öffnet den Kanal des Agenten auf dem Computer, nur für ihn.'
  ),
  'Buscar': ('Search', 'Suchen'),
  'Buscar en la conversación': ('Search in the conversation', 'Im Gespräch suchen'),
  'Nada': ('None', 'Nichts'),
  'Fotos y archivos': ('Photos and files', 'Fotos und Dateien'),
  'Fotos': ('Photos', 'Fotos'),
  'Archivos': ('Files', 'Dateien'),
  'Notas': ('Notes', 'Notizen'),
  'Nada por aquí todavía.': ('Nothing here yet.', 'Hier ist noch nichts.'),

  // Sentences the COMPUTER writes. It speaks Spanish and always will - the
  // console is Spanish - but the Spanish is the key here, so its messages go
  // through the same table. The ones it assembles from parts (a size, a path)
  // are not here: they arrive already assembled and cannot be matched.
  'Puedes escribirle desde aquí.': ('You can write to it from here.', 'Du kannst ihm von hier schreiben.'),
  'Aún no está activado. Actívalo y podrás escribirle desde aquí, igual que por Telegram.': ('Not switched on yet. Switch it on and you can write to it from here, just like on Telegram.', 'Noch nicht aktiviert. Aktiviere es, dann kannst du ihm von hier schreiben, genau wie über Telegram.'),
  'Conversación no válida.': ('Invalid conversation.', 'Ungültiges Gespräch.'),
  'Escribe algo primero.': ('Write something first.', 'Schreib zuerst etwas.'),
  'Ese archivo está vacío.': ('That file is empty.', 'Diese Datei ist leer.'),
  'Ese archivo ya no está disponible. Pídeselo otra vez al agente.': ('That file is not available any more. Ask the agent for it again.', 'Diese Datei ist nicht mehr verfügbar. Frag den Agenten noch einmal danach.'),
  'Ese archivo ya no está donde estaba.': ('That file is not where it was any more.', 'Diese Datei ist nicht mehr dort, wo sie war.'),
  'La grabación llegó vacía.': ('The recording arrived empty.', 'Die Aufnahme kam leer an.'),
  'No pude leer esa grabación.': ('I could not read that recording.', 'Ich konnte diese Aufnahme nicht lesen.'),
  'No pude leer ese archivo.': ('I could not read that file.', 'Ich konnte diese Datei nicht lesen.'),
  'No encontré un puerto libre para hablar con el agente.': ('I could not find a free port to talk to the agent.', 'Ich habe keinen freien Port gefunden, um mit dem Agenten zu sprechen.'),
  'Este ordenador no tiene instalado el transcriptor de voz.': ('This computer does not have the voice transcriber installed.', 'Auf diesem Computer ist der Transkriptor nicht installiert.'),
  'Copiar todo': ('Copy all', 'Alles kopieren'),
  'Copiado': ('Copied', 'Kopiert'),
  'Responder': ('Reply', 'Antworten'),
  'Volver a enviar': ('Send again', 'Erneut senden'),
  'Ese tipo de archivo no se abre desde aquí.': (
    'That kind of file is not opened from here.',
    'Diese Art von Datei wird hier nicht geöffnet.'
  ),
  'No hay ninguna app para abrir eso.': (
    'There is no app to open that.',
    'Es gibt keine App, die das öffnen kann.'
  ),
  'Este agente todavía no está puesto para hablar desde aquí.': (
    'This agent is not set up to talk from here yet.',
    'Dieser Agent ist noch nicht dafür eingerichtet, von hier aus zu sprechen.'
  ),
  'Se enciende una sola vez y queda encendido.': (
    'You switch it on once and it stays on.',
    'Einmal einschalten, dann bleibt es an.'
  ),
  'Enciéndelo en la computadora: elige este agente y abre «Háblale desde aquí».': (
    'Switch it on at the computer: pick this agent and open «Háblale desde aquí».',
    'Schalte es am Computer ein: Wähle diesen Agenten und öffne «Háblale desde aquí».'
  ),
  'Encender': ('Switch on', 'Einschalten'),
  'Encendiendo…': ('Switching on…', 'Wird eingeschaltet…'),

  // ── what an agent is, and where it can be talked to ──
  'Sobre este agente': ('About this agent', 'Über diesen Agenten'),
  'Quién es': ('Who it is', 'Wer er ist'),
  'Para qué es': ('What it is for', 'Wofür er da ist'),
  'Editar': ('Edit', 'Bearbeiten'),
  'Todavía no tiene uno escrito.': ('Nothing written for it yet.', 'Dafür ist noch nichts geschrieben.'),
  'Lo que este agente es y cómo debe comportarse.': (
    'What this agent is and how it should behave.',
    'Was dieser Agent ist und wie er sich verhalten soll.'
  ),
  'Lo usa desde el siguiente mensaje: no hace falta reiniciar nada.': (
    'It uses this from the next message on: nothing needs restarting.',
    'Ab der nächsten Nachricht gilt das: Es muss nichts neu gestartet werden.'
  ),
  'Debajo de eso': ('Underneath that', 'Darunter'),
  'Lo que trae Hermes de fábrica, igual para todos los agentes. No se edita desde aquí.': (
    'What Hermes ships with, the same for every agent. Not editable from here.',
    'Was Hermes mitbringt, für alle Agenten gleich. Hier nicht bearbeitbar.'
  ),
  'Lo que sabe hacer': ('What it knows how to do', 'Was er kann'),
  'Nada especial todavía.': ('Nothing special yet.', 'Noch nichts Besonderes.'),
  '1 habilidad': ('1 skill', '1 Fähigkeit'),
  '{0} habilidades': ('{0} skills', '{0} Fähigkeiten'),
  'Suyas': ('Its own', 'Seine eigenen'),
  'De la computadora': ('From the computer', 'Vom Computer'),
  'Las tienen todos los agentes de esta máquina.': (
    'Every agent on this machine has these.',
    'Die haben alle Agenten auf diesem Rechner.'
  ),
  'Son más largas de lo que cabe aquí; esto es el principio.': (
    'They are longer than fits here; this is the beginning.',
    'Sie sind länger als hier hineinpasst; das ist der Anfang.'
  ),
  'Buscando las instrucciones…': ('Fetching the instructions…', 'Anleitung wird geholt…'),
  'Con quién habla': ('Who it talks to', 'Mit wem er spricht'),
  'Es el único agente de esta computadora.': (
    'It is the only agent on this computer.',
    'Er ist der einzige Agent auf diesem Computer.'
  ),
  'Puede preguntarles algo y esperar su respuesta, sin que tú estés delante.': (
    'It can ask them something and wait for an answer, without you being there.',
    'Er kann sie etwas fragen und auf Antwort warten, auch ohne dich.'
  ),
  'Ahora mismo no contesta': ('Not answering right now', 'Antwortet gerade nicht'),
  'Cómo les habla': ('How it talks to them', 'Wie er mit ihnen spricht'),
  'Dónde se le puede hablar': ('Where it can be talked to', 'Wo man mit ihm sprechen kann'),
  'En este teléfono': ('On this phone', 'Auf diesem Telefon'),
  'Si está apagado, el agente no aparece para hablar desde el teléfono.': (
    'With this off, the agent cannot be talked to from the phone.',
    'Ist das aus, kann man vom Telefon aus nicht mit ihm sprechen.'
  ),
  'En la computadora': ('On the computer', 'Auf dem Computer'),
  'La ventana de conversación de la computadora.': (
    'The conversation window on the computer.',
    'Das Gesprächsfenster auf dem Computer.'
  ),
  'Para cambiar estos interruptores, este teléfono necesita el permiso de configuración.': (
    'To change these switches, this phone needs the configuration permission.',
    'Um diese Schalter zu ändern, braucht dieses Telefon die Konfigurationsberechtigung.'
  ),
  'Preguntándole al agente…': ('Asking the agent…', 'Der Agent wird gefragt…'),
  'Guardando…': ('Saving…', 'Wird gespeichert…'),
  'Todo lo que os habéis mandado.': (
    'Everything the two of you have sent.',
    'Alles, was ihr euch geschickt habt.'
  ),
};
