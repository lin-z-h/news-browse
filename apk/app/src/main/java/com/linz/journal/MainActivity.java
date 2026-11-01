package com.linz.journal;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JsPromptResult;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ProgressBar;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {

    private static final String SITE_URL = "https://lin-z-h.github.io/news-browse/";
    private static final String VERSION_URL = SITE_URL + "app-version.json";
    private static final String APK_URL =
            "https://github.com/lin-z-h/news-browse/releases/download/apk-latest/Journal.apk";

    private WebView web;
    private FrameLayout root;
    private ImageButton updateBtn;
    private ProgressBar updateBar;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean updateAvailable = false;
    private volatile boolean downloading = false;

    private int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        web.setWebViewClient(new WebViewClient());
        // onJsPrompt：页面里双击报头设置 Jina key 时，用原生对话框承接（WebView 默认不弹 prompt）
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsPrompt(WebView view, String url, String message,
                                      String defaultValue, JsPromptResult result) {
                final EditText input = new EditText(MainActivity.this);
                input.setInputType(InputType.TYPE_CLASS_TEXT);
                input.setText(defaultValue);
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(message)
                        .setView(input)
                        .setPositiveButton("OK", (d, w) -> result.confirm(input.getText().toString()))
                        .setNegativeButton("Cancel", (d, w) -> result.cancel())
                        .show();
                return true;
            }
        });
        web.loadUrl(SITE_URL);

        // 右上角更新按钮：圆形外框 + 上箭头；默认灰色，有更新时箭头变蓝
        updateBtn = new ImageButton(this);
        updateBtn.setImageResource(R.drawable.ic_update);
        updateBtn.setColorFilter(0xFF8D867A);
        updateBtn.setBackground(circleBg(0x66FFFFFF, 0x33888888));
        updateBtn.setOnClickListener(v -> { if (updateAvailable && !downloading) startDownload(); });
        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(dp(40), dp(40));
        blp.gravity = Gravity.TOP | Gravity.END;
        blp.topMargin = dp(12);
        blp.rightMargin = dp(12);
        root.addView(updateBtn, blp);

        // 下载进度条：点击后由圆形变形为圆角长框，蓝色从左到右填充
        updateBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        updateBar.setMax(100);
        updateBar.setProgressDrawable(getDrawable(R.drawable.update_progress));
        updateBar.setProgress(0);
        updateBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams plp = new FrameLayout.LayoutParams(dp(40), dp(40));
        plp.gravity = Gravity.TOP | Gravity.END;
        plp.topMargin = dp(12);
        plp.rightMargin = dp(12);
        root.addView(updateBar, plp);

        setContentView(root);
        checkUpdate();
    }

    private GradientDrawable circleBg(int fill, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(fill);
        g.setStroke(dp(1), stroke);
        return g;
    }

    private void checkUpdate() {
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(VERSION_URL).openConnection();
                c.setConnectTimeout(8000);
                c.setReadTimeout(8000);
                StringBuilder sb = new StringBuilder();
                try (InputStream in = c.getInputStream()) {
                    byte[] buf = new byte[4096];
                    int n;
                    while ((n = in.read(buf)) > 0) sb.append(new String(buf, 0, n, StandardCharsets.UTF_8));
                }
                int remote = new JSONObject(sb.toString()).optInt("versionCode", 0);
                int installed = 0;
                try {
                    installed = getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
                } catch (Exception ignored) {}
                if (remote > installed) ui.post(() -> setUpdateAvailable(true));
            } catch (Exception ignored) {
            }
        }).start();
    }

    private void setUpdateAvailable(boolean avail) {
        updateAvailable = avail;
        updateBtn.setColorFilter(avail ? 0xFF1A73E8 : 0xFF8D867A);
    }

    private void startDownload() {
        downloading = true;
        updateBtn.setVisibility(View.GONE);
        updateBar.setVisibility(View.VISIBLE);
        updateBar.setProgress(0);
        // 圆 → 圆角长框的变形动画
        ValueAnimator va = ValueAnimator.ofInt(dp(40), dp(170));
        va.setDuration(350);
        va.addUpdateListener(a -> {
            FrameLayout.LayoutParams p = (FrameLayout.LayoutParams) updateBar.getLayoutParams();
            p.width = (int) a.getAnimatedValue();
            updateBar.setLayoutParams(p);
        });
        va.start();
        new Thread(() -> {
            File apk = new File(getCacheDir(), "journal-update.apk");
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(APK_URL).openConnection();
                c.setInstanceFollowRedirects(true);
                c.setConnectTimeout(15000);
                c.setReadTimeout(30000);
                long total = c.getContentLengthLong();
                InputStream in = c.getInputStream();
                FileOutputStream out = new FileOutputStream(apk);
                byte[] buf = new byte[8192];
                long done = 0;
                int n;
                int lastP = -1;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    done += n;
                    if (total > 0) {
                        int p = (int) (done * 100 / total);
                        if (p != lastP) {
                            final int pp = p;
                            ui.post(() -> updateBar.setProgress(pp));
                            lastP = pp;
                        }
                    }
                }
                out.close();
                in.close();
                ui.post(() -> {
                    downloading = false;
                    install(apk);
                    setUpdateAvailable(false); // 更新成功，恢复灰色圆角
                    updateBtn.setVisibility(View.VISIBLE);
                    updateBar.setVisibility(View.GONE);
                });
            } catch (Exception e) {
                ui.post(() -> {
                    downloading = false;
                    Toast.makeText(MainActivity.this, "下载失败，点箭头重试", Toast.LENGTH_SHORT).show();
                    setUpdateAvailable(true);
                    updateBtn.setVisibility(View.VISIBLE);
                    updateBar.setVisibility(View.GONE);
                });
            }
        }).start();
    }

    private void install(File apk) {
        try {
            Uri uri = FileProvider.getUriForFile(this, "com.linz.journal.files", apk);
            Intent i = new Intent(Intent.ACTION_VIEW);
            i.setDataAndType(uri, "application/vnd.android.package-archive");
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception ignored) {
        }
    }

    private void restoreButton() {
        updateBar.setVisibility(View.GONE);
        updateBtn.setVisibility(View.VISIBLE);
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
