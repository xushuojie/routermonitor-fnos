package io.github.routermonitor.fnos;

public final class DisplayMathTest {
    public static void main(String[] args) {
        if (!DisplayMath.amount(999999, true)[1].equals("MB/s")) throw new AssertionError("unit overflow");
        if (!DisplayMath.amount(Double.NaN, true)[0].equals("—")) throw new AssertionError("missing data");
        if (!DisplayMath.amount(18917997150208d, false)[1].equals("TB")) throw new AssertionError("decimal capacity");
        if (DisplayMath.ceiling(13800000) != 14000000) throw new AssertionError("fine chart scale");
        DisplayMath.Axis axis = new DisplayMath.Axis();
        if (axis.update(12000000, 0) != 14000000) throw new AssertionError("expand immediately");
        if (axis.update(1000000, 100) != 14000000 || axis.update(1000000, 2099) != 14000000) throw new AssertionError("shrink debounce");
        if (axis.update(1000000, 2100) != 1200000) throw new AssertionError("shrink after two seconds");
        if (axis.update(20000000, 2200) < 20000000) throw new AssertionError("never clip a visible peak");
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
