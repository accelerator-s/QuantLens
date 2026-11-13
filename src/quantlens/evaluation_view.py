"""Format bounded, published daily evaluations for the existing detail cards."""
import math
import pandas as pd


def view(rows,daily,primary=5,direction='positive'):
    row=next((item for item in rows if item['horizon']==primary),rows[0] if rows else {})
    if not row:return {}
    frame=pd.DataFrame([item for item in daily if item['horizon']==row['horizon']])
    yearly=[];spread={};count=0
    if not frame.empty:
        frame['date']=pd.to_datetime(frame['date']);frame=frame.sort_values('date')
        count=int(frame.rank_ic.notna().sum())
        yearly=[{'year':int(year),'ic_mean':None if pd.isna(group.rank_ic.mean()) else float(group.rank_ic.mean())} for year,group in frame.groupby(frame.date.dt.year)]
        cohort=(frame.q5_ret-frame.q1_ret).iloc[::int(row['horizon'])].dropna()
        nav=(1+cohort).cumprod()
        if len(nav):
            annual=float(nav.iloc[-1]**(244/(len(nav)*row['horizon']))-1) if nav.iloc[-1]>0 else -1.
            spread={'annualized_return':annual,'max_drawdown':float((nav/nav.cummax()-1).min()),'avg_daily_turnover_oneside':row.get('turnover')}
    ic={'ic_mean':row.get('rank_ic'),'ic_std':row.get('ic_std'),'icir':row.get('icir'),
        'icir_annualized':row['icir']*math.sqrt(244) if row.get('icir') is not None else None,
        'win_rate':row.get('positive_ratio'),'n_valid_days':count}
    variant={'ic':ic,'coverage':row.get('coverage'),'turnover':row.get('turnover'),'monotonicity':row.get('monotonicity'),
             'yearly_ic':yearly,'groups':{'g5':{'long_short':spread,'mean_returns':{f'q{i}':row.get(f'q{i}_ret') for i in range(1,6)}}}}
    return {'schema_version':2,'factor':row['factor'],'version':row['version'],'direction':direction,'period':{'start':row['eval_start'],'end':row['eval_end']},
            'return_field':'ret_wd','labels':{'default':{'variants':{'published':variant}}},'decay':rows,
            'notes':['DWS 已发布标准化值；同一 DWD 成熟标签。分组收益为不重叠持有期毛收益，未扣交易费用。']}
