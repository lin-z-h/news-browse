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
import android.webkit.JavascriptInterface;
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
    // 下载源按顺序尝试：Pages 与应用同源（可达性=站点本身），GitHub Release 为备用
    private static final String[] APK_URLS = {
            SITE_URL + "Journal.apk",
            "https://github.com/lin-z-h/news-browse/releases/download/apk-latest/Journal.apk",
    };

    private WebView web;
    private FrameLayout root;
    private ImageButton updateBtn;
    private ProgressBar updateBar;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean updateAvailable = false;
    private volatile boolean downloading = false;
    private String capturingFor = null;
    private String capturingTitle = "";
    private String pendingCaptureJs = null;
    private boolean captureInjecting = false;

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

        web.enableSlowWholeDocumentDraw(); // 打印/导出 PDF 需要完整文档绘制
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        web.setVerticalScrollBarEnabled(false); // 滚动条隐藏，与网页端一致
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                // 人工验证完成后回到应用首页时，把提取的正文写入 localStorage 并刷新
                if (captureInjecting && url.startsWith(SITE_URL)) {
                    view.evaluateJavascript(pendingCaptureJs, r -> {
                        captureInjecting = false;
                        pendingCaptureJs = null;
                        view.evaluateJavascript("location.reload()", null);
                    });
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsPrompt(WebView view, String url, String message,
                                      String defaultValue, JsPromptResult result) {
                // 页面双击报头设置 Jina key 时，用原生对话框承接（WebView 默认不弹 prompt）
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
        // JS ↔ 原生桥：阅读模式开/关时隐藏/恢复右上角更新按钮
        web.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void hideUpdate(boolean hide) {
                ui.post(() -> {
                    if (downloading) return; // 下载进行中不打断
                    updateBtn.setVisibility(hide ? View.GONE : View.VISIBLE);
                });
            }

            @android.webkit.JavascriptInterface
            public void exportPage() {
                ui.post(() -> {
                    try {
                        android.print.PrintManager pm = (android.print.PrintManager) getSystemService(PRINT_SERVICE);
                        android.print.PrintDocumentAdapter adapter = web.createPrintDocumentAdapter("Journal");
                        pm.print("Journal", adapter, new android.print.PrintAttributes.Builder().build());
                    } catch (Exception ignored) {
                    }
                });
            }

            @android.webkit.JavascriptInterface
            public void captureFor(String id, String url, String title) {
                ui.post(() -> {
                    capturingFor = id;
                    capturingTitle = title == null ? "" : title;
                    updateAvailable = false;
                    updateBtn.setColorFilter(0xFF1A73E8); // 蓝色 = 待提取
                    updateBtn.setVisibility(View.VISIBLE);
                    updateBar.setVisibility(View.GONE);
                    web.loadUrl("https://archive.ph/newest/" + url);
                });
            }
        }, "Android");
        web.loadUrl(SITE_URL);

        // 右上角更新按钮：圆形外框 + 上箭头；默认灰色，有更新时箭头变蓝
        updateBtn = new ImageButton(this);
        updateBtn.setImageResource(R.drawable.ic_update);
        updateBtn.setColorFilter(0xFF8D867A);
        updateBtn.setBackground(circleBg(0x66FFFFFF, 0x33888888));
        updateBtn.setOnClickListener(v -> {
            if (capturingFor != null) { extractCaptured(); return; }
            if (updateAvailable && !downloading) startDownload();
        });
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
            boolean ok = false;
            String lastErr = null;
            for (String url : APK_URLS) { // 多源依次尝试：Pages 同源 → GitHub Release
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
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
                    if (apk.length() > 100 * 1024) { // 明显小于正常体积视为失败
                        ok = true;
                        break;
                    }
                } catch (Exception e) {
                    lastErr = type(e) + ": " + String.valueOf(e.getMessage()).substring(0, Math.min(60, String.valueOf(e.getMessage()).length()));
                }
                if (downloading) break; // 被关闭/中止
            }
            final boolean okF = ok;
            ui.post(() -> {
                downloading = false;
                if (okF) {
                    install(apk);
                    setUpdateAvailable(false); // 更新成功，恢复灰色圆角
                    updateBtn.setVisibility(View.VISIBLE);
                    updateBar.setVisibility(View.GONE);
                } else {
                    Toast.makeText(MainActivity.this, "下载失败，请检查网络后点箭头重试", Toast.LENGTH_LONG).show();
                    setUpdateAvailable(true);
                    updateBtn.setVisibility(View.VISIBLE);
                    updateBar.setVisibility(View.GONE);
                }
            });
        }).start();
    }

    private String type(Throwable t) {
        return t.getClass().getSimpleName();
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

    private void extractCaptured() {
        final String fid = capturingFor == null ? "" : capturingFor;
        final String ftitle = capturingTitle;
        web.evaluateJavascript("(document.body ? document.body.innerText : '')", value -> {
            String text = "";
            try {
                Object o = new org.json.JSONTokener(value).nextValue();
                if (o instanceof String) text = (String) o;
            } catch (Exception ignored) {}
            String textJson = org.json.JSONObject.quote(text);
            String titleJson = org.json.JSONObject.quote(ftitle);
            pendingCaptureJs = "try{localStorage.setItem('journal.captureRaw', JSON.stringify({id:'"
                    + fid + "', title:" + titleJson + ", text:" + textJson + "}))}catch(e){}";
            captureInjecting = true;
            web.loadUrl(SITE_URL);
        });
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
