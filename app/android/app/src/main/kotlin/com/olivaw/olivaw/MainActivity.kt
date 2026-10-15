package com.olivaw.olivaw

import android.app.Activity
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.annotation.NonNull
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * The three things this app needs from Android, and nothing else.
 *
 * Written here rather than pulled in as packages. Picking a file, recording a voice note
 * and opening a link are three platform intents and a MediaRecorder; the packages that
 * wrap them are each a dependency, a permission manifest and a release cadence that this
 * app would then be tied to. The README says this app has one dependency, and that claim
 * is worth more than the two hundred lines below.
 *
 * Everything returns through a single MethodChannel, and everything that can refuse does
 * so with a named error rather than an exception, so the Dart side can tell "the owner
 * said no" from "it broke".
 */
class MainActivity : FlutterActivity() {

    private val channel = "olivaw/native"
    private var pending: MethodChannel.Result? = null
    private var recorder: MediaRecorder? = null
    private var recordingPath: String? = null

    private val pickFileRequest = 4101
    private val micPermissionRequest = 4102
    private val notifyPermissionRequest = 4103

    override fun configureFlutterEngine(@NonNull engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, channel)
            .setMethodCallHandler { call, result -> handle(call, result) }
    }

    private fun handle(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "openUrl" -> openUrl(call.argument<String>("url"), result)
            "pick" -> pick(call.argument<String>("mime") ?: "*/*", result)
            "waitStart" -> waitStart(call.argument<String>("who"), result)
            "waitStop" -> waitStop(result)
            "notify" -> notify(call.argument<String>("title"),
                               call.argument<String>("body"), result)
            "canNotify" -> result.success(canNotify())
            "askNotify" -> askNotify(result)
            "save" -> save(call.argument<String>("name"),
                           call.argument<ByteArray>("bytes"), result)
            "playStart" -> playStart(call.argument<ByteArray>("bytes"),
                                     call.argument<String>("name"), result)
            "playStop" -> playStop(result)
            "probeDuration" -> probeDuration(call.argument<ByteArray>("bytes"),
                                             call.argument<String>("name"), result)
            "playPosition" -> result.success(player?.let {
                try { if (it.isPlaying) it.currentPosition else -1 } catch (e: Exception) { -1 }
            } ?: -1)
            "recordStart" -> recordStart(result)
            "recordStop" -> recordStop(result)
            "recordCancel" -> recordCancel(result)
            else -> result.notImplemented()
        }
    }

    // ── links ─────────────────────────────────────────────────────────────────────────

    private fun openUrl(url: String?, result: MethodChannel.Result) {
        if (url.isNullOrBlank()) {
            result.error("empty", "No URL", null); return
        }
        // Only the two schemes a message can legitimately carry. An agent's output is
        // text from a model, and handing "intent://" or "file://" straight to
        // startActivity would make any message it writes able to open anything on this
        // phone that will answer to it.
        val lower = url.lowercase()
        if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
            result.error("scheme", "Solo se abren enlaces http y https.", null); return
        }
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            result.success(true)
        } catch (e: Exception) {
            result.error("open", e.message, null)
        }
    }

    // ── waiting, and saying so ────────────────────────────────────────────────────────

    private fun waitStart(who: String?, result: MethodChannel.Result) {
        try {
            val i = Intent(this, WaitingService::class.java)
                .putExtra(WaitingService.EXTRA_WHO, who ?: "Tu agente")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(i)
            } else {
                startService(i)
            }
            result.success(true)
        } catch (e: Exception) {
            // Not fatal: without it the wait simply does not survive the screen going
            // off, which is worth saying but not worth failing the message over.
            result.error("wait", e.message, null)
        }
    }

    private fun waitStop(result: MethodChannel.Result) {
        try {
            stopService(Intent(this, WaitingService::class.java))
        } catch (e: Exception) { /* already gone */ }
        result.success(true)
    }

    private fun canNotify(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            this, android.Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun askNotify(result: MethodChannel.Result) {
        if (canNotify()) { result.success(true); return }
        ActivityCompat.requestPermissions(
            this,
            arrayOf(android.Manifest.permission.POST_NOTIFICATIONS),
            notifyPermissionRequest
        )
        result.success(false)
    }

    /** One notification for one reply. */
    private fun notify(title: String?, body: String?, result: MethodChannel.Result) {
        if (!canNotify()) { result.success(false); return }
        try {
            WaitingService.channels(this)
            val tap = PendingIntent.getActivity(
                this, 0,
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE
            )
            val note = NotificationCompat.Builder(this, WaitingService.REPLY_CHANNEL)
                .setContentTitle(title ?: "Olivaw")
                .setContentText(body ?: "")
                // The whole reply, not one clipped line: an answer worth a notification
                // is usually longer than a notification is tall.
                .setStyle(NotificationCompat.BigTextStyle().bigText(body ?: ""))
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setContentIntent(tap)
                .build()
            NotificationManagerCompat.from(this).notify(
                (System.currentTimeMillis() % 100000).toInt(), note)
            result.success(true)
        } catch (e: SecurityException) {
            result.success(false)
        } catch (e: Exception) {
            result.error("notify", e.message, null)
        }
    }

    // ── files and photos ──────────────────────────────────────────────────────────────

    private fun pick(mime: String, result: MethodChannel.Result) {
        if (pending != null) {
            result.error("busy", "Ya hay un diálogo abierto.", null); return
        }
        pending = result
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = mime
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        try {
            startActivityForResult(intent, pickFileRequest)
        } catch (e: Exception) {
            pending = null
            result.error("pick", e.message, null)
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != pickFileRequest) return
        val result = pending ?: return
        pending = null
        if (resultCode != Activity.RESULT_OK || data == null) {
            result.success(listOf<Map<String, Any>>())   // cancelled is not an error
            return
        }
        val uris = mutableListOf<Uri>()
        data.clipData?.let { clip ->
            for (i in 0 until clip.itemCount) uris.add(clip.getItemAt(i).uri)
        }
        data.data?.let { if (uris.isEmpty()) uris.add(it) }
        val out = mutableListOf<Map<String, Any>>()
        for (uri in uris) {
            try {
                out.add(read(uri))
            } catch (e: Exception) {
                // One unreadable file does not lose the others.
                continue
            }
        }
        result.success(out)
    }

    /** name + bytes for one picked document. */
    private fun read(uri: Uri): Map<String, Any> {
        var name = "archivo"
        contentResolver.query(uri, null, null, null, null)?.use { c ->
            val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (i >= 0 && c.moveToFirst()) name = c.getString(i) ?: name
        }
        val buffer = ByteArrayOutputStream()
        contentResolver.openInputStream(uri)?.use { input ->
            val chunk = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(chunk)
                if (n <= 0) break
                buffer.write(chunk, 0, n)
                // The computer refuses anything over 25 MB, so there is no point reading
                // a 2 GB video into memory on the phone first to be told so.
                if (buffer.size() > 26 * 1024 * 1024) {
                    throw IllegalStateException("too big")
                }
            }
        }
        return mapOf("name" to name, "bytes" to buffer.toByteArray())
    }

    /**
     * Put a file the agent sent into the phone's Downloads.
     *
     * Through MediaStore rather than a path, because since Android 10 an app cannot
     * simply write into shared storage and asking for the permission that used to allow
     * it would be asking for the whole of the user's files to deliver one download.
     */
    private fun save(name: String?, bytes: ByteArray?, result: MethodChannel.Result) {
        if (name.isNullOrBlank() || bytes == null || bytes.isEmpty()) {
            result.error("empty", "Nada que guardar.", null); return
        }
        // Both separators and the characters Windows forbids. The name came from the
        // other machine, and a name is all this is allowed to be - never a path.
        val safe = name.replace(Regex("""[\\/:*?"<>|]"""), "_")
            .trimStart('.')
            .take(120)
            .ifBlank { "archivo" }
        try {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, safe)
                put(MediaStore.Downloads.MIME_TYPE, guessMime(safe))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
            }
            val collection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                MediaStore.Downloads.EXTERNAL_CONTENT_URI
            else
                MediaStore.Files.getContentUri("external")
            val uri = contentResolver.insert(collection, values)
                ?: throw IllegalStateException("no destination")
            contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                contentResolver.update(uri, values, null, null)
            }
            result.success("Descargas/$safe")
        } catch (e: Exception) {
            result.error("save", e.message, null)
        }
    }

    private fun guessMime(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return when (ext) {
            "png" -> "image/png"
            "jpg", "jpeg" -> "image/jpeg"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "pdf" -> "application/pdf"
            "txt", "md", "log" -> "text/plain"
            "csv" -> "text/csv"
            "json" -> "application/json"
            "mp4" -> "video/mp4"
            "m4a" -> "audio/mp4"
            else -> "application/octet-stream"
        }
    }

    // ── playing a voice note ──────────────────────────────────────────────────────────

    private var player: android.media.MediaPlayer? = null
    private var playingFile: File? = null

    /**
     * Play [bytes] and report how long they last.
     *
     * Written to the cache first because MediaPlayer wants a file or a URI, not a byte
     * array - and the note has to exist somewhere anyway for the system to seek in it.
     * One at a time: starting a second note stops the first, which is what every
     * messaging app does and what a single pair of ears expects.
     */
    private fun playStart(bytes: ByteArray?, name: String?, result: MethodChannel.Result) {
        if (bytes == null || bytes.isEmpty()) {
            result.error("empty", "Nada que reproducir.", null); return
        }
        stopPlayer()
        try {
            val f = File(cacheDir, "play-" + (name ?: "nota.m4a").takeLast(40))
            f.writeBytes(bytes)
            val mp = android.media.MediaPlayer()
            mp.setDataSource(f.absolutePath)
            mp.prepare()
            mp.setOnCompletionListener { stopPlayer() }
            mp.start()
            player = mp
            playingFile = f
            result.success(mp.duration)
        } catch (e: Exception) {
            stopPlayer()
            result.error("play", e.message, null)
        }
    }

    /**
     * How long a note lasts, without playing it.
     *
     * A bubble that says 0:00 until you press play is a bubble that looks broken. There
     * is no way to ask the length of an encoded stream without decoding its header, so
     * this prepares a player, reads the duration and throws it away.
     */
    private fun probeDuration(bytes: ByteArray?, name: String?,
                              result: MethodChannel.Result) {
        if (bytes == null || bytes.isEmpty()) { result.success(0); return }
        var f: File? = null
        try {
            f = File(cacheDir, "probe-" + (name ?: "nota.m4a").takeLast(40))
            f.writeBytes(bytes)
            val mp = android.media.MediaPlayer()
            mp.setDataSource(f.absolutePath)
            mp.prepare()
            val ms = mp.duration
            mp.release()
            result.success(ms)
        } catch (e: Exception) {
            result.success(0)
        } finally {
            try { f?.delete() } catch (e: Exception) { /* gone */ }
        }
    }

    private fun playStop(result: MethodChannel.Result) {
        stopPlayer()
        result.success(true)
    }

    private fun stopPlayer() {
        try { player?.stop() } catch (e: Exception) { /* was not playing */ }
        try { player?.release() } catch (e: Exception) { /* already released */ }
        player = null
        try { playingFile?.delete() } catch (e: Exception) { /* gone */ }
        playingFile = null
    }

    override fun onDestroy() {
        stopPlayer()
        super.onDestroy()
    }

    // ── voice notes ───────────────────────────────────────────────────────────────────

    private fun hasMic() = ContextCompat.checkSelfPermission(
        this, android.Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    private fun recordStart(result: MethodChannel.Result) {
        if (!hasMic()) {
            ActivityCompat.requestPermissions(
                this, arrayOf(android.Manifest.permission.RECORD_AUDIO), micPermissionRequest
            )
            // Deliberately not queued behind the dialog: the owner may take a while over
            // it, and a half-started recording is worse than asking them to press again.
            result.error("permission", "Concede el permiso del micrófono y vuelve a pulsar.", null)
            return
        }
        if (recorder != null) {
            result.error("busy", "Ya se está grabando.", null); return
        }
        val file = File(cacheDir, "nota-${System.currentTimeMillis()}.m4a")
        try {
            @Suppress("DEPRECATION")
            val r = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this)
                    else MediaRecorder()
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            // Voice, not music: 32 kbps mono at 22 kHz is a minute of speech in about
            // 240 KB, which crosses a phone connection without anyone waiting for it, and
            // is far more than Whisper needs to transcribe it.
            r.setAudioChannels(1)
            r.setAudioSamplingRate(22050)
            r.setAudioEncodingBitRate(32000)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
            recorder = r
            recordingPath = file.absolutePath
            result.success(true)
        } catch (e: Exception) {
            recorder = null
            recordingPath = null
            result.error("record", e.message, null)
        }
    }

    private fun recordStop(result: MethodChannel.Result) {
        val r = recorder
        val path = recordingPath
        recorder = null
        recordingPath = null
        if (r == null || path == null) {
            result.error("idle", "No se estaba grabando.", null); return
        }
        try {
            r.stop()
        } catch (e: Exception) {
            // stop() throws when nothing was captured - a tap too short to be a note.
            r.reset(); r.release()
            File(path).delete()
            result.error("short", "Demasiado corto.", null)
            return
        }
        r.reset()
        r.release()
        val f = File(path)
        val bytes = try { f.readBytes() } catch (e: Exception) { ByteArray(0) }
        f.delete()
        if (bytes.isEmpty()) {
            result.error("short", "Demasiado corto.", null); return
        }
        result.success(mapOf("name" to f.name, "bytes" to bytes))
    }

    private fun recordCancel(result: MethodChannel.Result) {
        val r = recorder
        val path = recordingPath
        recorder = null
        recordingPath = null
        try { r?.stop() } catch (e: Exception) { /* nothing was captured */ }
        r?.reset()
        r?.release()
        path?.let { File(it).delete() }
        result.success(true)
    }
}
