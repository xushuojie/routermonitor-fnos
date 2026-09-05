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
    final android.text.TextPaint paint=new android.text.TextPaint(3);
    final RectF rect=new RectF();
    final Path path=new Path();
    final DisplayMath.History history=new DisplayMath.History();
    final SimpleDateFormat hm=new SimpleDateFormat("HH:mm",Locale.US), seconds=new SimpleDateFormat(":ss",Locale.US), date=new SimpleDateFormat("MM 月 dd 日 · EEEE",Locale.CHINA);
    JSONObject data=new JSONObject();
    long netAt, statusAt, clockAt, serverClock, pageAt=SystemClock.elapsedRealtime(), slideAt;
    long netSuccess, statusSuccess, failures, drawMaxUs;
    double rx=Double.NaN, tx=Double.NaN, networkAge, snapshotAge, ceiling=1000;
    final DisplayMath.Axis axis=new DisplayMath.Axis();
    String serverHost="—";
    double visiblePeak;
    String netError="等待连接", statusError="等待连接";
    int page, previousPage;
    float scale=1, offsetX, offsetY, touchX, touchY;
    boolean longPressed,dragged;
    float scroll,lastTouchY;
    int insetLeft,insetTop,insetRight,insetBottom;
    DisplayLayout layout;
    void relayout(){layout=null;scroll=0;invalidate();}
    void insets(int l,int t,int r,int b){insetLeft=l;insetTop=t;insetRight=r;insetBottom=b;relayout();}
    @Override protected void onSizeChanged(int w,int h,int oldw,int oldh){relayout();}
    void ensureLayout(){
        float unit=getResources().getDisplayMetrics().density*getResources().getConfiguration().fontScale/1.5f;
        float w=Math.max(1,getWidth()-insetLeft-insetRight)/unit,h=Math.max(1,getHeight()-insetTop-insetBottom)/unit;
        if(layout==null||unit!=scale||layout.width!=w){scale=unit;layout=new DisplayLayout(w,h,activity.prefs.getBoolean("expand",true));}
        offsetX=insetLeft;offsetY=insetTop;
        scroll=Math.max(0,Math.min(scroll,Math.max(0,layout.contentHeight-h)));
    }
    float fitted(float preferred,String reference,float width){paint.setTextSize(preferred);return Math.min(preferred,preferred*Math.max(1,width)/Math.max(1,paint.measureText(reference)));}
    void label(Canvas c,String value,float x,float y,float size,int color,float width,boolean alignRight){
        paint.setTextSize(size);
        String shown=android.text.TextUtils.ellipsize(value, paint, Math.max(1,width), android.text.TextUtils.TruncateAt.END).toString();
        if(alignRight)right(c,shown,x,y,size,color);else text(c,shown,x,y,size,color);
    }

    MonitorView(MainActivity activity) {
        super(activity);this.activity=activity;setFocusable(true);setClickable(true);
        paint.setTypeface(Typeface.create("sans-serif",Typeface.NORMAL));
        TimeZone zone=TimeZone.getTimeZone("Asia/Shanghai");hm.setTimeZone(zone);seconds.setTimeZone(zone);date.setTimeZone(zone);
        setContentDescription("NAS 监控：网速、时间、硬盘读写、轮播信息和功率 CPU GPU 内存。点击右上角或长按打开设置，点击轮播区切换。");
    }
    void reset() {
        data=new JSONObject();history.clear();netAt=statusAt=0;rx=tx=Double.NaN;netError=statusError="连接中";ceiling=axis.range=1000;axis.lowerSince=-1;invalidate();
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
        if(!epoch.equals(history.epoch)){history.clear();history.epoch=epoch;ceiling=axis.range=1000;axis.lowerSince=-1;}
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
        c.drawColor(BG);ensureLayout();
        c.save();c.clipRect(insetLeft,insetTop,getWidth()-insetRight,getHeight()-insetBottom);
        c.translate(offsetX,offsetY);c.scale(scale,scale);c.translate(0,-scroll);
        float w=layout.width;
        label(c,"FNOS / 桌面监控",24,35,18,MUTED,w>=780?170:w-60-Math.min(210,w*.42f),false);
        if(w>=780)label(c,"NAS · "+serverHost,210,35,16,MUTED,w-440,false);
        else label(c,"NAS · "+serverHost,24,57,16,MUTED,w-48,false);
        String connection=netFresh()&&statusFresh()&&netError.length()==0&&statusError.length()==0?"● 在线 · 设置":"● "+(netError.length()>0?netError:statusError.length()>0?statusError:"数据已过期");
        label(c,connection,w-24,35,18,connection.startsWith("● 在线")?GREEN:GOLD,Math.min(210,w*.42f),true);
        DisplayLayout.Box n=layout.network;
        boolean stacked=n.w<360;float half=stacked?n.w:(n.w-20)/2;
        speed(c,n.x,n.y,half,"↑ 上传",netFresh()?tx:Double.NaN,UP);
        speed(c,stacked?n.x:n.x+half+20,stacked?n.y+110:n.y,half,"↓ 下载",netFresh()?rx:Double.NaN,DOWN);
        chart(c,now,n.x,n.y+(stacked?230:120),n.w,n.h-(stacked?342:184));
        line(c,n.x,n.bottom()-(stacked?112:56),n.x+n.w,n.bottom()-(stacked?112:56),LINE,1);
        disk(c,n.x,n.bottom()-(stacked?71:15),half,"R 读取",metric("disk_io","read_speed",true),GREEN);
        disk(c,stacked?n.x:n.x+half+20,n.bottom()-15,half,"W 写入",metric("disk_io","write_speed",true),GOLD);
        DisplayLayout.Box clock=layout.clock;
        Date wall=new Date(serverClock>0?serverClock+now-clockAt:System.currentTimeMillis());
        float size=fitted(Math.min(180,clock.h*.8f),"88:88",(clock.w-12)/1.25f);
        paint.setTextSize(size);float hoursWidth=paint.measureText(hm.format(wall));
        paint.setTextSize(size*.375f);float clockX=clock.x+(clock.w-hoursWidth-3-paint.measureText(seconds.format(wall)))/2;
        float baseline=clock.y+(clock.h-size-30)/2+size*.85f;
        text(c,hm.format(wall),clockX,baseline,size,TEXT);
        text(c,seconds.format(wall),clockX+hoursWidth+3,baseline,size*.375f,MUTED);
        paint.setTextSize(20);text(c,date.format(wall),clock.x+(clock.w-paint.measureText(date.format(wall)))/2,baseline+34,20,MUTED);
        if(layout.expanded){for(int i=0;i<4;i++)carousel(c,i,layout.auxiliary[i],false);}
        else {
            if(now-pageAt>=5000)nextPage(now);
            DisplayLayout.Box box=layout.auxiliary[0];panel(c,box.x,box.y,box.w,box.h);
            c.save();c.clipRect(box.x,box.y,box.x+box.w,box.bottom()-26);
            float progress=slideAt==0?1:Math.min(1,(now-slideAt)/180f);
            if(progress<1){float eased=1-(1-progress)*(1-progress);c.save();c.translate(-box.w*eased,0);carousel(c,previousPage,box,true);c.restore();c.save();c.translate(box.w*(1-eased),0);carousel(c,page,box,true);c.restore();postInvalidateDelayed(33);}else carousel(c,page,box,true);
            c.restore();
            for(int i=0;i<4;i++){paint.setColor(i==page?TEXT:Color.rgb(94,127,140));c.drawCircle(box.x+box.w/2-36+i*24,box.bottom()-16,3.5f,paint);}
        }
        metricCard(c,layout.metrics[0],"整机功率",metric("ups","watts",false),"W",35,DOWN);
        metricCard(c,layout.metrics[1],"CPU",metric("cpu","percent",false),"%",100,UP);
        metricCard(c,layout.metrics[2],"GPU",metric("gpu","utilization",false),"%",100,DOWN);
        metricCard(c,layout.metrics[3],"内存",metric("memory","percent",false),"%",100,GREEN);
        c.restore();drawMaxUs=Math.max(drawMaxUs,(System.nanoTime()-began)/1000);
    }
    void speed(Canvas c,float x,float y,float width,String name,double n,int color) {
        String[] parts=DisplayMath.amount(n,true);
        text(c,name,x,y+20,20,color);right(c,parts[1],x+width,y+20,28,MUTED);
        text(c,parts[0],x,y+100,fitted(86,"88.8",width),TEXT);
    }
    void disk(Canvas c,float x,float baseline,float width,String name,double n,int color) {
        String[] parts=DisplayMath.amount(n,true);
        text(c,name,x,baseline,18,color);
        text(c,parts[0],x+62,baseline,fitted(36,"88.8",width-120),TEXT);
        right(c,parts[1],x+width,baseline,18,MUTED);
    }
    void metricCard(Canvas c,DisplayLayout.Box box,String title,double n,String unit,double limit,int color) {
        float x=box.x,y=box.y,w=box.w;
        panel(c,x,y,w,box.h);text(c,title,x+16,y+34,18,MUTED);
        if(limit==35)right(c,"35W",x+w-16,y+34,18,MUTED);
        if(title.equals("CPU"))right(c,value(metric("temperature_summary","cpu",false),"%.0f°C"),x+w-16,y+34,18,GOLD);
        float size=fitted(44,"888.8",w-56);String formatted=value(n,unit.equals("W")?"%.1f":"%.0f");text(c,formatted,x+16,y+84,size,TEXT);
        paint.setTextSize(size);float width=paint.measureText(formatted);text(c,unit,x+19+width,y+84,20,MUTED);
        paint.setColor(LINE);rect.set(x+16,box.bottom()-16,x+w-16,box.bottom()-8);c.drawRoundRect(rect,3,3,paint);
        if(DisplayMath.valid(n)&&n>0){paint.setColor(color);rect.right=x+16+(float)((w-32)*Math.min(1,n/limit));c.drawRoundRect(rect,3,3,paint);}
    }
    void nextPage(long now){previousPage=page;page=(page+1)%4;pageAt=slideAt=now;invalidate();}
    void carousel(Canvas c,int p,DisplayLayout.Box box,boolean rotating) {
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
        if(!rotating)panel(c,box.x,box.y,box.w,box.h);
        label(c,title,box.x+16,box.y+34,18,MUTED,box.w-(rotating?88:32),false);
        if(rotating)right(c,(p+1)+" / 4",box.x+box.w-16,box.y+34,18,MUTED);
        float leftX=box.x+box.w*.125f,rightX=box.x+box.w*.875f,slot=box.w*.375f-10;
        boolean stacked=box.h>180;
        if(stacked)slot=box.w*.75f;
        float secondY=stacked?150:78;
        float size=fitted(30,"888.8 GB",slot);
        text(c,left,leftX,box.y+78,size,TEXT);right(c,rightValue,rightX,box.y+secondY,size,TEXT);
        label(c,leftLabel,leftX,box.y+104,16,p==0?UP:MUTED,slot,false);
        label(c,rightLabel,rightX,box.y+secondY+26,16,p==0?DOWN:MUTED,slot,true);
    }
    void chart(Canvas c,long now,float x0,float y0,float width,float height) {
        double end=history.size>0?history.time[history.index(history.size-1)]+Math.min(65,netAge()/1000d)+networkAge:0;
        double maximum=0;
        for(int i=0;i<history.size;i++){int j=history.index(i);if(history.time[j]<end-60||history.time[j]>end)continue;if(DisplayMath.valid(history.rx[j]))maximum=Math.max(maximum,history.rx[j]);if(DisplayMath.valid(history.tx[j]))maximum=Math.max(maximum,history.tx[j]);}
        visiblePeak=maximum;ceiling=axis.update(maximum,now);
        String[] top=DisplayMath.amount(ceiling,true);text(c,top[0]+" "+top[1],x0,y0+16,16,MUTED);right(c,"网络趋势 · 近 60 秒",x0+width,y0+16,16,MUTED);
        float topY=y0+30,bottomY=y0+height-26;
        line(c,x0,topY,x0+width,topY,LINE,1);line(c,x0,(topY+bottomY)/2,x0+width,(topY+bottomY)/2,LINE,1);line(c,x0,bottomY,x0+width,bottomY,LINE,1);
        c.save();c.clipRect(x0,topY-1,x0+width,bottomY+2);
        for(int series=0;series<2;series++) {
            path.reset();boolean connected=false;
            for(int i=0;i<history.size;i++) {
                int j=history.index(i);double t=history.time[j],v=series==0?history.tx[j]:history.rx[j];
                if(t<end-60||t>end||!DisplayMath.valid(v)){connected=false;continue;}
                float x=(float)(x0+(t-(end-60))/60*width),y=(float)(bottomY-v/ceiling*(bottomY-topY));
                if(!connected||history.gap[j])path.moveTo(x,y);else path.lineTo(x,y);connected=true;
            }
            paint.setColor(series==0?UP:DOWN);paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(2);c.drawPath(path,paint);paint.setStyle(Paint.Style.FILL);
        }
        c.restore();text(c,"0 · −60s",x0,y0+height-4,16,MUTED);text(c,"−30s",x0+width/2-20,y0+height-4,16,MUTED);right(c,"现在",x0+width,y0+height-4,16,MUTED);
    }
    final Runnable hold=new Runnable(){public void run(){longPressed=true;activity.settings();}};
    @Override public boolean onTouchEvent(MotionEvent event) {
        ensureLayout();float x=(event.getX()-offsetX)/scale,y=(event.getY()-offsetY)/scale+scroll;
        if(event.getAction()==MotionEvent.ACTION_DOWN){touchX=x;touchY=y;lastTouchY=event.getY();longPressed=dragged=false;postDelayed(hold,650);return true;}
        if(event.getAction()==MotionEvent.ACTION_MOVE){
            if(Math.abs(x-touchX)+Math.abs(y-touchY)>12){dragged=true;removeCallbacks(hold);}
            if(dragged){scroll+=(lastTouchY-event.getY())/scale;ensureLayout();invalidate();}lastTouchY=event.getY();return true;
        }
        if(event.getAction()==MotionEvent.ACTION_CANCEL){removeCallbacks(hold);return true;}
        if(event.getAction()==MotionEvent.ACTION_UP){removeCallbacks(hold);if(!longPressed&&!dragged){if(y<72&&x>layout.width-220)activity.settings();else if(!layout.expanded&&layout.auxiliary[0].contains(x,y))nextPage(SystemClock.elapsedRealtime());performClick();}return true;}
        return true;
    }
    @Override public boolean performClick(){super.performClick();return true;}
    @Override public boolean onKeyDown(int key,android.view.KeyEvent e){
        if(key==android.view.KeyEvent.KEYCODE_DPAD_CENTER||key==android.view.KeyEvent.KEYCODE_ENTER){activity.settings();return true;}
        if(key==android.view.KeyEvent.KEYCODE_DPAD_RIGHT){nextPage(SystemClock.elapsedRealtime());return true;}
        if(key==android.view.KeyEvent.KEYCODE_DPAD_DOWN||key==android.view.KeyEvent.KEYCODE_DPAD_UP){scroll+=key==android.view.KeyEvent.KEYCODE_DPAD_DOWN?120:-120;ensureLayout();invalidate();return true;}
        return super.onKeyDown(key,e);
    }
    @Override public void onInitializeAccessibilityNodeInfo(android.view.accessibility.AccessibilityNodeInfo info){
        super.onInitializeAccessibilityNodeInfo(info);
        info.setContentDescription("NAS 监控。上传 "+amount(netFresh()?tx:Double.NaN)+"每秒，下载 "+amount(netFresh()?rx:Double.NaN)+"每秒。功率 "+value(metric("ups","watts",false),"%.1f瓦")+"，CPU "+value(metric("cpu","percent",false),"%.0f%%")+"，温度 "+value(metric("temperature_summary","cpu",false),"%.0f摄氏度")+"，GPU "+value(metric("gpu","utilization",false),"%.0f%%")+"，内存 "+value(metric("memory","percent",false),"%.0f%%")+"。双击打开设置，上下滚动查看。");
        info.addAction(16);info.setScrollable(true);info.addAction(4096);info.addAction(8192);
    }
    @Override public boolean performAccessibilityAction(int action,android.os.Bundle args){
        if(action==16){activity.settings();return true;}
        if(action==4096||action==8192){scroll+=action==4096?240:-240;ensureLayout();invalidate();return true;}
        return super.performAccessibilityAction(action,args);
    }
    @Override protected void onDetachedFromWindow(){removeCallbacks(hold);super.onDetachedFromWindow();}
}
