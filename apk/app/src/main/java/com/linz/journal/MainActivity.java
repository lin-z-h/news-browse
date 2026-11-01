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
import android.widget.Button;
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
import java.util.regex.Pattern;

public class MainActivity extends Activity {

    private static final String SITE_URL = "https://lin-z-h.github.io/news-browse/";
    private static final String VERSION_URL = SITE_URL + "app-version.json";
    // 下载源按顺序尝试：Pages 与应用同源（可达性=站点本身），GitHub Release 为备用
    private static final String[] APK_URLS = {
            SITE_URL + "Journal.apk",
            "https://github.com/lin-z-h/news-browse/releases/download/apk-latest/Journal.apk",
    };
    // archive.today 全部镜像域：只有落在这些域上才允许提取正文
    private static final String[] ARCHIVE_HOSTS = {
            "archive.ph", "archive.today", "archive.is", "archive.li",
            "archive.vn", "archive.md", "archive.fo", "archive.tc",
    };
    // 提取文本的垃圾特征：人机验证页被当正文抓回来时直接拒绝
    private static final Pattern CAPTCHA_RE = Pattern.compile(
            "completing the captcha|proves you are (a )?human|are you a robot|verif(y|ying) (that )?you are (a )?human|security check to access|just a moment|enable javascript and cookies",
            Pattern.CASE_INSENSITIVE);
    // archive.today 快照页正文在 iframe 里（与主页面同源），工具条文字在 body——优先取更长的那个
    private static final String EXTRACT_JS =
            "(function(){var best='';var fr=document.getElementsByTagName('iframe');" +
            "for(var i=0;i<fr.length;i++){try{var d=fr[i].contentDocument;" +
            "if(d&&d.body){var t=d.body.innerText||'';if(t.length>best.length)best=t;}}catch(e){}}" +
            "try{var b=document.body?document.body.innerText:'';if(b.length>best.length)best=b;}catch(e){}" +
            "return best;})()";

    private WebView web;
    private FrameLayout root;
    private ImageButton updateBtn;
    private ProgressBar updateBar;
    private Button captureBtn;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean updateAvailable = false;
    private volatile boolean downloading = false;
    // 两个互斥的浮层状态：阅读模式开着（JS 通过 hideUpdate 桥告知）/ 捕获流程进行中
    private boolean readerOpen = false;
    private boolean captureMode = false;
    private String capturingFor = null;
    private String capturingUrl = "";
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
                if (url.startsWith(SITE_URL)) {
                    if (captureInjecting) {
                        // 人工验证完成、点了提取按钮后回到首页：写入正文并刷新
                        view.evaluateJavascript(pendingCaptureJs, r -> {
                            captureInjecting = false;
                            pendingCaptureJs = null;
                            view.evaluateJavascript("location.reload()", null);
                        });
                    } else if (captureMode) {
                        // 用户没提取就退回了首页：取消捕获态，恢复更新按钮
                        captureMode = false;
                        capturingFor = null;
                        applyButtons();
                    }
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
        // JS ↔ 原生桥：阅读模式开/关时收敛浮层按钮（更新按钮与捕获按钮互不干扰）
        web.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void hideUpdate(boolean hide) {
                ui.post(() -> {
                    readerOpen = hide;
                    applyButtons();
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
                    captureMode = true;
                    capturingFor = id;
                    capturingUrl = url == null ? "" : url;
                    capturingTitle = title == null ? "" : title;
                    readerOpen = false;
                    applyButtons();
                    web.loadUrl("https://archive.ph/newest/" + url);
                });
            }

            // Wayback 快照原生通道：浏览器 fetch 受 CORS 限制读不到快照内容，原生层没有这个限制。
            // 依次试 wayback_url 和 web/2/ 重定向猜测，抓到的 HTML 通过 __wbDeliver 推回页面。
            @android.webkit.JavascriptInterface
            public void fetchSnapshot(String id, String url, String waybackUrl) {
                new Thread(() -> {
                    String html = null;
                    String[] tries = {
                            (waybackUrl == null || waybackUrl.isEmpty()) ? null : waybackUrl,
                            "https://web.archive.org/web/2/" + (url == null ? "" : url),
                    };
                    for (String u : tries) {
                        if (u == null) continue;
                        try {
                            HttpURLConnection c = (HttpURLConnection) new URL(u).openConnection();
                            c.setInstanceFollowRedirects(true);
                            c.setConnectTimeout(10000);
                            c.setReadTimeout(15000);
                            if (c.getResponseCode() != 200) continue;
                            java.io.InputStream in = c.getInputStream();
                            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                            byte[] buf = new byte[8192];
                            int n;
                            while ((n = in.read(buf)) > 0 && bos.size() < 3 * 1024 * 1024) bos.write(buf, 0, n);
                            in.close();
                            String s = bos.toString("UTF-8");
                            if (s.length() > 5000) { html = s; break; }
                        } catch (Exception ignored) {
                        }
                    }
                    final String fh = html;
                    final String fid = id == null ? "" : id;
                    ui.post(() -> {
                        try {
                            String js = "try{if(window.__wbDeliver)__wbDeliver("
                                    + org.json.JSONObject.quote(fid) + ","
                                    + (fh == null ? "null" : org.json.JSONObject.quote(fh)) + ");}catch(e){}";
                            web.evaluateJavascript(js, null);
                        } catch (Exception ignored) {
                        }
                    });
                }).start();
            }
        }, "Android");
        web.loadUrl(SITE_URL);

        // 右上角更新按钮：只管应用更新。圆形外框 + 上箭头，默认灰色，有更新时变蓝
        updateBtn = new ImageButton(this);
        updateBtn.setImageResource(R.drawable.ic_update);
        updateBtn.setColorFilter(0xFF8D867A);
        updateBtn.setBackground(circleBg(0x66FFFFFF, 0x33888888));
        updateBtn.setOnClickListener(v -> {
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

        // 捕获确认按钮：与更新按钮彻底分开。只在捕获流程中出现在底部中央，文字说明用途
        captureBtn = new Button(this);
        captureBtn.setText("完成验证 · 提取正文");
        captureBtn.setAllCaps(false);
        captureBtn.setTextSize(14);
        captureBtn.setTextColor(0xFFFFFFFF);
        captureBtn.setMinWidth(0);
        captureBtn.setMinHeight(0);
        captureBtn.setPadding(dp(20), 0, dp(20), 0);
        GradientDrawable capBg = new GradientDrawable();
        capBg.setCornerRadius(dp(22));
        capBg.setColor(0xFF1A73E8);
        captureBtn.setBackground(capBg);
        captureBtn.setOnClickListener(v -> extractCaptured());
        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(44));
        clp.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        clp.bottomMargin = dp(28);
        root.addView(captureBtn, clp);

        applyButtons();
        setContentView(root);
        checkUpdate();
    }

    // 浮层按钮可见性的唯一裁决处：更新按钮只在「无更新弹窗下载、非阅读模式、非捕获态」时显示
    private void applyButtons() {
        boolean showUpdate = !readerOpen && !captureMode && !downloading;
        updateBtn.setVisibility(showUpdate ? View.VISIBLE : View.GONE);
        updateBar.setVisibility(downloading ? View.VISIBLE : View.GONE);
        captureBtn.setVisibility(captureMode ? View.VISIBLE : View.GONE);
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
        applyButtons();
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
                } catch (Exception ignored) {
                }
                if (!downloading) break; // 被关闭/中止
            }
            final boolean okF = ok;
            ui.post(() -> {
                downloading = false;
                applyButtons();
                if (okF) {
                    install(apk);
                    setUpdateAvailable(false); // 更新成功，恢复灰色
                } else {
                    Toast.makeText(MainActivity.this, "下载失败，请检查网络后点箭头重试", Toast.LENGTH_LONG).show();
                    setUpdateAvailable(true);
                }
            });
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

    private boolean isArchivePage(String u) {
        if (u == null) return false;
        try {
            String h = new URL(u).getHost();
            if (h == null) return false;
            for (String a : ARCHIVE_HOSTS) {
                if (h.equals(a) || h.endsWith("." + a)) return true;
            }
        } catch (Exception ignored) {
        }
        return false;
    }

    // 捕获确认：当前页必须是 archive 镜像域，提取文本要过验证页/长度关，否则提示用户留在原地重试
    private void extractCaptured() {
        if (!captureMode) return;
        if (!isArchivePage(web.getUrl())) {
            Toast.makeText(this, "请先在归档页完成人机验证后再提取", Toast.LENGTH_LONG).show();
            return;
        }
        web.evaluateJavascript(EXTRACT_JS, value -> {
            String text = "";
            try {
                Object o = new org.json.JSONTokener(value).nextValue();
                if (o instanceof String) text = (String) o;
            } catch (Exception ignored) {}
            // 工具条/验证页行都很短：正文至少有 3 行 ≥24 字符（中文短讯一条就够长），验证页另有特征词把关
            int longLines = 0;
            for (String ln : text.split("\n")) {
                if (ln.trim().length() >= 24) longLines++;
            }
            boolean garbage = text.length() < 200 || longLines < 3 || CAPTCHA_RE.matcher(text).find();
            if (garbage) {
                Toast.makeText(this, "未提取到正文：请先完成人机验证，等快照页加载完再点", Toast.LENGTH_LONG).show();
                return; // 留在归档页，捕获按钮保持可按，用户过完验证再点
            }
            String textJson = JSONObject.quote(text);
            String titleJson = JSONObject.quote(capturingTitle);
            String idJson = JSONObject.quote(capturingFor == null ? "" : capturingFor);
            String urlJson = JSONObject.quote(capturingUrl);
            pendingCaptureJs = "try{localStorage.setItem('journal.captureRaw', JSON.stringify({id:"
                    + idJson + ", title:" + titleJson + ", url:" + urlJson + ", text:" + textJson + "}))}catch(e){}";
            captureInjecting = true;
            captureMode = false;
            capturingFor = null;
            applyButtons();
            web.loadUrl(SITE_URL);
        });
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
