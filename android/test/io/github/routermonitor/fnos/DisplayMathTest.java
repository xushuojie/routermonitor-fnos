package io.github.routermonitor.fnos;

public final class DisplayMathTest {
    public static void main(String[] args) {
        java.util.HashSet<String> positions=new java.util.HashSet<String>();
        for(int minute=0;minute<81;minute++){
            int x=DisplayMath.oledShift(minute*60000L,false),y=DisplayMath.oledShift(minute*60000L,true);
            if(Math.abs(x)>4||Math.abs(y)>4)throw new AssertionError("shift outside reserved pixels");
            positions.add(x+":"+y);
            for(int size:new int[]{270,540,1440,3200}){
                if(4+x<0||4+x+size-8>size||4+y<0||4+y+size-8>size)throw new AssertionError("shift clips viewport");
            }
            if(x!=DisplayMath.oledShift(minute*60000L+59999,false))throw new AssertionError("shift must stay still for a minute");
        }
        if(positions.size()!=81||DisplayMath.oledShift(81*60000L,false)!=DisplayMath.oledShift(0,false))throw new AssertionError("shift coverage and repeat");
        for(float base:new float[]{.01f,.1f,.3f,1f}){
            if(Math.abs(DisplayMath.idleBrightness(base,300000,true)-base)>.0001)throw new AssertionError("dim starts too soon");
            if(Math.abs(DisplayMath.idleBrightness(base,360000,true)-Math.max(.01f,base*.6f))>.0001)throw new AssertionError("dim target");
            if(DisplayMath.idleBrightness(base,Long.MAX_VALUE,false)!=base||DisplayMath.idleBrightness(base,0,true)!=base)throw new AssertionError("disabled or awakened brightness");
            float previous=base;
            for(long idle=300000;idle<=400000;idle+=1000){float v=DisplayMath.idleBrightness(base,idle,true);if(v>previous||v<.01f)throw new AssertionError("brightness fade");previous=v;}
        }
        if (!DisplayMath.amount(999999, true)[1].equals("MB/s")) throw new AssertionError("unit overflow");
        if (!DisplayMath.amount(Double.NaN, true)[0].equals("—")) throw new AssertionError("missing data");
        if (!DisplayMath.amount(18917997150208d, false)[1].equals("TB")) throw new AssertionError("decimal capacity");
        if (DisplayMath.ceiling(13800000) != 14000000) throw new AssertionError("fine chart scale");
        DisplayMath.Axis axis = new DisplayMath.Axis();
        if (axis.update(12000000, 0) != 14000000) throw new AssertionError("expand immediately");
        if (axis.update(1000000, 100) != 14000000 || axis.update(1000000, 2099) != 14000000) throw new AssertionError("shrink debounce");
        if (axis.update(1000000, 2100) != 1200000) throw new AssertionError("shrink after two seconds");
        if (axis.update(20000000, 2200) < 20000000) throw new AssertionError("never clip a visible peak");
        if(!DisplayMath.nightActive(1380,1380,420)||!DisplayMath.nightActive(0,1380,420)||DisplayMath.nightActive(420,1380,420))throw new AssertionError("overnight boundaries");
        if(!DisplayMath.nightActive(600,540,1020)||DisplayMath.nightActive(500,540,1020)||!DisplayMath.nightActive(600,0,0))throw new AssertionError("daytime and all day");
        for(float[] screen:new float[][]{{960,540},{540,960},{1920,1200},{1200,1920},{270,480},{480,270}}){
            DisplayLayout layout=new DisplayLayout(screen[0],screen[1],true);
            java.util.ArrayList<DisplayLayout.Box> boxes=new java.util.ArrayList<DisplayLayout.Box>();
            boxes.add(layout.clock);boxes.add(layout.network);java.util.Collections.addAll(boxes,layout.auxiliary);java.util.Collections.addAll(boxes,layout.metrics);
            for(int i=0;i<boxes.size();i++){
                DisplayLayout.Box b=boxes.get(i);
                if(b.x<0||b.y<0||b.w<=0||b.h<=0||b.x+b.w>screen[0]+.01||b.bottom()>layout.contentHeight)throw new AssertionError("layout bounds");
                for(int j=0;j<i;j++){DisplayLayout.Box a=boxes.get(j);if(a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.bottom()&&a.bottom()>b.y)throw new AssertionError("overlapping modules");}
            }
        }
        if(!new DisplayLayout(960,540,true).wide||new DisplayLayout(540,960,true).wide||!new DisplayLayout(1920,1200,true).expanded)throw new AssertionError("adaptive mode");
        for(float[] viewport:new float[][]{{1220,510},{1180,500},{1100,470},{960,540}}){
            float unit=DisplayLayout.fittedUnit(viewport[0],viewport[1],1,1,true);
            DisplayLayout layout=new DisplayLayout(viewport[0]/unit,viewport[1]/unit,true);
            if(layout.contentHeight*unit>viewport[1]+.01f)throw new AssertionError("landscape fits usable height");
            if(unit<.85f||unit>1)throw new AssertionError("bounded compact scaling");
        }
        // Insets reduce actual available pixels; density alone is not the viewport.
        float compact=DisplayLayout.fittedUnit(3000,1260,2.5f,1,true);
        DisplayLayout phone=new DisplayLayout(3000/compact,1260/compact,true);
        if(phone.contentHeight*compact>1260.01f)throw new AssertionError("dense phone safe area");
        if(DisplayLayout.fittedUnit(1220,510,1,1.5f,true)!=1)throw new AssertionError("respect accessibility font scale");
        if(DisplayLayout.fittedUnit(1220,350,1,1,true)!=1)throw new AssertionError("short split screen scrolls without tiny text");
        if(DisplayLayout.clampScroll(100,540,600)!=0||DisplayLayout.clampScroll(100,540,500)!=40)throw new AssertionError("height resize clamps scroll");
        if(new DisplayLayout(1200,500,true).height==new DisplayLayout(1200,600,true).height)throw new AssertionError("height participates in layout identity");
        DisplayMath.History h = new DisplayMath.History();
        for (int i=0;i<400;i++) h.add(i, i*.2, i, i, false);
        if (h.size != 320 || h.seq != 399 || h.time[h.index(0)] != 16) throw new AssertionError("bounded history");
        h.add(399, 80, 1, 1, false);
        if (h.seq != 399) throw new AssertionError("duplicate");
        h.add(402, 80.4, 1, 1, false);
        if (!h.gap[h.index(319)]) throw new AssertionError("gap");
        h.clear();
        if (h.size != 0 || h.seq != -1) throw new AssertionError("restart");
        System.out.println("Display formatting, scale, sequence and bounded history: OK");
    }
}
