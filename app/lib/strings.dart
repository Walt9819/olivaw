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
  'sin saber': ('not sure', 'unklar'),
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
  'Seguir la conversación anterior':
      ('Carry on the last conversation',
       'Das letzte Gespräch fortsetzen'),
  'Mientras tanto puedes escribir o grabar.':
      ('You can write or record meanwhile.',
       'Du kannst derweil schreiben oder aufnehmen.'),
  'Todavía no has agrupado a nadie.':
      ('You have not grouped anybody yet.',
       'Du hast noch niemanden gruppiert.'),
  'Crear un equipo':
      ('Create a team',
       'Ein Team erstellen'),
  'Cambiar este equipo':
      ('Change this team',
       'Dieses Team ändern'),
  'Ponle un nombre al equipo.':
      ('Give the team a name.',
       'Gib dem Team einen Namen.'),
  'No pude guardar el equipo.':
      ('I could not save the team.',
       'Ich konnte das Team nicht speichern.'),
  '¿Deshacer este equipo?':
      ('Undo this team?',
       'Dieses Team auflösen?'),
  'Los agentes siguen ahí; dejan de estar agrupados.':
      ('The agents stay; they just stop being grouped.',
       'Die Agenten bleiben; sie sind nur nicht mehr gruppiert.'),
  'Deshacer':
      ('Undo',
       'Auflösen'),
  'No pude deshacerlo.':
      ('I could not undo it.',
       'Ich konnte es nicht auflösen.'),
  'Nombre':
      ('Name',
       'Name'),
  'iGalenus, Casa, Marketing':
      ('iGalenus, Home, Marketing',
       'iGalenus, Zuhause, Marketing'),
  'Su símbolo':
      ('Its mark',
       'Sein Zeichen'),
  'Quién está en él':
      ('Who is on it',
       'Wer dabei ist'),
  'ahora en {0}':
      ('now in {0}',
       'jetzt in {0}'),
  'Deshacer el equipo':
      ('Undo the team',
       'Team auflösen'),
  'Llaves y contraseñas':
      ('Keys and passwords',
       'Schlüssel und Passwörter'),
  'Para que entre en sitios por ti. Se guardan en la computadora y no se ven nunca, aquí tampoco.':
      ('So it can sign in for you. Kept on the computer and never shown — not here either.',
       'Damit er sich für dich anmelden kann. Auf dem Computer gespeichert und nie sichtbar — auch hier nicht.'),
  'No pude leer las llaves.':
      ('I could not read the keys.',
       'Ich konnte die Schlüssel nicht lesen.'),
  '¿Borrar esta llave?':
      ('Delete this key?',
       'Diesen Schlüssel löschen?'),
  '{0} dejará de poder entrar ahí.':
      ('{0} will no longer be able to get in there.',
       '{0} kommt dort nicht mehr hinein.'),
  'No':
      ('No',
       'Nein'),
  'Borrar':
      ('Delete',
       'Löschen'),
  'No pude borrarla.':
      ('I could not delete it.',
       'Ich konnte ihn nicht löschen.'),
  'Permitida una vez, durante los próximos minutos.':
      ('Allowed once, for the next few minutes.',
       'Einmal erlaubt, für die nächsten Minuten.'),
  'No pude permitirla.':
      ('I could not allow it.',
       'Ich konnte das nicht erlauben.'),
  'Añadir':
      ('Add',
       'Hinzufügen'),
  'Esa computadora no tiene dónde guardarlas a salvo, así que no guarda ninguna.':
      ('That computer has nowhere safe to keep them, so it keeps none.',
       'Dieser Computer hat keinen sicheren Ort dafür, also speichert er keine.'),
  'Para que {0} pueda entrar en sitios por ti sin que la contraseña pase nunca por la conversación.':
      ('So {0} can sign in for you without the password ever going through the conversation.',
       'Damit {0} sich für dich anmelden kann, ohne dass das Passwort je durch das Gespräch geht.'),
  'Todavía no le has dejado ninguna llave.':
      ('You have not left it any keys yet.',
       'Du hast ihm noch keine Schlüssel hinterlassen.'),
  'Se guardan en el llavero de la computadora ({0}). Las escribe en la página él solo, como mucho {1} veces por hora, y sólo en el sitio de cada una. Nadie puede verlas después: ni él, ni esta app, ni tú.':
      ('They are kept in the computer\'s own keychain ({0}). It types them into the page itself, at most {1} times an hour, and only on each key\'s own site. Nobody can see them afterwards: not it, not this app, not you.',
       'Sie liegen im Schlüsselbund des Computers ({0}). Er tippt sie selbst auf der Seite ein, höchstens {1} Mal pro Stunde, und nur auf der jeweils eigenen Website. Danach kann sie niemand sehen: er nicht, diese App nicht, du nicht.'),
  'cuenta guardada':
      ('account saved',
       'Konto gespeichert'),
  'guarda {0}':
      ('holds {0}',
       'enthält {0}'),
  'permitida ahora':
      ('allowed right now',
       'gerade erlaubt'),
  'Permitir una vez':
      ('Allow once',
       'Einmal erlauben'),
  '¿De qué sitio es?':
      ('Which site is it for?',
       'Für welche Seite?'),
  'Escribe la contraseña que quieres guardar.':
      ('Type the password you want to keep.',
       'Gib das Passwort ein, das gespeichert werden soll.'),
  'No pude guardarla.':
      ('I could not save it.',
       'Ich konnte es nicht speichern.'),
  'Cambiar esta llave':
      ('Change this key',
       'Diesen Schlüssel ändern'),
  'Añadir una llave':
      ('Add a key',
       'Einen Schlüssel hinzufügen'),
  '¿De qué sitio?':
      ('Which site?',
       'Welche Seite?'),
  '…o escribe cualquier otro abajo: sirve para cualquier sitio con usuario y contraseña.':
      ('…or type any other below: this works for any site with a username and a password.',
       '…oder schreib unten eine beliebige andere: das geht für jede Seite mit Benutzer und Passwort.'),
  'Sitio (una palabra)':
      ('Site (one word)',
       'Seite (ein Wort)'),
  'Cómo la llamas':
      ('What you call it',
       'Wie du ihn nennst'),
  'Google de trabajo':
      ('Work Google',
       'Google für die Arbeit'),
  'Página de acceso':
      ('Sign-in page',
       'Anmeldeseite'),
  'La llave sólo se escribe en este sitio. Es lo que impide que una página falsa se la lleve.':
      ('The key is only ever typed on this site. That is what stops a fake page taking it.',
       'Der Schlüssel wird nur auf dieser Seite eingetippt. Das verhindert, dass eine gefälschte Seite ihn bekommt.'),
  'Usuario o correo':
      ('Username or email',
       'Benutzer oder E-Mail'),
  'Contraseña nueva':
      ('New password',
       'Neues Passwort'),
  'Contraseña':
      ('Password',
       'Passwort'),
  'Déjala vacía si no la cambias. No se puede ver la que hay.':
      ('Leave it empty to keep the current one. There is no way to see it.',
       'Leer lassen, um das bisherige zu behalten. Es lässt sich nicht ansehen.'),
  'Se guarda en el llavero de la computadora. Nadie la ve después, tampoco tú.':
      ('Kept in the computer\'s keychain. Nobody sees it afterwards, you included.',
       'Liegt im Schlüsselbund des Computers. Danach sieht es niemand mehr, du auch nicht.'),
  'Clave de 2 pasos (opcional)':
      ('Two-step key (optional)',
       'Zwei-Faktor-Schlüssel (optional)'),
  'La clave larga que da el sitio al activar la verificación en dos pasos. Con ella escribe los 6 dígitos él solo.':
      ('The long key the site gives you when you turn on two-step verification. With it, it types the six digits itself.',
       'Der lange Schlüssel, den die Seite bei der Zwei-Faktor-Einrichtung zeigt. Damit tippt er die sechs Ziffern selbst.'),
  'Pedirme permiso cada vez':
      ('Ask me every time',
       'Mich jedes Mal fragen'),
  'Te lo pedirá por su canal, y tú lo das aquí con «Permitir una vez».':
      ('It will ask you on its own channel, and you say yes here with «Allow once».',
       'Er fragt dich über seinen Kanal, und du erlaubst es hier mit «Einmal erlauben».'),
  'Mirando qué llaves tiene…':
      ('Looking at which keys it has…',
       'Schaue nach, welche Schlüssel er hat…'),
  'Esta conversación no tiene nada que leer.':
      ('There is nothing to read in this conversation.',
       'In diesem Gespräch gibt es nichts zu lesen.'),
  'Buscar en lo que os dijisteis': ('Search what you said to each other',
      'Durchsuche, was ihr euch gesagt habt'),
  'Buscado en {0} conversaciones': ('Searched {0} conversations',
      'In {0} Gesprächen gesucht'),
  'No pude buscar en las conversaciones.': ('I could not search the conversations.',
      'Ich konnte die Gespräche nicht durchsuchen.'),
  'Nada que diga eso.': ('Nothing says that.', 'Nichts sagt das.'),
  'Trayendo lo de antes…': ('Bringing what came before…',
      'Hole, was davor war…'),
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

  // ── waiting, zooming, and finding the message a file came with ──
  'Trayéndolo…': ('Fetching it…', 'Wird geholt…'),
  'Trayéndolo… {0}': ('Fetching it… {0}', 'Wird geholt… {0}'),
  'Alejar': ('Zoom out', 'Verkleinern'),
  'Acercar': ('Zoom in', 'Vergrößern'),
  'Ver en la conversación': ('See it in the conversation', 'Im Gespräch ansehen'),

  // ── what a wait says while it waits ──
  'Buscando tu computadora…': ('Looking for your computer…', 'Dein Computer wird gesucht…'),
  'Preguntando por tus agentes…': ('Asking about your agents…', 'Deine Agenten werden abgefragt…'),
  'Tarda un poco la primera vez. Sigue en ello.': (
    'The first time takes a moment. Still going.',
    'Beim ersten Mal dauert es etwas. Läuft noch.'
  ),
  'Conectando con el agente…': ('Connecting to the agent…', 'Verbindung zum Agenten…'),
  'Viendo dónde os quedasteis…': ('Finding where you left off…', 'Wo ihr aufgehört habt…'),
  'Sigue en ello. Fuera de casa tarda un poco más.': (
    'Still going. Away from home it takes a little longer.',
    'Läuft noch. Unterwegs dauert es etwas länger.'
  ),
  'Abriendo la conversación…': ('Opening the conversation…', 'Das Gespräch wird geöffnet…'),
  'Trayendo lo que os dijisteis…': (
    'Fetching what you said to each other…',
    'Was ihr euch gesagt habt, wird geholt…'
  ),
  'Esta computadora todavía no sabe contarme sobre el agente.': (
    'This computer cannot tell me about the agent yet.',
    'Dieser Computer kann mir noch nichts über den Agenten sagen.'
  ),

  // ── recording a note, and the agent's own face ──
  'Pausa': ('Pause', 'Pause'),
  'En pausa': ('Paused', 'Pausiert'),
  'Seguir grabando': ('Keep recording', 'Weiter aufnehmen'),
  'Enviar': ('Send', 'Senden'),
  'Este teléfono no sabe pausar la grabación.': (
    'This phone cannot pause a recording.',
    'Dieses Telefon kann eine Aufnahme nicht pausieren.'
  ),
  'Cambiar el nombre': ('Change the name', 'Namen ändern'),
  'Elegir una foto': ('Choose a photo', 'Ein Foto wählen'),
  'Usar un símbolo': ('Use a symbol', 'Ein Symbol verwenden'),
  'Volver a las iniciales': ('Back to initials', 'Zurück zu den Initialen'),
  'Esa foto llegó vacía.': ('That photo came through empty.', 'Das Foto kam leer an.'),
  'Esa foto pesa {0} y el máximo son 5 MB. Recórtala y vuelve a probar.': (
    'That photo is {0} and the limit is 5 MB. Crop it and try again.',
    'Das Foto hat {0}, erlaubt sind 5 MB. Schneide es zu und versuche es erneut.'
  ),

  // ── teams, and how much work has been done ──
  'Agentes': ('Agents', 'Agenten'),
  'Equipos': ('Teams', 'Teams'),
  'Uso': ('Usage', 'Nutzung'),
  'Todavía no has agrupado a nadie. Los equipos se crean en la computadora, en «El equipo».': (
    'Nobody is grouped yet. Teams are made at the computer, under «El equipo».',
    'Noch ist niemand gruppiert. Teams werden am Computer unter «El equipo» erstellt.'
  ),
  '1 agente': ('1 agent', '1 Agent'),
  '{0} agentes': ('{0} agents', '{0} Agenten'),
  'Sin equipo': ('No team', 'Ohne Team'),
  'Trabajan igual que los demás; sólo no están agrupados.': (
    'They work like the rest; they are simply not grouped.',
    'Sie arbeiten wie die anderen, sie sind nur nicht gruppiert.'
  ),
  'Mirando los equipos…': ('Looking at the teams…', 'Teams werden geladen…'),
  'Conversaciones': ('Conversations', 'Gespräche'),
  'Mensajes': ('Messages', 'Nachrichten'),
  'Herramientas': ('Tool calls', 'Werkzeuge'),
  'Tokens': ('Tokens', 'Tokens'),
  'Coste': ('Cost', 'Kosten'),
  'Último movimiento {0}': ('Last active {0}', 'Zuletzt aktiv {0}'),
  'Los últimos': ('The last', 'Die letzten'),
  '{0} d': ('{0} d', '{0} T'),
  'Con una suscripción no hay precio por token, así que esto mide trabajo hecho, no dinero.': (
    'On a subscription there is no price per token, so this measures work done, not money.',
    'Im Abo gibt es keinen Preis pro Token, das hier misst also geleistete Arbeit, nicht Geld.'
  ),
  'Sin datos: su canal no está encendido ahora mismo.': (
    'No data: its channel is not switched on right now.',
    'Keine Daten: sein Kanal ist gerade nicht eingeschaltet.'
  ),
  'Telegram': ('Telegram', 'Telegram'),
  'Consola': ('Console', 'Konsole'),
  'Esta app': ('This app', 'Diese App'),
  'Contando el trabajo…': ('Counting the work…', 'Arbeit wird gezählt…'),
  'Un agente por vez.': ('One agent at a time.', 'Ein Agent nach dem anderen.'),
};
