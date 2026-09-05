package io.github.routermonitor.fnos;

import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/** One bounded native canvas. All state is owned by the UI thread. */
final class MonitorView extends View {
    static final int BG=Color.rgb(9,21,28), PANEL=Color.rgb(17,36,45), LINE=Color.rgb(41,66,78);
    static final int TEXT=Color.rgb(241,246,248), MUTED=Color.rgb(174,194,204);
    static final int UP=Color.rgb(255,135,155), DOWN=Color.rgb(100,212,255), GREEN=Color.rgb(102,223,187), GOLD=Color.rgb(244,196,119);
    final MainActivity activity;
    final Paint paint=new Paint(3);
    final RectF rect=new RectF();
    final Path path=new Path();
    final DisplayMath.History history=new DisplayMath.History();
    final SimpleDateFormat hm=new SimpleDateFormat("HH:mm",Locale.US), seconds=new SimpleDateFormat(":ss",Locale.US), date=new SimpleDateFormat("MM 月 dd 日 · EEEE",Locale.CHINA);
    JSONObject data=new JSONObject();
    long netAt, statusAt, clockAt, serverClock, pageAt=SystemClock.elapsedRealtime(), slideAt;
    long netSuccess, statusSuccess, failures, drawMaxUs;
    double rx=Double.NaN, tx=Double.NaN, networkAge, snapshotAge, ceiling=1000;
    long smallerSince;
    String netError="等待连接", statusError="等待连接";
    int page, previousPage;
    float scale=1, offsetX, offsetY, touchX, touchY;
    boolean longPressed;

    MonitorView(MainActivity activity) {
        super(activity);this.activity=activity;setFocusable(true);setClickable(true);
        paint.setTypeface(Typeface.create("sans-serif",Typeface.NORMAL));
        TimeZone zone=TimeZone.getTimeZone("Asia/Shanghai");hm.setTimeZone(zone);seconds.setTimeZone(zone);date.setTimeZone(zone);
        setContentDescription("NAS 监控：网速、时间、硬盘读写、轮播信息和功率 CPU GPU 内存。点击右上角或长按打开设置，点击轮播区切换。");
    }
    void reset() {
        data=new JSONObject();history.clear();netAt=statusAt=0;rx=tx=Double.NaN;netError=statusError="连接中";ceiling=1000;invalidate();
    }
    long netAge(){return netAt==0?Long.MAX_VALUE:SystemClock.elapsedRealtime()-netAt;}
    long statusAge(){return statusAt==0?Long.MAX_VALUE:SystemClock.elapsedRealtime()-statusAt;}
    boolean netFresh(){return netAge()/1000d+networkAge<=3;}
    boolean statusFresh(){return statusAge()/1000d+snapshotAge<=3;}
    static double number(JSONObject object,String key) {
        if(object==null)return Double.NaN;
        double value=object.optDouble(key,Double.NaN);return DisplayMath.valid(value)?value:Double.NaN;
    }
    static double point(JSONArray object,int index) {
        double value=object.optDouble(index,Double.NaN);return DisplayMath.valid(value)?value:Double.NaN;
    }
    void network(JSONObject value,long received) {
        String epoch=value.optString("epoch","");
        if(!epoch.equals(history.epoch)){history.clear();history.epoch=epoch;ceiling=1000;}
        JSONArray rates=value.optJSONArray("rate"),points=value.optJSONArray("points");
        if(rates==null||points==null){failed(true,"数据格式错误");return;}
        rx=point(rates,0);tx=point(rates,1);
        for(int i=0;i<points.length();i++) {
            JSONArray p=points.optJSONArray(i);if(p==null||p.length()!=4)continue;
            history.add(p.optLong(0,-1),point(p,1),point(p,2),point(p,3),i==0&&value.optBoolean("gap",false));
        }
        networkAge=value.optDouble("age",0);netAt=received;netError="";netSuccess++;invalidate();
    }
    void status(JSONObject value,long received,long clock) {
        data=value;statusAt=received;snapshotAge=value.optDouble("age",0);statusError="";statusSuccess++;
        if(clock>0){serverClock=clock;clockAt=received;}invalidate();
    }
    void failed(boolean net,String message){failures++;if(net)netError=message;else statusError=message;if(message.equals("Token 无效")){if(net)netAt=0;else statusAt=0;}invalidate();}
    double metric(String group,String field,boolean needsValid) {
        if(!statusFresh())return Double.NaN;
        JSONObject obj=data.optJSONObject(group);
        if(obj==null||(needsValid&&!obj.optBoolean("valid",false)))return Double.NaN;
        if(group.equals("storage")||group.equals("temperature_summary")) {
            double age=number(data.optJSONObject("metric_age"),group.equals("storage")?"storage":"temperature");
            if(!DisplayMath.valid(age)||age+statusAge()/1000d>(group.equals("storage")?90:15))return Double.NaN;
        }
        return number(obj,field);
    }
    String value(double n,String format) {return DisplayMath.valid(n)?String.format(Locale.US,format,n):"—";}
    String amount(double n){String[] parts=DisplayMath.amount(n,false);return parts[0]+(DisplayMath.valid(n)?" "+parts[1]:"");}
    void text(Canvas c,String s,float x,float baseline,float size,int color) {
        paint.setStyle(Paint.Style.FILL);paint.setColor(color);paint.setTextSize(size);c.drawText(s,x,baseline,paint);
    }
    void right(Canvas c,String s,float x,float baseline,float size,int color) {paint.setTextSize(size);text(c,s,x-paint.measureText(s),baseline,size,color);}
    void line(Canvas c,float x,float y,float x2,float y2,int color,float width){paint.setColor(color);paint.setStrokeWidth(width);c.drawLine(x,y,x2,y2,paint);}
    void panel(Canvas c,float x,float y,float width,float height) {paint.setStyle(Paint.Style.FILL);paint.setColor(PANEL);rect.set(x,y,x+width,y+height);c.drawRoundRect(rect,12,12,paint);}

    @Override protected void onDraw(Canvas c) {
        long began=System.nanoTime(),now=SystemClock.elapsedRealtime();
        c.drawColor(BG);
        scale=Math.min(getWidth()/960f,getHeight()/540f);offsetX=(getWidth()-960*scale)/2;offsetY=(getHeight()-540*scale)/2;
        c.save();c.translate(offsetX,offsetY);c.scale(scale,scale);
        text(c,"FNOS / 桌面监控",24,39,18,MUTED);
        String connection=netFresh()&&statusFresh()&&netError.length()==0&&statusError.length()==0?"● 在线 · 设置":"● "+(netError.length()>0?netError:statusError.length()>0?statusError:"数据已过期")+" · 设置";
        right(c,connection,936,39,18,connection.startsWith("● 在线")?GREEN:GOLD);
        speed(c,24,"↑ 上传",netFresh()?tx:Double.NaN,UP);
        speed(c,257,"↓ 下载",netFresh()?rx:Double.NaN,DOWN);
        chart(c,now);
        line(c,24,318,936,318,LINE,1);
        disk(c,24,"R 读取",metric("disk_io","read_speed",true),GREEN);
        disk(c,490,"W 写入",metric("disk_io","write_speed",true),GOLD);
        Date wall=new Date(serverClock>0?serverClock+now-clockAt:System.currentTimeMillis());
        String hours=hm.format(wall);
        paint.setTextSize(96);float width=paint.measureText(hours);
        paint.setTextSize(40);float clockX=490+(446-width-3-paint.measureText(seconds.format(wall)))/2;
        text(c,hours,clockX,150,96,TEXT);
        text(c,seconds.format(wall),clockX+width+3,150,40,MUTED);
        paint.setTextSize(20);String day=date.format(wall);
        text(c,day,490+(446-paint.measureText(day))/2,178,20,MUTED);
        if(now-pageAt>=5000){nextPage(now);}
        panel(c,490,184,446,122);
        c.save();c.clipRect(490,184,936,286);
        float progress=slideAt==0?1:Math.min(1,(now-slideAt)/180f);
        if(progress<1){float eased=1-(1-progress)*(1-progress);c.save();c.translate(-446*eased,0);carousel(c,previousPage);c.restore();c.save();c.translate(446*(1-eased),0);carousel(c,page);c.restore();postInvalidateDelayed(33);}else carousel(c,page);
        c.restore();
        for(int i=0;i<4;i++){paint.setColor(i==page?TEXT:Color.rgb(94,127,140));c.drawCircle(683+i*20,295,3.5f,paint);}
        metricCard(c,24,"整机功率",metric("ups","watts",false),"W",35,DOWN);
        metricCard(c,257,"CPU",metric("cpu","percent",false),"%",100,UP);
        metricCard(c,490,"GPU",metric("gpu","utilization",false),"%",100,DOWN);
        metricCard(c,723,"内存",metric("memory","percent",false),"%",100,GREEN);
        c.restore();drawMaxUs=Math.max(drawMaxUs,(System.nanoTime()-began)/1000);
    }
    void speed(Canvas c,float x,String label,double n,int color) {
        String[] parts=DisplayMath.amount(n,true);
        text(c,label,x,84,20,color);right(c,parts[1],x+213,84,28,MUTED);
        text(c,parts[0],x,160,76,TEXT);
    }
    void disk(Canvas c,float x,String label,double n,int color) {
        String[] parts=DisplayMath.amount(n,true);
        text(c,label,x,361,18,color);text(c,parts[0],x+65,361,36,TEXT);
        right(c,parts[1],x+446,361,18,MUTED);
    }
    void metricCard(Canvas c,float x,String title,double n,String unit,double limit,int color) {
        panel(c,x,406,213,110);text(c,title,x+14,434,18,MUTED);
        if(limit==35)right(c,"35W",x+199,434,18,MUTED);
        String formatted=value(n,unit.equals("W")?"%.1f":"%.0f");text(c,formatted,x+14,478,40,TEXT);
        paint.setTextSize(40);float w=paint.measureText(formatted);text(c,unit,x+17+w,478,20,MUTED);
        paint.setColor(LINE);rect.set(x+14,497,x+199,505);c.drawRoundRect(rect,3,3,paint);
        if(DisplayMath.valid(n)&&n>0){paint.setColor(color);rect.right=x+14+(float)(185*Math.min(1,n/limit));c.drawRoundRect(rect,3,3,paint);}
    }
    void nextPage(long now){previousPage=page;page=(page+1)%4;pageAt=slideAt=now;invalidate();}
    void carousel(Canvas c,int p) {
        String title,left,leftLabel,rightValue,rightLabel;
        if(p==0) {
            double coverage=metric("traffic_24h","coverage_seconds",true);
            title=DisplayMath.valid(coverage)&&coverage<86390?"24h 流量 · 已覆盖 "+String.format(Locale.US,"%.1fh",coverage/3600):"近 24 小时流量";
            left=amount(metric("traffic_24h","tx_bytes",true));leftLabel="↑ 上传";
            rightValue=amount(metric("traffic_24h","rx_bytes",true));rightLabel="↓ 下载";
        } else if(p==1) {
            title="持续运行";double up=statusFresh()?number(data,"uptime"):Double.NaN;
            left=DisplayMath.valid(up)?((long)up/86400)+" 天":"—";leftLabel="开机时长";
            rightValue=DisplayMath.valid(up)?String.format(Locale.US,"%02d:%02d",((long)up/3600)%24,((long)up/60)%60):"—";rightLabel="小时 : 分钟";
        } else if(p==2) {
            title="设备温度";left=value(metric("temperature_summary","cpu",false),"%.0f°C");leftLabel="CPU 温度";
            rightValue=value(metric("temperature_summary","disk",false),"%.0f°C");rightLabel="硬盘最高";
        } else {
            title="存储空间";left=amount(metric("storage","total",true));leftLabel="总容量";
            rightValue=value(metric("storage","percent",true),"%.1f%%");rightLabel="已用 "+amount(metric("storage","used",true));
        }
        text(c,title,506,208,17,MUTED);right(c,(p+1)+" / 4",920,208,17,MUTED);
        text(c,left,506,253,28,TEXT);right(c,rightValue,920,253,28,TEXT);
        text(c,leftLabel,506,280,16,p==0?UP:MUTED);right(c,rightLabel,920,280,16,p==0?DOWN:MUTED);
    }
    void chart(Canvas c,long now) {
        double end=history.size>0?history.time[history.index(history.size-1)]+Math.min(65,netAge()/1000d)+networkAge:0;
        double maximum=0;
        for(int i=0;i<history.size;i++){int j=history.index(i);if(history.time[j]<end-60)continue;if(DisplayMath.valid(history.rx[j]))maximum=Math.max(maximum,history.rx[j]);if(DisplayMath.valid(history.tx[j]))maximum=Math.max(maximum,history.tx[j]);}
        double proposed=DisplayMath.ceiling(maximum*1.15);
        if(proposed>ceiling){ceiling=proposed;smallerSince=0;}
        else if(proposed<ceiling){if(smallerSince==0)smallerSince=now;if(now-smallerSince>10000){ceiling=proposed;smallerSince=0;}}else smallerSince=0;
        String[] top=DisplayMath.amount(ceiling,true);text(c,top[0]+" "+top[1],24,198,16,MUTED);right(c,"网络趋势 · 近 60 秒",470,198,16,MUTED);
        line(c,24,208,470,208,LINE,1);line(c,24,248,470,248,LINE,1);line(c,24,287,470,287,LINE,1);
        c.save();c.clipRect(24,207,470,289);
        for(int series=0;series<2;series++) {
            path.reset();boolean connected=false;
            for(int i=0;i<history.size;i++) {
                int j=history.index(i);double t=history.time[j],v=series==0?history.tx[j]:history.rx[j];
                if(t<end-60||t>end||!DisplayMath.valid(v)){connected=false;continue;}
                float x=(float)(24+(t-(end-60))/60*446),y=(float)(287-v/ceiling*79);
                if(!connected||history.gap[j])path.moveTo(x,y);else path.lineTo(x,y);connected=true;
            }
            paint.setColor(series==0?UP:DOWN);paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(2);c.drawPath(path,paint);paint.setStyle(Paint.Style.FILL);
        }
        c.restore();text(c,"0 · −60s",24,305,16,MUTED);text(c,"−30s",223,305,16,MUTED);right(c,"现在",470,305,16,MUTED);
    }
    final Runnable hold=new Runnable(){public void run(){longPressed=true;activity.settings();}};
    @Override public boolean onTouchEvent(MotionEvent event) {
        float x=(event.getX()-offsetX)/scale,y=(event.getY()-offsetY)/scale;
        if(event.getAction()==MotionEvent.ACTION_DOWN){touchX=x;touchY=y;longPressed=false;postDelayed(hold,650);return true;}
        if(event.getAction()==MotionEvent.ACTION_MOVE&&Math.abs(x-touchX)+Math.abs(y-touchY)>20)removeCallbacks(hold);
        if(event.getAction()==MotionEvent.ACTION_CANCEL){removeCallbacks(hold);return true;}
        if(event.getAction()==MotionEvent.ACTION_UP){removeCallbacks(hold);if(!longPressed){if(y<58&&x>600)activity.settings();else if(x>=490&&y>=184&&y<=306)nextPage(SystemClock.elapsedRealtime());performClick();}return true;}
        return true;
    }
    @Override public boolean performClick(){super.performClick();return true;}
    @Override protected void onDetachedFromWindow(){removeCallbacks(hold);super.onDetachedFromWindow();}
}
