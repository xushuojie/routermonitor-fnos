package io.github.routermonitor.fnos;

import android.os.Build;
import android.view.View;

/** Keep newer platform types out of the Android 4.3 execution path. */
final class PlatformUi {
    static void install(MonitorView view) {if(Build.VERSION.SDK_INT>=20)Insets20.install(view);}
    static final class Insets20 {
        static void install(final MonitorView view){
            view.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener(){
                public android.view.WindowInsets onApplyWindowInsets(View v,android.view.WindowInsets insets){
                    if(Build.VERSION.SDK_INT>=30)Insets30.apply(view,insets);
                    else view.insets(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
                    return insets;
                }
            });view.requestApplyInsets();
        }
    }
    static final class Insets30 {
        static void apply(MonitorView view,android.view.WindowInsets insets){
            android.graphics.Insets i=insets.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout()|android.view.WindowInsets.Type.systemGestures());
            view.insets(i.left,i.top,i.right,i.bottom);
        }
    }
}
