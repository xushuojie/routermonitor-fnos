package io.github.routermonitor.fnos;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.text.InputType;
import android.util.Log;
import android.view.WindowManager;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.ArrayAdapter;
import android.widget.TextView;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.Calendar;
import java.util.TimeZone;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

public final class MainActivity extends Activity {
    final Handler ui = new Handler();
    MonitorView display;
    SharedPreferences prefs;
    ScheduledThreadPoolExecutor workers;
    volatile HttpURLConnection netConnection, statusConnection;
    volatile int generation;
    boolean resumed, configuring;
    long lastLog;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs = getSharedPreferences("display", MODE_PRIVATE);
        display = new MonitorView(this);
        setContentView(display);
        if (prefs.getString("token", "").length() == 0) ui.post(new Runnable() { public void run() { settings(); }});
    }
    @Override public void onResume() { super.onResume(); resumed = true; start(); ui.post(tick); }
    @Override public void onPause() { resumed = false; ui.removeCallbacks(tick); stop(); super.onPause(); }
    @Override public void onBackPressed() { settings(); }
    @Override public boolean onCreateOptionsMenu(android.view.Menu menu) { settings(); return false; }

    final Runnable tick = new Runnable() { public void run() {
        if (!resumed) return;
        applyBrightness();
        display.invalidate();
        if (SystemClock.elapsedRealtime() - lastLog >= 30000) {
            lastLog = SystemClock.elapsedRealtime();
            Log.i("NasMonitor", "net=" + display.netSuccess + " status=" + display.statusSuccess + " failures=" + display.failures + " netAgeMs=" + display.netAge() + " statusAgeMs=" + display.statusAge() + " drawMaxUs=" + display.drawMaxUs);
            display.drawMaxUs = 0;
        }
        ui.postDelayed(this, 1000);
    }};
    void applyBrightness() {
        float value = prefs.getInt("brightness", 30) / 100f;
        Calendar c = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        int hour = c.get(Calendar.HOUR_OF_DAY);
        if (prefs.getBoolean("night", true) && (hour >= 23 || hour < 7)) value = Math.min(value, .1f);
        WindowManager.LayoutParams p = getWindow().getAttributes();
        if (Math.abs(p.screenBrightness - value) > .001f) { p.screenBrightness = value; getWindow().setAttributes(p); }
    }
    void stop() {
        generation++;
        if (workers != null) { workers.shutdownNow(); workers = null; }
        if (netConnection != null) netConnection.disconnect();
        if (statusConnection != null) statusConnection.disconnect();
    }
    void start() {
        if (!resumed || configuring || workers != null || prefs.getString("token", "").length() == 0) return;
        workers = new ScheduledThreadPoolExecutor(2);
        int current = ++generation;
        String base = prefs.getString("server", ""), token = prefs.getString("token", "");
        workers.execute(new Poll(true, current, base, token, prefs.getInt("interval", 500), workers));
        workers.execute(new Poll(false, current, base, token, 1000, workers));
    }

    final class Poll implements Runnable {
        final boolean net;
        final int ownGeneration, interval;
        final String base, token;
        final ScheduledThreadPoolExecutor executor;
        String epoch = "";
        long sequence;
        int failures;
        Poll(boolean net, int gen, String base, String token, int interval, ScheduledThreadPoolExecutor executor) {
            this.net=net; ownGeneration=gen; this.base=base; this.token=token; this.interval=interval; this.executor=executor;
        }
        public void run() {
            if (ownGeneration != generation) return;
            long started = SystemClock.elapsedRealtime();
            HttpURLConnection connection = null;
            String problem = "连接中断";
            try {
                String path = net ? "/net?v=2&since=" + sequence + "&epoch=" + URLEncoder.encode(epoch, "UTF-8") : "/status?display=1&v=2";
                connection = (HttpURLConnection)new URL(base + path).openConnection();
                if (net) netConnection=connection; else statusConnection=connection;
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(1500); connection.setReadTimeout(1500);
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setRequestProperty("Accept", "application/json");
                connection.setUseCaches(false);
                int code = connection.getResponseCode();
                if (code != 200) {
                    problem = code == 401 ? "Token 无效" : code == 503 ? "采集暂不可用" : "服务响应 " + code;
                    throw new java.io.IOException();
                }
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                InputStream stream = connection.getInputStream();
                try {
                    byte[] buffer = new byte[2048]; int count;
                    while ((count=stream.read(buffer)) != -1) {
                        if (bytes.size() + count > 16384) { problem="响应过大"; throw new java.io.IOException(); }
                        bytes.write(buffer, 0, count);
                    }
                } finally { stream.close(); }
                final JSONObject data = new JSONObject(bytes.toString("UTF-8"));
                double age=data.optDouble("age", Double.NaN);
                if (data.optInt("v") != 2 || !DisplayMath.valid(age) || age > (net ? 1 : 3)) {
                    problem="数据已过期"; throw new java.io.IOException();
                }
                if (net) {
                    String nextEpoch = data.getString("epoch"); long nextSequence=data.getLong("seq");
                    if (nextEpoch.length()>64 || nextSequence<0 || nextSequence>4294967295L || data.getJSONArray("points").length()>4 || data.getJSONArray("rate").length()!=2) throw new java.io.IOException();
                    epoch=nextEpoch; sequence=nextSequence;
                }
                final long received = SystemClock.elapsedRealtime();
                final long serverTime = connection.getHeaderFieldDate("Date", 0);
                failures=0;
                ui.post(new Runnable() { public void run() {
                    if (ownGeneration != generation) return;
                    if (net) display.network(data, received); else display.status(data, received, serverTime);
                }});
            } catch (Exception error) {
                failures=Math.min(failures+1, 5);
                final String message=problem;
                ui.post(new Runnable() { public void run() {
                    if (ownGeneration != generation) return;
                    display.failed(net, message);
                }});
            } finally {
                // Fully consumed responses keep the platform connection pool reusable.
                if (connection != null && failures > 0) connection.disconnect();
                if (net) netConnection=null; else statusConnection=null;
            }
            if (ownGeneration == generation && !executor.isShutdown()) {
                long delay = failures == 0 ? Math.max(20, interval-(SystemClock.elapsedRealtime()-started)) : Math.min(10000, 1000L << (failures-1));
                try { executor.schedule(this, delay, TimeUnit.MILLISECONDS); } catch (java.util.concurrent.RejectedExecutionException ignored) { }
            }
        }
    }

    void settings() {
        if (configuring || isFinishing()) return;
        configuring=true; stop();
        final LinearLayout form=new LinearLayout(this); form.setOrientation(LinearLayout.VERTICAL);
        int padding=(int)(16*getResources().getDisplayMetrics().density); form.setPadding(padding, 0, padding, 0);
        final EditText server=field(form, "NAS 服务地址（包含 http:// 和端口）", prefs.getString("server", ""), false);
        server.setHint("http://192.168.x.x:18199");
        final EditText token=field(form, "只读 Token", prefs.getString("token", ""), true);
        TextView caption=new TextView(this); caption.setText("网络曲线刷新频率"); form.addView(caption);
        final Spinner speed=new Spinner(this);
        ArrayAdapter<String> adapter=new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, new String[]{"均衡 · 500ms（推荐）", "流畅 · 200ms"});
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item); speed.setAdapter(adapter);
        speed.setSelection(prefs.getInt("interval", 500)==200?1:0); form.addView(speed);
        final TextView light=new TextView(this); form.addView(light);
        final SeekBar brightness=new SeekBar(this); brightness.setMax(90); brightness.setProgress(prefs.getInt("brightness", 30)-10);
        light.setText("亮度 " + (brightness.getProgress()+10) + "%");
        brightness.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            public void onProgressChanged(SeekBar b,int progress,boolean user){light.setText("亮度 " + (progress+10) + "%");}
            public void onStartTrackingTouch(SeekBar b){} public void onStopTrackingTouch(SeekBar b){}
        }); form.addView(brightness);
        final CheckBox night=new CheckBox(this); night.setText("23:00–07:00 自动调暗至 10%"); night.setChecked(prefs.getBoolean("night", true)); form.addView(night);
        ScrollView scroll=new ScrollView(this); scroll.addView(form);
        final AlertDialog dialog=new AlertDialog.Builder(this).setTitle("NAS 显示终端设置").setView(scroll).setPositiveButton("保存并连接", null).setNegativeButton("取消", null).create();
        dialog.setOnDismissListener(new DialogInterface.OnDismissListener(){public void onDismiss(DialogInterface d){configuring=false;start();}});
        dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(new android.view.View.OnClickListener(){public void onClick(android.view.View v){
            String address=server.getText().toString().trim(), secret=token.getText().toString().trim();
            try {
                URL u=new URL(address);
                if (!(u.getProtocol().equals("http") || u.getProtocol().equals("https")) || u.getHost().length()==0 || u.getUserInfo()!=null || u.getQuery()!=null || u.getRef()!=null || !(u.getPath().equals("")||u.getPath().equals("/"))) throw new Exception();
            } catch (Exception e) {server.setError("请输入有效的 HTTP / HTTPS 服务根地址");return;}
            if (secret.length()<1 || secret.length()>512 || !secret.matches("[!-~]+")) {token.setError("请输入有效的只读 Token");return;}
            while(address.endsWith("/"))address=address.substring(0,address.length()-1);
            prefs.edit().putString("server",address).putString("token",secret).putInt("interval",speed.getSelectedItemPosition()==1?200:500).putInt("brightness",brightness.getProgress()+10).putBoolean("night",night.isChecked()).commit();
            display.reset();dialog.dismiss();
        }});
    }
    EditText field(LinearLayout form,String label,String value,boolean secret) {
        TextView title=new TextView(this);title.setText(label);form.addView(title);
        EditText input=new EditText(this);input.setSingleLine(true);input.setContentDescription(label);
        input.setInputType(InputType.TYPE_CLASS_TEXT|(secret?InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_TEXT_VARIATION_URI));
        input.setText(value);form.addView(input);return input;
    }
}
