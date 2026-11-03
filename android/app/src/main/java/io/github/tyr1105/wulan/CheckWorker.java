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
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
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
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * Every 15 minutes (when online), checks the site's data/index.json for an issue newer than the
 * last one seen, and posts one notification for it. Skips the night, and stops checking once
 * today's issue is known.
 */
public class CheckWorker extends Worker {
    static final String EXTRA_FROM_NOTICE = "from_notice";
    private static final String CHANNEL = "daily";
    private static final String PREFS = "wulan";
    private static final String KEY_SEEN = "last_seen";
    private static final String WORK_NAME = "check-new-issue";
    private static final Pattern DATE = Pattern.compile("^\\d{4}-\\d{2}-\\d{2}$");

    public CheckWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context ctx) {
        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build();
        PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(CheckWorker.class, 15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build();
        WorkManager.getInstance(ctx)
                .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request);
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
        String seen = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_SEEN, "");
        if (seen.compareTo(today()) >= 0) return Result.success(); // today's issue is already known
        if (!seen.isEmpty() && hourInSingapore() < 7) return Result.success(); // new issues land around 08:00

        try {
            JSONObject latest = fetchLatest();
            if (latest == null) return Result.success();
            String date = latest.optString("date", "");
            if (!DATE.matcher(date).matches()) return Result.success();
            if (seen.isEmpty()) {
                // First check after install: remember the current issue without notifying.
                markSeen(ctx, date);
            } else if (date.compareTo(seen) > 0) {
                notifyIssue(ctx, latest);
                markSeen(ctx, date);
            }
            return Result.success();
        } catch (Exception e) {
            return Result.retry();
        }
    }

    private static String today() {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("Asia/Singapore"));
        return f.format(new Date());
    }

    private static int hourInSingapore() {
        return Calendar.getInstance(TimeZone.getTimeZone("Asia/Singapore")).get(Calendar.HOUR_OF_DAY);
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

    private static void notifyIssue(Context ctx, JSONObject issue) {
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.createNotificationChannel(new NotificationChannel(
                CHANNEL, ctx.getString(R.string.channel_name), NotificationManager.IMPORTANCE_DEFAULT));

        String date = issue.optString("date", "");
        int no = issue.optInt("no", 0);
        int count = issue.optInt("count", 0);
        String lead = issue.optString("lead_title", "");
        String title = ctx.getString(R.string.app_name) + " · " + monthDay(date) + (no > 0 ? " · 第" + no + "期" : "");
        String text = lead.isEmpty() ? "新一期已经更新" : "头条：" + lead;
        String more = text + (count > 0 ? "\n共 " + count + " 条，点开阅读" : "");

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
                .setStyle(new NotificationCompat.BigTextStyle().bigText(more))
                .setContentIntent(pi)
                .setAutoCancel(true);
        try {
            NotificationManagerCompat.from(ctx).notify(1, b.build());
        } catch (SecurityException ignored) {
            // notifications turned off
        }
    }

    private static String monthDay(String date) {
        if (!DATE.matcher(date).matches()) return date;
        return Integer.parseInt(date.substring(5, 7)) + "月" + Integer.parseInt(date.substring(8, 10)) + "日";
    }
}
