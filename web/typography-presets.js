'use strict';
document.addEventListener('DOMContentLoaded',async()=>{
  try{const r=await fetch('/api/studio',{cache:'no-store'}),data=await r.json();if(!r.ok)throw Error('字体搭配加载失败');
    for(const preset of data.presets){const button=document.createElement('button');button.type='button';button.textContent=preset.label;button.title=preset.description;
      button.addEventListener('click',()=>{if(!current||!Object.keys(fields).length){message('请等待字体编辑器加载完成。');return;}for(const [key,value] of Object.entries(preset.style)){const parts=key.split('_'),field=fields[parts[0]];if(!field)continue;const control=field[parts[1]];if(!control)continue;if(parts[1]==='bold')control.checked=value;else control.value=value;}changed();message('已试用「'+preset.label+'」。预览确认后再保存，不会自动覆盖原配置。');});
      document.getElementById('font-preset-bar').append(button);
    }
  }catch(e){message(e.message,true);}
});
