package io.github.routermonitor.fnos;

import java.util.Locale;

/** Formatting and bounded history shared by the renderer and the desktop check. */
final class DisplayMath {
    static boolean valid(double v) { return !Double.isNaN(v) && !Double.isInfinite(v) && v >= 0; }
    static String[] amount(double bytes, boolean speed) {
        if (!valid(bytes)) return new String[]{"—", speed ? "B/s" : "B"};
        String[] units = {"B", "KB", "MB", "GB", "TB", "PB"};
        int n = 0;
        while (bytes >= 1000 && n < units.length - 1) { bytes /= 1000; n++; }
        // Do not round 999.9 to 1000 in a fixed-width number slot.
        if (bytes >= 999.5 && n < units.length - 1) { bytes /= 1000; n++; }
        String value = String.format(Locale.US, bytes >= 100 || n == 0 ? "%.0f" : bytes >= 10 ? "%.1f" : "%.2f", bytes);
        if (value.endsWith(".00")) value = value.substring(0, value.length() - 3);
        else if (value.endsWith(".0")) value = value.substring(0, value.length() - 2);
        return new String[]{value, units[n] + (speed ? "/s" : "")};
    }
    static double ceiling(double max) {
        if (!valid(max) || max < 1000) return 1000;
        double base = Math.pow(10, Math.floor(Math.log10(max)));
        for (double step : new double[]{1, 2, 5, 10}) if (base * step >= max) return base * step;
        return base * 10;
    }
    static final class History {
        final double[] time = new double[320], rx = new double[320], tx = new double[320];
        final boolean[] gap = new boolean[320];
        int head, size;
        long seq = -1;
        String epoch = "";
        void clear() { head = size = 0; seq = -1; epoch = ""; }
        void add(long sequence, double stamp, double down, double up, boolean broken) {
            if (sequence <= seq || !valid(stamp)) return;
            if (size > 0 && stamp <= time[(head + 319) % 320]) return;
            gap[head] = broken || (seq >= 0 && sequence != seq + 1);
            time[head] = stamp; rx[head] = down; tx[head] = up;
            head = (head + 1) % 320; size = Math.min(320, size + 1); seq = sequence;
        }
        int index(int i) { return (head - size + 320 + i) % 320; }
    }
}
