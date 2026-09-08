package io.github.routermonitor.fnos;

import android.app.Dialog;
import android.content.Context;
import android.content.DialogInterface;
import android.os.Build;
import android.view.View;
import android.view.ContextThemeWrapper;
import android.widget.*;

/** Explicit AOSP themes on older OS versions, bundled Google Material 3 on API 31+. */
final class SettingsUi {
    static boolean modern(){return Build.VERSION.SDK_INT>=31;}
    static boolean dark(Context c){return (c.getResources().getConfiguration().uiMode&48)==32;}
    static int platformTheme(boolean dark){
        if(Build.VERSION.SDK_INT>=21)return dark?android.R.style.Theme_Material:android.R.style.Theme_Material_Light;
        return dark?android.R.style.Theme_Holo:android.R.style.Theme_Holo_Light;
    }
    static final class FormDialog {
        final Context context;
        final android.app.AlertDialog.Builder old;
        final com.google.android.material.dialog.MaterialAlertDialogBuilder current;
        Dialog dialog;
        FormDialog(Context host){
            if(modern()){
                Context theme=new ContextThemeWrapper(host,dark(host)?com.google.android.material.R.style.Theme_Material3_Dark_NoActionBar:com.google.android.material.R.style.Theme_Material3_Light_NoActionBar);
                current=new com.google.android.material.dialog.MaterialAlertDialogBuilder(theme);old=null;context=current.getContext();
            }else{
                old=new android.app.AlertDialog.Builder(new ContextThemeWrapper(host,platformTheme(dark(host))));current=null;context=old.getContext();
            }
        }
        Dialog create(View content){
            if(current!=null)dialog=current.setTitle("NAS 显示终端设置").setView(content).setPositiveButton("保存并连接",null).setNegativeButton("取消",null).create();
            else dialog=old.setTitle("NAS 显示终端设置").setView(content).setPositiveButton("保存并连接",null).setNegativeButton("取消",null).create();
            return dialog;
        }
        Button saveButton(){return current!=null?((androidx.appcompat.app.AlertDialog)dialog).getButton(DialogInterface.BUTTON_POSITIVE):((android.app.AlertDialog)dialog).getButton(DialogInterface.BUTTON_POSITIVE);}
    }
    static CheckBox check(Context c){return modern()?new com.google.android.material.checkbox.MaterialCheckBox(c):new CheckBox(c);}
    static Button button(Context c){return modern()?new com.google.android.material.button.MaterialButton(c):new Button(c);}
    static EditText field(LinearLayout form,String label,boolean secret){
        Context c=form.getContext();EditText input;
        if(modern()){
            com.google.android.material.textfield.TextInputLayout box=new com.google.android.material.textfield.TextInputLayout(c);
            box.setHint(label);
            if(secret)box.setEndIconMode(com.google.android.material.textfield.TextInputLayout.END_ICON_PASSWORD_TOGGLE);
            input=new com.google.android.material.textfield.TextInputEditText(box.getContext());box.addView(input);form.addView(box);
            LinearLayout.LayoutParams lp=(LinearLayout.LayoutParams)box.getLayoutParams();lp.bottomMargin=dp(c,12);box.setLayoutParams(lp);
        }else{TextView title=new TextView(c);title.setText(label);form.addView(title);input=new EditText(c);form.addView(input);}
        return input;
    }
    static int dp(Context c,int n){return Math.round(n*c.getResources().getDisplayMetrics().density);}
    static final class Level {
        final SeekBar old;final com.google.android.material.slider.Slider current;
        Level(LinearLayout form,final String title,int value,int min,int max){
            Context c=form.getContext();final TextView caption=new TextView(c);form.addView(caption);caption.setText(title+" "+value+"%");
            if(modern()){
                old=null;current=new com.google.android.material.slider.Slider(c);current.setValueFrom(min);current.setValueTo(max);current.setStepSize(1);current.setValue(value);current.setContentDescription(title);
                current.addOnChangeListener(new com.google.android.material.slider.Slider.OnChangeListener(){public void onValueChange(com.google.android.material.slider.Slider slider,float v,boolean user){caption.setText(title+" "+Math.round(v)+"%");}});form.addView(current);
            }else{
                current=null;old=new SeekBar(c);old.setMax(max-min);old.setProgress(value-min);old.setTag(min);old.setContentDescription(title);
                old.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){public void onProgressChanged(SeekBar b,int v,boolean user){caption.setText(title+" "+(v+(Integer)b.getTag())+"%");}public void onStartTrackingTouch(SeekBar b){}public void onStopTrackingTouch(SeekBar b){}});form.addView(old);
            }
        }
        int value(){return current!=null?Math.round(current.getValue()):old.getProgress()+(Integer)old.getTag();}
    }
    static final class Rate {
        final Spinner old;int selected;
        Rate(LinearLayout form,int interval){
            Context c=form.getContext();selected=interval==200?1:0;String[] labels={"均衡 · 500ms（推荐）","流畅 · 200ms"};
            if(modern()){
                old=null;
                com.google.android.material.textfield.TextInputLayout box=new com.google.android.material.textfield.TextInputLayout(c,null,com.google.android.material.R.attr.textInputStyle);
                box.setHint("网络曲线刷新频率");box.setEndIconMode(com.google.android.material.textfield.TextInputLayout.END_ICON_DROPDOWN_MENU);
                com.google.android.material.textfield.MaterialAutoCompleteTextView menu=new com.google.android.material.textfield.MaterialAutoCompleteTextView(box.getContext());menu.setInputType(0);
                menu.setAdapter(new ArrayAdapter<String>(c,android.R.layout.simple_dropdown_item_1line,labels));menu.setText(labels[selected],false);
                menu.setOnItemClickListener(new AdapterView.OnItemClickListener(){public void onItemClick(AdapterView<?> parent,View view,int position,long id){selected=position;}});box.addView(menu);form.addView(box);
            }else{
                TextView caption=new TextView(c);caption.setText("网络曲线刷新频率");form.addView(caption);old=new Spinner(c);ArrayAdapter<String> adapter=new ArrayAdapter<String>(c,android.R.layout.simple_spinner_item,labels);adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);old.setAdapter(adapter);old.setSelection(selected);form.addView(old);
            }
        }
        int position(){return old==null?selected:old.getSelectedItemPosition();}
    }
    static void time(final MainActivity activity,final Button button,final String title,final int[] times,final int index){
        if(modern()){
            final com.google.android.material.timepicker.MaterialTimePicker picker=new com.google.android.material.timepicker.MaterialTimePicker.Builder()
                .setTimeFormat(com.google.android.material.timepicker.TimeFormat.CLOCK_24H).setHour(times[index]/60).setMinute(times[index]%60).setTitleText(title).build();
            picker.addOnPositiveButtonClickListener(new View.OnClickListener(){public void onClick(View v){times[index]=picker.getHour()*60+picker.getMinute();update(button,title,times[index]);}});
            picker.show(activity.getSupportFragmentManager(),"time-picker");
        }else new android.app.TimePickerDialog(button.getContext(),new android.app.TimePickerDialog.OnTimeSetListener(){public void onTimeSet(TimePicker picker,int hour,int minute){times[index]=hour*60+minute;update(button,title,times[index]);}},times[index]/60,times[index]%60,true).show();
    }
    static void update(Button button,String title,int time){button.setText(String.format(java.util.Locale.US,"%s  %02d:%02d",title,time/60,time%60));}
}
