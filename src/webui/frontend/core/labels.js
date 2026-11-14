const LABELS = {
  open:'待处理',in_review:'评审中',accepted:'已采纳',rejected:'已驳回',solved:'已解决',
  draft:'草稿',active:'已启用',disabled:'已停用',retired:'已退出',pending:'待处理',
  approved:'已批准',committed:'已保存',discarded:'已丢弃',superseded:'已被取代',
  running:'运行中',completed:'已完成',failed:'失败',interrupted:'已中断',incomplete:'未完成',
  fresh:'已更新',stale:'已过期',stale_for_execution:'已过期待处理',archived:'已归档',log_mktcap:'对数市值',missing:'缺失',high:'高',medium:'中',low:'低',
  ready:'已就绪',quarantined:'暂停使用',queued:'排队中',paused:'已暂停',frozen:'已确定',
  cancelled:'已取消',proposed:'待评估',validating:'检查中',evaluated:'已评价',invalid:'无效',
  eliminated:'未采用',shadow:'模拟观察',historical:'历史重放',production:'已批准使用',
  watching:'持续观察',recorded:'已记录',reviewed:'已复核',invalidated:'已作废',
  evaluating:'评价中',challenger:'候选策略',champion:'正式策略',rolled_back:'已回滚',
  planned:'已计划',reserved:'已预留',opened:'已开封',applied:'已执行',confirmed:'已确认',
  expired:'已过期',partially_executed:'部分成交',executed:'已执行',received:'已收到',
  reconciled:'已核对',disputed:'待核对',attributed:'已完成归因',validated:'已校验',
  observed:'已发现',verified:'已核实',unplanned:'计划外成交',partial:'部分成交',
  planned_with_large_deviation:'成交偏差较大',skipped:'已跳过',aborted:'已中止',
  positive:'正向',negative:'负向',long:'多头',short:'空头',buy:'买入',sell:'卖出',
  decrease:'减仓',increase:'加仓',close:'清仓',daily:'每日',weekly:'每周',monthly:'每月',
  stock:'股票','CN-A':'A 股','CN-A-stock':'中国A股股票交易规则',CNY:'人民币',Asia_Shanghai:'北京时间',
  api_model:'接口模型',human:'人工',mixed:'人工与模型',
  system:'系统',broker:'券商',detailed:'当前持仓',unchanged:'沿用上次快照',
  factor_candidate:'生成因子候选',factor_evolution:'迭代因子',provider_mapping:'数据字段映射',
  strategy_critique:'评审策略',memory_candidate:'生成研究记忆',
  structured_config:'生成配置',data_quality_merge:'处理数据冲突',risk_review:'风险复核',
  cheap_batch_label:'批量标注',web_search:'网页检索',web_fetch:'网页读取',
  reasoning_high:'深度推理',coding_high:'代码生成',cheap_batch:'低成本批处理',
  long_context:'长上下文',review_strict:'严格复核',structured_output:'结构化输出',
  debugging:'调试诊断',risk_aware:'风险识别',web_browsing:'联网检索',
  train:'训练',validation:'验证',test:'评估',locked_test:'锁定测试',walk_forward:'滚动验证',
  baseline:'基线',research:'研究',default:'默认',strict:'严格财务',
  model_run:'模型实验',model_result:'模型结果',rolling_plan:'滚动研究',rolling_result:'滚动结果',
  forecast:'预测',forecast_outcome:'预测结果',watch_event:'关注记录',assistant_answer:'助手回答',
  tracking_result:'跟踪结果',tracking_config:'跟踪设置',tracking_attempt:'跟踪任务',
  dataset:'研究数据',quality:'数据检查',backup:'备份',recovery:'恢复副本',
  allocation_scenario:'组合测算',screen_result:'选股结果',assistant_evidence:'助手依据',
  start:'开始日期',train_end:'训练截止',valid_end:'验证截止',end:'评估截止',
  stock_limit:'股票数量上限',cores:'计算核数',trees:'树数量',depth:'树深度',top_n:'持股数',
  cost_bps:'单边成本（基点）',seed:'随机种子',name:'名称',run_id:'实验编号',
  features_seconds:'因子计算（秒）',training_seconds:'模型训练（秒）',evaluation_seconds:'评估（秒）',
  instrument_id:'证券代码',account_id:'账户编号',currency:'币种',status:'状态',
  task:'任务',kind:'类型',created_at:'创建时间',updated_at:'更新时间',
  'AI':'模型','IC':'相关系数','ICIR':'信息系数比率','Rank IC':'秩相关系数',
};

export function labelText(value) {
  if(value===null||value===undefined)return '—';
  return LABELS[value]??String(value);
}


export function displayText(value) {
  return labelText(value).replaceAll('双Worker','双工作节点').replaceAll('Spark研究','模型研究')
    .replaceAll('PIT 可见的 TTM','按披露日期可见的近一年').replaceAll('披露日 PIT 更新','披露日更新')
    .replaceAll('交由 AI 逐类修改并执行因子剔除实验','由模型逐类修改，并检验因子剔除效果')
    .replaceAll('年度IC等权均值不是显著性检验','年度相关系数均值不代表统计显著性')
    .replaceAll('UTC+8','北京时间').replaceAll('session 凭据','会话凭据');
}
