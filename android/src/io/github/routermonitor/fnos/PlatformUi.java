package io.github.routermonitor.fnos;

import android.os.Build;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;

/** Full-bleed dashboard; keep new platform types outside old Android execution paths. */
final class PlatformUi {
    static void fullscreen(MainActivity activity){
        Window window=activity.getWindow();
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        int flags=View.SYSTEM_UI_FLAG_LAYOUT_STABLE|View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                |View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION|View.SYSTEM_UI_FLAG_FULLSCREEN
                |View.SYSTEM_UI_FLAG_HIDE_NAVIGATION;
        if(Build.VERSION.SDK_INT>=19)flags|=View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
        window.getDecorView().setSystemUiVisibility(flags);
        if(Build.VERSION.SDK_INT>=30)Window30.fullscreen(window);
        else if(Build.VERSION.SDK_INT>=28)Window28.cutout(window);
    }
    static void install(MonitorView view){view.insets(0,0,0,0);if(Build.VERSION.SDK_INT>=20)Insets20.install(view);}
    static final class Insets20 {
        static void install(final MonitorView view){
            view.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener(){
                public android.view.WindowInsets onApplyWindowInsets(View v,android.view.WindowInsets insets){
                    // Ignore cutout, system-bar and gesture strips. Only desktop captions reserve space.
                    if(Build.VERSION.SDK_INT>=30)Window30.apply(view,insets);
                    else view.insets(0,0,0,0);
                    return insets;
                }
            });view.requestApplyInsets();
        }
    }
    static final class Window28 {
        static void cutout(Window window){
            WindowManager.LayoutParams params=window.getAttributes();
            params.layoutInDisplayCutoutMode=WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            window.setAttributes(params);
        }
    }
    static final class Window30 {
        static void fullscreen(Window window){
            window.setDecorFitsSystemWindows(false);
            WindowManager.LayoutParams params=window.getAttributes();
            params.layoutInDisplayCutoutMode=WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
            window.setAttributes(params);
            android.view.WindowInsetsController controller=window.getInsetsController();
            if(controller!=null){
                controller.setSystemBarsBehavior(android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                controller.hide(android.view.WindowInsets.Type.systemBars());
            }
        }
        static void apply(MonitorView view,android.view.WindowInsets insets){
            android.graphics.Insets i=insets.getInsets(android.view.WindowInsets.Type.captionBar());
            view.insets(i.left,i.top,i.right,i.bottom);
        }
    }
}
