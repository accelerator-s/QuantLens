import {mountTabs} from '../../core/tabs.js';
import {mount as mountLens} from '../lens/lens.js';
export async function mount(root,ctx) {
  await mountTabs(root,ctx,'settings',[
    {id:'data',label:'数据同步',component:'data'},
    {id:'interfaces',label:'模型配置',component:'ai-config'},
    {id:'system',label:'任务',pane:'tasks',mount:mountLens},
    {id:'cluster',label:'集群',pane:'cluster',mount:mountLens},
    {id:'records',label:'记录',component:'review'},
  ]);
}
