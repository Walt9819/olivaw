package com.olivaw.olivaw

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * Keeps this app alive while an agent is thinking, and says so.
 *
 * An agent takes its time - a minute is ordinary, several is not unusual - and a phone
 * put back in a pocket is a phone whose app Android is free to freeze or kill. Without
 * this, leaving the screen during a long answer loses the wait: the question was asked
 * and the computer will finish it, but the phone stops listening and nothing says when
 * it is done.
 *
 * Deliberately NOT Firebase. Push would mean a Google project, a third party holding a
 * token for every phone, and the message passing through it - which is exactly what the
 * rest of this was built to avoid. A foreground service costs a visible notification
 * while it runs, which is a fair trade and arguably the honest one: the phone IS doing
 * something, and the owner can see it.
 *
 * It holds no connection of its own. The Dart side already knows how to talk to the
 * computer, with the certificate it pinned; all this does is raise the process out of
 * the range where Android stops it.
 */
class WaitingService : Service() {

    companion object {
        const val ONGOING_CHANNEL = "olivaw.waiting"
        const val REPLY_CHANNEL = "olivaw.replies"
        const val ONGOING_ID = 1001
        const val EXTRA_WHO = "who"

        fun channels(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = context.getSystemService(NotificationManager::class.java)
            // Low importance: it is a status, not news. It must exist while the app
            // waits, but it should never make a sound for doing so.
            nm.createNotificationChannel(
                NotificationChannel(
                    ONGOING_CHANNEL,
                    "Esperando respuesta",
                    NotificationManager.IMPORTANCE_LOW
                ).apply { description = "Mientras un agente está pensando." }
            )
            // The reply itself IS news.
            nm.createNotificationChannel(
                NotificationChannel(
                    REPLY_CHANNEL,
                    "Respuestas",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply { description = "Cuando un agente responde." }
            )
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        channels(this)
        val who = intent?.getStringExtra(EXTRA_WHO) ?: "tu agente"
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val note = NotificationCompat.Builder(this, ONGOING_CHANNEL)
            .setContentTitle("$who está pensando…")
            .setContentText("Te aviso en cuanto responda.")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(tap)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                ONGOING_ID, note,
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(ONGOING_ID, note)
        }
        // If Android kills us under pressure it should not resurrect us with a stale
        // intent: whether a reply is still owed is the app's business, not ours.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        @Suppress("DEPRECATION")
        stopForeground(true)
        super.onDestroy()
    }
}
