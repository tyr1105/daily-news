package io.github.tyr1105.wulan;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

/** Shows the 五栏日报 website full screen; pull down to refresh. */
public class MainActivity extends AppCompatActivity {
    /** Reload when the app comes back after this long, so the newest issue shows. */
    private static final long STALE_MS = 30L * 60L * 1000L;

    private WebView web;
    private SwipeRefreshLayout swipe;
    private View offline;
    private Uri site;
    private long loadedAt = 0L;

    private final ActivityResultLauncher<String> askNotifications =
            registerForActivityResult(new ActivityResultContracts.RequestPermission(), granted -> { });

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        setContentView(R.layout.activity_main);
        site = Uri.parse(BuildConfig.SITE_URL);

        View root = findViewById(R.id.root);
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        WindowInsetsControllerCompat bars = WindowCompat.getInsetsController(getWindow(), root);
        bars.setAppearanceLightStatusBars(!night);
        bars.setAppearanceLightNavigationBars(!night);

        swipe = findViewById(R.id.swipe);
        web = findViewById(R.id.web);
        offline = findViewById(R.id.offline);
        findViewById(R.id.retry).setOnClickListener(v -> load());
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.paper));
        swipe.setColorSchemeColors(ContextCompat.getColor(this, R.color.accent));
        swipe.setProgressBackgroundColorSchemeColor(ContextCompat.getColor(this, R.color.sheet));

        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setAllowFileAccess(false);
        ws.setAllowContentAccess(false);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            // The site has its own dark theme; don't let WebView recolor it.
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(ws, false);
        }
        web.addJavascriptInterface(new Bridge(), "WulanApp");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (isSite(u)) return false;
                openExternal(u); // news sources open in the phone's browser
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                swipe.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    swipe.setRefreshing(false);
                    offline.setVisibility(View.VISIBLE);
                }
            }
        });
        swipe.setOnRefreshListener(this::load);
        swipe.setOnChildScrollUpCallback((parent, child) -> web.getScrollY() > 0);

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (web.canGoBack()) {
                    web.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        CheckWorker.schedule(this);
        askForNotifications();
        load();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (loadedAt > 0L && SystemClock.elapsedRealtime() - loadedAt > STALE_MS) load();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent != null && intent.getBooleanExtra(CheckWorker.EXTRA_FROM_NOTICE, false)) load();
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    private void load() {
        offline.setVisibility(View.GONE);
        loadedAt = SystemClock.elapsedRealtime();
        web.loadUrl(BuildConfig.SITE_URL);
    }

    private boolean isSite(Uri u) {
        if (u == null || !"https".equals(u.getScheme()) || u.getHost() == null) return false;
        String path = u.getPath() == null ? "/" : u.getPath();
        return u.getHost().equalsIgnoreCase(site.getHost()) && path.startsWith(site.getPath());
    }

    private void openExternal(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (Exception ignored) {
            // no app can open this link
        }
    }

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS);
        }
    }

    /** Called by the page when it shows the newest issue, so that issue is not notified again. */
    private class Bridge {
        @JavascriptInterface
        public void seen(String date) {
            CheckWorker.markSeen(getApplicationContext(), date);
        }
    }
}
