import {el, kvEl, tableEl} from '../../core/dom.js';
import {labelText} from '../../core/labels.js';
import {fmtTime} from '../../core/fmt.js';

const present = value => value !== null && value !== undefined && value !== '';
const percent = value => present(value) ? `${Number((Number(value) * 100).toFixed(4))}%` : '';
const unit = (value, suffix) => present(value) ? `${value} ${suffix}` : '';
const frequency = value => ({daily:'每日', weekly:'每周', monthly:'每月', quarterly:'每季', event_driven:'事件驱动'}[value] || value);
const steps = {winsorize:'缩尾', industry_neutral:'行业中性化', size_neutral:'市值中性化', zscore:'标准化', rank:'分位排名', fillna:'缺失值填充'};
const price = {open:'当日开盘', close:'当日收盘', next_open:'次日开盘', next_close:'次日收盘', prev_close:'前收盘', vwap:'成交量加权均价'};

function section(title, pairs) {
  const node = el('section', 'strategy-summary__section');
  node.append(el('h4', '', title));
  if (pairs) node.append(kvEl(pairs.filter(([,value]) => present(value))));
  return node;
}

export function renderStrategyConfig(data) {
  const config = data.resolved;
  const doc = data.profile;
  const root = el('div', 'strategy-summary');
  const grid = el('div', 'strategy-summary__grid');
  const universe = config.universe || {};
  const position = config.position_constraint || {};
  const risk = config.risk_control || {};
  const fees = config.fees_slippage || {};
  const execution = config.execution_assumption || {};
  const instruments = (config.instruments || []).map(labelText).join('、');
  grid.append(section('选股与调仓', [
    ['市场', [labelText(config.market), instruments].filter(Boolean).join(' · ')],
    [config.timescale === config.data_frequency ? '研究 / 数据频率' : '研究频率', frequency(config.timescale)],
    ['数据频率', config.timescale !== config.data_frequency ? frequency(config.data_frequency) : ''],
    ['上市要求', present(universe.min_listed_days) ? `满 ${universe.min_listed_days} 个交易日` : ''],
    ['停牌股', present(universe.exclude_suspended) ? (universe.exclude_suspended ? '排除' : '保留') : ''],
    ['ST 股', present(universe.exclude_st) ? (universe.exclude_st ? '排除' : '保留') : ''],
    ['调仓', frequency(config.rebalance_frequency)],
    ['持仓周期', unit(config.holding_period, '个交易日')],
  ]));
  const sameLimit = present(position.max_weight_per_name) && present(risk.max_single_name_pct)
    && Number(position.max_weight_per_name) === Number(risk.max_single_name_pct);
  grid.append(section('仓位与风险', [
    ['持仓数量', unit(position.top_n, '只')],
    [sameLimit ? '单票上限' : '单票仓位上限', percent(position.max_weight_per_name)],
    ['单票风控上限', sameLimit ? '' : percent(risk.max_single_name_pct)],
    ['行业上限', percent(risk.max_industry_pct)],
    ['现金保留', percent(position.min_cash_ratio)],
    ['价格偏差阈值', percent(config.price_deviation_threshold)],
  ]));
  grid.append(section('交易与成本', [
    ['成交价格', price[execution.price_anchor] || execution.price_anchor],
    ['成交方式', {all_or_nothing:'全额成交或放弃', partial:'允许部分成交'}[execution.fill_assumption] || execution.fill_assumption],
    ['市场规则', config.trading_rules?.market_rule],
    ['费率', {market_rule:'按市场规则', manual:'手动设置'}[fees.fee_source] || fees.fee_source],
    ['手动费率', unit(fees.manual_fee_bps, '基点')],
    ['滑点', unit(fees.slippage_bps, '基点')],
  ]));
  root.append(grid);

  const research = el('div', 'strategy-summary__grid');
  const factors = config.factors?.items || [];
  const weighting = {equal:'等权', manual:'手动权重'}[config.factors?.weighting] || config.factors?.weighting;
  const factorSection = section(`因子组合${weighting ? ` · ${weighting}` : ''}`);
  const columns = [{key:'name', label:'因子', format: name => data.factor_display_names?.[name] || name}];
  if (factors.some(item => present(item.weight))) columns.push({key:'weight', label:'权重', format:percent});
  if (factors.some(item => present(item.direction_override))) columns.push({
    key:'direction_override', label:'方向', format: value => ({positive:'正向', negative:'负向'}[value] || value),
  });
  factorSection.append(factors.length ? tableEl(columns, factors) : el('p', 'item-row__meta', '待添加因子'));
  research.append(factorSection);

  if (config.standardization_chain?.length) {
    const process = section('因子处理');
    const list = el('ol', 'strategy-summary__steps content-scroll');
    for (const item of config.standardization_chain) {
      const row = el('li');
      row.append(el('strong', '', steps[item.step] || labelText(item.step)));
      const parameters = Object.entries(item).filter(([key,value]) => key !== 'step' && present(value)).map(([key,value]) => {
        const labels = {lower_pct:'下限', upper_pct:'上限', industry_source:'行业分类', size_field:'市值字段', method:'方法'};
        return `${labels[key] || labelText(key)} ${['lower_pct','upper_pct'].includes(key) ? percent(value) : labelText(value)}`;
      });
      if (parameters.length) row.append(el('span', '', parameters.join(' · ')));
      list.append(row);
    }
    process.append(list);
    research.append(process);
  }
  root.append(research);

  const explanation = config.extra || doc.extra || {};
  const notes = [
    ['用途', explanation.purpose], ['研究依据', explanation.hypothesis], ['证据', explanation.evidence],
    ['选股说明', universe.notes], ['成交说明', execution.notes], ['风控说明', risk.notes],
  ].filter(([,value]) => present(value));
  const details = el('details', 'strategy-summary__notes');
  details.append(el('summary', '', '补充说明与配置来源'));
  const body = el('div', 'content-scroll');
  for (const [title, value] of notes) {
    const paragraph = el('p');
    paragraph.append(el('strong', '', `${title}：`), document.createTextNode(value));
    body.append(paragraph);
  }
  body.append(kvEl([
    ['配置标识', doc.profile_name],
    ['继承自', data.inheritance_display_name || doc.inheritance_parent],
    ['来源链', config.source_chain?.join(' → ')],
    ['关联事项', doc.related_issue_id],
    ['创建时间', doc.created_at ? fmtTime(doc.created_at) : ''],
    ['更新时间', doc.updated_at ? fmtTime(doc.updated_at) : ''],
  ].filter(([,value]) => present(value))));
  details.append(body);
  root.append(details);
  return root;
}
