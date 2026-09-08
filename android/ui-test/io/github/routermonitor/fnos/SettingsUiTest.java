package io.github.routermonitor.fnos;

import android.app.Dialog;
import android.content.DialogInterface;
import android.widget.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(sdk={28,31,35})
public class SettingsUiTest {
    @Test @Config(qualifiers="night") public void darkThemeControlsCreate(){controlsCreateAndKeepValues();}
    @Test public void controlsCreateAndKeepValues(){
        MainActivity activity=Robolectric.buildActivity(MainActivity.class).create().get();
        SettingsUi.FormDialog builder=new SettingsUi.FormDialog(activity);
        LinearLayout form=new LinearLayout(builder.context);
        EditText input=SettingsUi.field(form,"只读 Token",true);input.setText("test-value");
        SettingsUi.Level level=new SettingsUi.Level(form,"亮度",30,10,100);
        SettingsUi.Rate rate=new SettingsUi.Rate(form,200);
        CheckBox check=SettingsUi.check(builder.context);form.addView(check);check.setChecked(true);
        Dialog dialog=builder.create(form);dialog.show();
        assertNotNull(builder.saveButton());assertEquals(30,level.value());assertEquals(1,rate.position());assertTrue(check.isChecked());
        assertEquals("test-value",input.getText().toString());
        if(SettingsUi.modern()){
            assertTrue(input instanceof com.google.android.material.textfield.TextInputEditText);
            assertTrue(check instanceof com.google.android.material.checkbox.MaterialCheckBox);
            assertNotNull(level.current);level.current.setValue(72);assertEquals(72,level.value());
        }else{assertEquals(EditText.class,input.getClass());level.old.setProgress(62);assertEquals(72,level.value());}
        dialog.dismiss();activity.finish();
    }
    @Test public void fullSettingsCanOpenAndCancel(){
        MainActivity activity=Robolectric.buildActivity(MainActivity.class).create().get();
        activity.settings();
        Dialog dialog=org.robolectric.shadows.ShadowDialog.getLatestDialog();
        assertNotNull(dialog);assertTrue(dialog.isShowing());assertTrue(activity.configuring);
        dialog.dismiss();org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
        // A deferred first-run opening is intentional when no token was configured.
        assertEquals("",activity.prefs.getString("token",""));activity.finish();
    }
    static java.util.List<EditText> inputs(android.view.View view){
        java.util.List<EditText> result=new java.util.ArrayList<>();
        if(view instanceof EditText && !(view instanceof AutoCompleteTextView))result.add((EditText)view);
        if(view instanceof android.view.ViewGroup){android.view.ViewGroup group=(android.view.ViewGroup)view;for(int i=0;i<group.getChildCount();i++)result.addAll(inputs(group.getChildAt(i)));}
        return result;
    }
    @Test public void saveUsesSamePreferencesAndValidation(){
        MainActivity activity=Robolectric.buildActivity(MainActivity.class).create().get();activity.settings();
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
        Dialog dialog=org.robolectric.shadows.ShadowDialog.getLatestDialog();
        java.util.List<EditText> fields=inputs(dialog.getWindow().getDecorView());assertEquals(2,fields.size());
        Button save=SettingsUi.modern()?((androidx.appcompat.app.AlertDialog)dialog).getButton(-1):((android.app.AlertDialog)dialog).getButton(-1);
        save.performClick();assertTrue(dialog.isShowing());assertNotNull(fields.get(0).getError());
        fields.get(0).setText("http://192.168.1.2:18199");fields.get(1).setText("fixture-token");save.performClick();
        assertEquals("fixture-token",activity.prefs.getString("token",""));assertEquals(30,activity.prefs.getInt("brightness",0));assertFalse(dialog.isShowing());activity.finish();
    }
    @Test @Config(sdk=31) public void materialTimePickerOpens(){
        org.robolectric.android.controller.ActivityController<MainActivity> controller=Robolectric.buildActivity(MainActivity.class).create().start().resume();
        MainActivity activity=controller.get();SettingsUi.FormDialog theme=new SettingsUi.FormDialog(activity);
        Button button=SettingsUi.button(theme.context);int[] times={1380,420};
        SettingsUi.time(activity,button,"开启时间",times,0);activity.getSupportFragmentManager().executePendingTransactions();
        com.google.android.material.timepicker.MaterialTimePicker picker=(com.google.android.material.timepicker.MaterialTimePicker)activity.getSupportFragmentManager().findFragmentByTag("time-picker");
        assertNotNull(picker);assertNotNull(picker.getDialog());assertTrue(picker.getDialog().isShowing());assertEquals(23,picker.getHour());assertEquals(0,picker.getMinute());
        picker.dismiss();controller.pause().stop().destroy();
    }
}
