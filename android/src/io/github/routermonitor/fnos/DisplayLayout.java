package io.github.routermonitor.fnos;

/** Layout units are 2/3 sp: the original 960px phone retains its typography. */
final class DisplayLayout {
    static final class Box {
        final float x,y,w,h;
        Box(float x,float y,float w,float h){this.x=x;this.y=y;this.w=w;this.h=h;}
        float bottom(){return y+h;}
        boolean contains(float px,float py){return px>=x&&px<=x+w&&py>=y&&py<=y+h;}
    }
    final Box network,clock;
    final Box[] auxiliary,metrics=new Box[4];
    final boolean wide,expanded;
    final float width,contentHeight;
    DisplayLayout(float w,float h,boolean expand) {
        width=w;float margin=24,gap=20,inner=Math.max(1,w-48),top=64;
        wide=w>=900&&w>h;
        expanded=expand&&((wide&&w>=1260&&h>=1000)||(!wide&&w>=900&&h>=1300));
        int columns=w>=780?4:w>=480?2:1;
        float metricHeight=(4/columns)*136-20;
        float end;
        if(wide){
            float col=(inner-gap)/2;
            float hero=Math.max(316,h-top-24-116-gap-(expanded?320:0));
            network=new Box(margin,top,col,hero);
            clock=new Box(margin+col+gap,top,col,expanded?hero:hero-160);
            if(expanded){auxiliary=grid(margin,top+hero+gap,inner,140,2,4);end=auxiliary[3].bottom();}
            else {auxiliary=new Box[]{new Box(clock.x,top+hero-140,col,140)};end=top+hero;}
        }else{
            float clockHeight=Math.min(240,Math.max(150,inner*.36f));
            float netHeight=inner<360?440:Math.min(430,Math.max(316,inner*.64f));
            float auxHeight=expanded?300:w<480?210:140;
            float extra=Math.max(0,h-margin-top-clockHeight-netHeight-auxHeight-3*gap-metricHeight);
            clock=new Box(margin,top,inner,clockHeight+extra*.45f);
            network=new Box(margin,clock.bottom()+gap,inner,netHeight+extra*.55f);
            auxiliary=grid(margin,network.bottom()+gap,inner,w<480?210:140,expanded?2:1,expanded?4:1);
            end=auxiliary[auxiliary.length-1].bottom();
        }
        float metricTop=Math.max(end+gap,h-margin-((4/columns)*136-20));
        Box[] cards=grid(margin,metricTop,inner,116,columns,4);
        System.arraycopy(cards,0,metrics,0,4);
        contentHeight=metrics[3].bottom()+margin;
    }
    static Box[] grid(float x,float y,float w,float height,int cols,int count){
        Box[] boxes=new Box[count];float width=(w-(cols-1)*20)/cols;
        for(int i=0;i<count;i++)boxes[i]=new Box(x+(i%cols)*(width+20),y+(i/cols)*(height+20),width,height);
        return boxes;
    }
}
