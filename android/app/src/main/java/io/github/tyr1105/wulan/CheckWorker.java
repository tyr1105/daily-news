package io.github.tyr1105.wulan;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;
import androidx.work.Constraints;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * Checks for a new issue at fixed times instead of polling: the daily issue is published before
 * 08:00 Singapore time, so the first check is at 08:30, with catch-up checks at 09:00, 09:30, 10:30
 * and 12:00 in case it is late. Once today's issue is found (or seen in the app), the next check is
 * tomorrow at 08:30. Each run schedules the next one.
 */
public class CheckWorker extends Worker {
    static final String EXTRA_FROM_NOTICE = "from_notice";
    private static final String CHANNEL = "daily";
    private static final String PREFS = "wulan";
    private static final String KEY_SEEN = "last_seen";
    private static final String WORK_NAME = "daily-check";
    private static final String OLD_PERIODIC_WORK = "check-new-issue"; // 1.0.1 polled every 15 minutes
    private static final TimeZone SGT = TimeZone.getTimeZone("Asia/Singapore");
    private static final int[][] SLOTS = {{8, 30}, {9, 0}, {9, 30}, {10, 30}, {12, 0}};
    private static final Pattern DATE = Pattern.compile("^\\d{4}-\\d{2}-\\d{2}$");

    public CheckWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    /** Called when the app opens: (re)plans the next check. */
    static void schedule(Context ctx) {
        WorkManager.getInstance(ctx).cancelUniqueWork(OLD_PERIODIC_WORK);
        enqueueNext(ctx, ExistingWorkPolicy.REPLACE);
    }

    private static void enqueueNext(Context ctx, ExistingWorkPolicy policy) {
        long delay = Math.max(0L, nextCheckAt(ctx) - System.currentTimeMillis());
        OneTimeWorkRequest request = new OneTimeWorkRequest.Builder(CheckWorker.class)
                .setInitialDelay(delay, TimeUnit.MILLISECONDS)
                .setConstraints(new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build();
        WorkManager.getInstance(ctx).enqueueUniqueWork(WORK_NAME, policy, request);
    }

    /** The next check time, in epoch milliseconds. */
    static long nextCheckAt(Context ctx) {
        Calendar now = Calendar.getInstance(SGT);
        boolean haveToday = seen(ctx).compareTo(day(now)) >= 0;
        if (!haveToday) {
            for (int[] slot : SLOTS) {
                Calendar c = at(now, 0, slot[0], slot[1]);
                if (c.getTimeInMillis() > now.getTimeInMillis() + 60_000L) return c.getTimeInMillis();
            }
        }
        return at(now, 1, SLOTS[0][0], SLOTS[0][1]).getTimeInMillis();
    }

    private static Calendar at(Calendar base, int addDays, int hour, int minute) {
        Calendar c = (Calendar) base.clone();
        c.add(Calendar.DAY_OF_YEAR, addDays);
        c.set(Calendar.HOUR_OF_DAY, hour);
        c.set(Calendar.MINUTE, minute);
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        return c;
    }

    private static String day(Calendar c) {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
        f.setTimeZone(SGT);
        return f.format(c.getTime());
    }

    private static String seen(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_SEEN, "");
    }

    static synchronized void markSeen(Context ctx, String date) {
        if (date == null || !DATE.matcher(date).matches()) return;
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (date.compareTo(prefs.getString(KEY_SEEN, "")) > 0) {
            prefs.edit().putString(KEY_SEEN, date).apply();
        }
    }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        String seen = seen(ctx);
        if (seen.compareTo(day(Calendar.getInstance(SGT))) < 0) {
            try {
                JSONObject latest = fetchLatest();
                String date = latest == null ? "" : latest.optString("date", "");
                if (DATE.matcher(date).matches() && date.compareTo(seen) > 0) {
                    // First check after install only records the current issue, without a notification.
                    if (!seen.isEmpty()) notifyIssue(ctx, latest);
                    markSeen(ctx, date);
                }
            } catch (Exception ignored) {
                // offline or the site is down: the next slot tries again
            }
        }
        enqueueNext(ctx, ExistingWorkPolicy.APPEND_OR_REPLACE);
        return Result.success();
    }

    private static JSONObject fetchLatest() throws Exception {
        // The query string keeps GitHub Pages' CDN from answering with an older copy.
        URL url = new URL(BuildConfig.SITE_URL + "data/index.json?t=" + (System.currentTimeMillis() / 60000L));
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setUseCaches(false);
        c.setRequestProperty("Accept", "application/json");
        try {
            if (c.getResponseCode() != 200) return null;
            StringBuilder body = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) body.append(line);
            }
            JSONArray issues = new JSONObject(body.toString()).optJSONArray("issues");
            if (issues == null) return null;
            JSONObject best = null;
            for (int i = 0; i < issues.length(); i++) {
                JSONObject o = issues.optJSONObject(i);
                if (o == null) continue;
                String d = o.optString("date", "");
                if (!DATE.matcher(d).matches()) continue;
                if (best == null || d.compareTo(best.optString("date", "")) > 0) best = o;
            }
            return best;
        } finally {
            c.disconnect();
        }
    }

    /** True when this app may show notifications (permission granted and not switched off). */
    static boolean notificationsOn(Context ctx) {
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return false;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        NotificationChannel ch = nm == null ? null : nm.getNotificationChannel(CHANNEL);
        return ch == null || ch.getImportance() != NotificationManager.IMPORTANCE_NONE;
    }

    static void post(Context ctx, int id, String title, String text, String bigText) {
        if (!notificationsOn(ctx)) return;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.createNotificationChannel(new NotificationChannel(
                CHANNEL, ctx.getString(R.string.channel_name), NotificationManager.IMPORTANCE_DEFAULT));
        Intent open = new Intent(ctx, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(EXTRA_FROM_NOTICE, true);
        PendingIntent pi = PendingIntent.getActivity(ctx, 1, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        NotificationCompat.Builder b = new NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_wulan)
                .setColor(ContextCompat.getColor(ctx, R.color.accent))
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(bigText == null ? text : bigText))
                .setContentIntent(pi)
                .setAutoCancel(true);
        try {
            NotificationManagerCompat.from(ctx).notify(id, b.build());
        } catch (SecurityException ignored) {
            // notifications turned off
        }
    }

    private static void notifyIssue(Context ctx, JSONObject issue) {
        String date = issue.optString("date", "");
        int no = issue.optInt("no", 0);
        int count = issue.optInt("count", 0);
        String lead = issue.optString("lead_title", "");
        String title = ctx.getString(R.string.app_name) + " · " + monthDay(date) + (no > 0 ? " · 第" + no + "期" : "");
        String text = lead.isEmpty() ? "新一期已经更新" : "头条：" + lead;
        post(ctx, 1, title, text, text + (count > 0 ? "\n共 " + count + " 条，点开阅读" : ""));
    }

    private static String monthDay(String date) {
        if (!DATE.matcher(date).matches()) return date;
        return Integer.parseInt(date.substring(5, 7)) + "月" + Integer.parseInt(date.substring(8, 10)) + "日";
    }
}
