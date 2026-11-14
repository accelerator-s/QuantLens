import {mountTabs} from '../../core/tabs.js';

export async function mount(root, ctx) {
  await mountTabs(root, ctx, 'strategy-research', [
    {id: 'library', label: '策略库', component: 'strategy'},
    {id: 'research', label: '新策略研究', component: 'strategy-builder'},
    {id: 'experiments', label: '策略实验', component: 'strategy-runs'},
  ]);
}
