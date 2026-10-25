package io.agdeck.console;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * AG Deck 的 Android 外壳。
 *
 * 页面本身仍然由电脑上的服务提供（WebView 加载），这样电脑和手机看到的是同一份界面、
 * 同一份数据；这里只补上"App 该有的东西"：图标、全屏无地址栏、返回键、记住登录态、
 * 连不上时的友好提示与地址设置。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "ag";
    private static final String KEY_URL = "url";
    /** 首次启动不猜地址：弹窗让用户填电脑上显示的 URL（AG Deck 启动时会打印） */
    private static final String DEFAULT_URL = "";

    private static final int BG = Color.rgb(0xEE, 0xF0, 0xF4);
    private static final int CARD = Color.WHITE;
    private static final int TEXT = Color.rgb(0x17, 0x1A, 0x21);
    private static final int SUB = Color.rgb(0x8B, 0x94, 0xA3);
    private static final int ACCENT = Color.rgb(0x44, 0x57, 0xFF);

    private WebView web;
    private View splash;
    private View errorView;
    private TextView errorTitle;
    private SharedPreferences prefs;
    private String url;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        url = normalize(prefs.getString(KEY_URL, DEFAULT_URL));

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = buildWebView();
        root.addView(web, match());

        splash = buildSplash();
        root.addView(splash, match());

        errorView = buildError();
        errorView.setVisibility(View.GONE);
        root.addView(errorView, match());

        setContentView(root);

        handleIntent(getIntent());
        if (url.isEmpty()) {
            // 第一次启动：不猜地址，直接让用户填电脑上打印的 URL
            new android.os.Handler().postDelayed(this::showSettings, 350);
        } else {
            web.loadUrl(url);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if ("io.agdeck.console.SETTINGS".equals(action)) {
            web.postDelayed(this::showSettings, 300);
        } else if ("io.agdeck.console.REFRESH".equals(action)) {
            web.postDelayed(() -> web.reload(), 300);
        }
    }

    /* ---------------- WebView ---------------- */

    @SuppressLint("SetJavaScriptEnabled")
    private WebView buildWebView() {
        WebView w = new WebView(this);
        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(false);
        s.setUseWideViewPort(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setTextZoom(100);
        s.setUserAgentString(s.getUserAgentString() + " AGDeckApp/1.0");

        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(w, true);

        w.setBackgroundColor(BG);
        w.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);

        w.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                String scheme = u.getScheme() == null ? "" : u.getScheme();
                if (scheme.startsWith("http")) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String u) {
                splash.setVisibility(View.GONE);
                CookieManager.getInstance().flush();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    showError();
                }
            }
        });

        return w;
    }

    /* ---------------- 界面 ---------------- */

    private View buildSplash() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(BG);

        ImageView icon = new ImageView(this);
        icon.setImageResource(R.mipmap.ic_launcher);
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(dp(88), dp(88));
        ip.bottomMargin = dp(18);
        box.addView(icon, ip);

        TextView title = new TextView(this);
        title.setText("AG Deck");
        title.setTextColor(TEXT);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        TextView sub = new TextView(this);
        sub.setText(getString(R.string.connecting));
        sub.setTextColor(SUB);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        sub.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.topMargin = dp(8);
        box.addView(sub, sp);

        return box;
    }

    private View buildError() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(BG);
        box.setPadding(dp(32), dp(32), dp(32), dp(32));

        TextView icon = new TextView(this);
        icon.setText("⚠");
        icon.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34);
        icon.setTextColor(Color.rgb(0xC9, 0x8A, 0x2B));
        icon.setGravity(Gravity.CENTER);
        box.addView(icon);

        errorTitle = new TextView(this);
        errorTitle.setText(getString(R.string.cant_reach));
        errorTitle.setTextColor(TEXT);
        errorTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        errorTitle.setTypeface(Typeface.DEFAULT_BOLD);
        errorTitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tp.topMargin = dp(10);
        box.addView(errorTitle, tp);

        TextView hint = new TextView(this);
        hint.setText("确认电脑上的 AG Deck 在运行，且手机和电脑在同一个 Tailscale 网络或同一 Wi-Fi 下。");
        hint.setTextColor(SUB);
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        hint.setGravity(Gravity.CENTER);
        hint.setLineSpacing(0, 1.35f);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hp.topMargin = dp(8);
        box.addView(hint, hp);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rp.topMargin = dp(22);
        box.addView(row, rp);

        TextView retry = button(getString(R.string.retry), ACCENT, Color.WHITE);
        retry.setOnClickListener(v -> {
            if (url == null || url.isEmpty()) {
                showSettings();
                return;
            }
            hideError();
            web.loadUrl(url);
        });
        LinearLayout.LayoutParams b1 = new LinearLayout.LayoutParams(dp(112), dp(44));
        b1.rightMargin = dp(10);
        row.addView(retry, b1);

        TextView addr = button(getString(R.string.change_addr), Color.WHITE, TEXT);
        addr.setOnClickListener(v -> showSettings());
        row.addView(addr, new LinearLayout.LayoutParams(dp(112), dp(44)));

        return box;
    }

    private TextView button(String label, int bg, int fg) {
        TextView tv = new TextView(this);
        tv.setText(label);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        tv.setTypeface(Typeface.DEFAULT_BOLD);
        tv.setTextColor(fg);
        tv.setGravity(Gravity.CENTER);
        GradientDrawable d = new GradientDrawable();
        d.setColor(bg);
        d.setCornerRadius(dp(10));
        if (bg == Color.WHITE) {
            d.setStroke(dp(1), Color.rgb(0xE3, 0xE6, 0xEC));
        }
        tv.setBackground(d);
        return tv;
    }

    private void showError() {
        if (splash != null) splash.setVisibility(View.GONE);
        if (errorTitle != null) {
            errorTitle.setText(getString(R.string.cant_reach) + "\n" + hostOf(url));
        }
        errorView.setVisibility(View.VISIBLE);
    }

    /** 首次启动还没填地址：给一个明确的"去设置"界面，而不是空白启动页 */
    private void showSetupRequired() {
        if (splash != null) splash.setVisibility(View.GONE);
        if (errorTitle != null) errorTitle.setText(R.string.no_addr);
        if (errorView != null) errorView.setVisibility(View.VISIBLE);
    }

    private void hideError() {
        errorView.setVisibility(View.GONE);
        splash.setVisibility(View.VISIBLE);
    }

    /* ---------------- 设置 ---------------- */

    private void showSettings() {
        final EditText input = new EditText(this);
        input.setText(url);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint(R.string.hint_addr);
        input.setSelection(input.getText().length());
        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(dp(20), dp(8), dp(20), 0);
        wrap.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        new AlertDialog.Builder(this)
                .setTitle(R.string.server_addr)
                .setView(wrap)
                .setPositiveButton(R.string.save, (d, w) -> {
                    String u = normalize(input.getText().toString());
                    if (u.isEmpty()) {
                        Toast.makeText(this, R.string.hint_addr, Toast.LENGTH_LONG).show();
                        return;
                    }
                    url = u;
                    prefs.edit().putString(KEY_URL, u).apply();
                    hideError();
                    web.loadUrl(u);
                    Toast.makeText(this, u, Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton(R.string.cancel, (d, w) -> {
                    if (url == null || url.isEmpty()) showSetupRequired();
                })
                .setOnCancelListener(d -> {
                    if (url == null || url.isEmpty()) showSetupRequired();
                })
                .show();
    }

    private static String normalize(String raw) {
        String u = raw == null ? "" : raw.trim();
        if (u.isEmpty()) return "";
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    private static String hostOf(String u) {
        try {
            Uri uri = Uri.parse(u);
            return uri.getHost() + (uri.getPort() > 0 ? ":" + uri.getPort() : "");
        } catch (Exception e) {
            return u;
        }
    }

    /* ---------------- 生命周期 ---------------- */

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
        prefs.edit().putString(KEY_URL, url).apply();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.stopLoading();
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    /* ---------------- 小工具 ---------------- */

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private FrameLayout.LayoutParams match() {
        return new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }
}
