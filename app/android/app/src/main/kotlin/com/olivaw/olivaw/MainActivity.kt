package com.olivaw.olivaw

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.annotation.NonNull
import androidx.core.app.ActivityCompat
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

    override fun configureFlutterEngine(@NonNull engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, channel)
            .setMethodCallHandler { call, result -> handle(call, result) }
    }

    private fun handle(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "openUrl" -> openUrl(call.argument<String>("url"), result)
            "pick" -> pick(call.argument<String>("mime") ?: "*/*", result)
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
