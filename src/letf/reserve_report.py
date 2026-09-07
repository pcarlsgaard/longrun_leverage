"""Compact result tables and five diagnostic figures, without parameter ranking."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .pipeline import table


def write_report(root,saved,manifest):
    out=root/'reports'
    m=pd.read_csv(out/'capital_reserve_metrics.csv')
    matched=pd.read_csv(out/'capital_reserve_matched_controls.csv')
    stress=pd.read_csv(out/'capital_reserve_stress_cycles.csv')
    sensitivity=pd.read_csv(out/'capital_reserve_sensitivity.csv')
    bulls=pd.read_csv(out/'capital_reserve_bull_episodes.csv')
    names=['NO_RESERVE','FIXED_90_10','FIXED_85_15','HWM_RESERVE','ASYMMETRIC_RESERVE_BAND']
    colors=['#263746','#367ca5','#83a2b7','#ba622c','#34744f']
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    def finish(fig,name):
        fig.canvas.draw()
        fig.set_layout_engine(None)
        fig.savefig(out/f'capital_reserve_{name}.png',dpi=140,bbox_inches='tight'); plt.close(fig)
    for field,label,name in [('wealth','Wealth per original $1 (log scale)','wealth'),
                              ('reserve_weight','Reserve / total portfolio','balances'),
                              ('drawdown','Total portfolio drawdown','drawdowns')]:
        fig,axs=plt.subplots(2,1,figsize=(11,7),sharex=True,constrained_layout=True)
        for lag,ax in zip((1,2),axs):
            for n,color in zip(names,colors):
                a=saved[(n,lag)]
                s=a.wealth/a.wealth.cummax().clip(lower=1)-1 if field=='drawdown' else a[field]
                ax.plot(s.index,s,label=n,color=color,lw=1)
            if field=='wealth': ax.set_yscale('log')
            ax.set_title(f'UPRO sleeve · LAG{lag} · 0 bp'); ax.set_ylabel(label); ax.grid(alpha=.15)
        axs[0].legend(ncol=3,fontsize=8); finish(fig,name)
    dep=pd.read_csv(out/'capital_reserve_deployments.csv',parse_dates=['execution_close'])
    fig,axs=plt.subplots(5,1,figsize=(11,11),constrained_layout=True)
    for event,ax in zip(['1987','2000_2002','2007_2009','2020','2022'],axs):
        s=stress[(stress.event.astype(str)==event)&(stress.series=='HWM_RESERVE')&
                 (stress.fund=='UPRO')&(stress.lag=='LAG2')&(stress.cost_bps==0)].iloc[0]
        start=pd.Timestamp(s.underlying_peak)-pd.DateOffset(months=6)
        end=min(pd.Timestamp(s.underlying_trough)+pd.DateOffset(years=3),saved[('HWM_RESERVE',2)].index[-1])
        for n,c in [('HWM_RESERVE',colors[3]),('ASYMMETRIC_RESERVE_BAND',colors[4])]:
            a=saved[(n,2)].loc[start:end]
            ax.plot(a.index,a.reserve_weight,label=n,color=c,lw=1)
            d=dep[(dep.series==n)&(dep.fund=='UPRO')&(dep.lag=='LAG2')&(dep.cost_bps==0)&
                  (dep.execution_close>=start)&(dep.execution_close<=end)]
            ax.scatter(d.execution_close,[a.reserve_weight.asof(x) for x in d.execution_close],s=15,color=c,marker='v')
        ax.axvline(pd.Timestamp(s.underlying_trough),color='#888',ls=':')
        ax.set_title(event+' · LAG2 · triangles: deployment closes'); ax.set_ylabel('Reserve weight'); ax.grid(alpha=.15)
    axs[0].legend(); finish(fig,'deployments')
    s=stress[(stress.fund=='UPRO')&(stress.lag=='LAG2')&(stress.cost_bps==25)&
             (stress.horizon=='3y')&stress.available&(stress.series.isin(names[3:]))]
    fig,axs=plt.subplots(1,2,figsize=(11,4),constrained_layout=True)
    for n,ax in zip(names[3:],axs):
        f=s[s.series==n]; x=np.arange(len(f))
        # Normalize each cycle by no-reserve peak wealth, retaining signed costs.
        ax.bar(x-.2,f.cycle_local_carried_opportunity_cost/f.pre_no_reserve_wealth,.4,label='Cycle-local carried shortfall')
        ax.bar(x+.2,f.cycle_local_incremental_post_drawdown_wealth/f.pre_no_reserve_wealth,.4,label='Cycle-local recovery benefit')
        ax.set_xticks(x,f.event,rotation=25); ax.axhline(0,color='#888',lw=.8)
        ax.set_title(n); ax.set_ylabel('Wealth / no-reserve wealth at peak'); ax.grid(axis='y',alpha=.15)
    axs[0].legend(fontsize=7); fig.suptitle('UPRO · LAG2 · 25 bp · 3 years after underlying trough'); finish(fig,'full_cycles')
    primary=m[(m.fund=='UPRO')&(m.series.isin(names))]
    display=primary[primary.cost_bps.isin([0,25])][['series','lag','cost_bps','cagr','max_drawdown',
        'terminal_multiple','average_reserve_weight','average_effective_equity_exposure']].copy()
    for c in ('cagr','max_drawdown','average_reserve_weight'): display[c]=display[c].map(lambda x:f'{100*x:.2f}%')
    focus=matched[(matched.fund=='UPRO')&(matched.lag=='LAG2')&(matched.cost_bps==25)]
    lines=['# Capital reserve / dry-powder experiment',
        f'Frozen sample: **{manifest["entry_close"]}–{manifest["as_of"]}**. Original daily histories, funding, fees, '
        'T-bill yield accrual, TR-SMA construction and common falsification dates are reused unchanged.',
        '## Main comparison',table(display),
        'NO_RESERVE means `UPRO_SMA_TO_SP500` or `SSO_SMA_TO_SP500`, as identified by the fund column. '
        '`FIXED_90_10` and `FIXED_85_15` are aliases for `STATIC_RESERVE_10` and `STATIC_RESERVE_15`. '
        'HWM_RESERVE and ASYMMETRIC_RESERVE_BAND use staged recovery deployment. '
        'HWM_DRAWDOWN and BAND_DRAWDOWN are separate deployment sensitivities, never combined with staging.',
        '## Equal-average-reserve and exposure controls',
        table(focus[['series','average_reserve','rounded_fixed_reserve','dynamic_cagr','fixed_cagr',
            'dynamic_to_fixed_terminal_ratio','dynamic_average_effective_exposure','constant_leverage_cagr']]),
        'Matching uses the full-sample mean reserve, rounded half-up to 0/5/10/15/20% etc. This is an ex-post '
        'diagnostic, not a deployable forecast or parameter optimization. The fixed target and its realized '
        'mean differ through drift. Equal cash allocation is not equal risk: state-dependent exposure, volatility '
        'and drawdowns remain different. Constant leverage uses the dynamic policy’s mean effective exposure '
        'with the original inferred funding+spread and the same fund fee; no core return history is rebuilt. '
        'Its daily reset is synthetic and incurs no additional investor state-switch fee.',
        ]+assessment(root)
    lines.extend([
        '## Accounting and implementation conventions',
        '- All policies start with $1. HWM starts with no bills, fixed policies start at their target, '
        'and the band starts with 10% bills. Initial allocations are not charged as transitions, matching '
        'the existing framework. There are no contributions, withdrawals, taxes or external reserve funding.',
        '- A new total-portfolio high advances the HWM immediately when recognized, even when the cap '
        'blocks a transfer. Gain-harvest orders use only the lag-eligible observed close, are executed '
        'within current available capital, and are capped using execution-close total wealth. The cap '
        'limits accumulation; passive reserve weight can exceed it after risky losses.',
        '- The band’s 20% upper boundary is a drift/reference boundary, not a mandatory crash sale of '
        'bills: no forced buying of leveraged equities below SMA. Above 5%, no ordinary replenishment '
        'occurs. Below 5%, favorable-state rebuilding targets 10%. After deployment, replenishment '
        'requires a newly observed all-history high in the unreserved risky sleeve, before investor switching charges, after that deployment. '
        'At most one reserve transfer occurs per execution close; deployment takes precedence.',
        '- Staging starts on an executed unfavorable→favorable transition, not an initially favorable '
        'sample entry. First tranche is one-third of current bill units; second is another third of episode-entry '
        'units after 20 completed favorable sessions; all remaining bills, including new harvests, follow 60 completed sessions. Interest '
        'stays with each tranche. A new episode re-splits remaining bills and resets confirmation. '
        'A continuing adverse signal never causes a staged purchase.',
        '- Drawdown uses the existing 1× S&P total-return index from its all-history high, lagged like '
        'the SMA. The first threshold snapshots bill units; 20/25/25/30% are deployed once per '
        'underlying drawdown episode. Gapped thresholds execute together. Thresholds reset only after '
        'the underlying regains its high. Additional later reserve accumulation is not part of the '
        'original snapshotted pool. All adverse-state buys follow 1×, favorable buys follow the fund.',
        '- LAG1 orders from close t affect return ending t+1; LAG2 affects t+2. HWM/band observations '
        'and drawdown triggers use the same lag. Fixed calendar rebalances receive the same extra '
        'session delay. Staging counts executed allocation states.',
        '- Costs use half-L1 turnover across actual old/new risky assets and bills. A full risky '
        'switch plus reserve deployment charges the union of those trades once, not their sum. '
        'Costs are paid proportionally from post-transfer holdings. Category trade counts may overlap; '
        'total charged transition days and net turnover do not. Direct cost drag holds the realized '
        'decision path fixed; zero-cost-versus-cost total drag also includes changes in future decisions.',
        '- Exposure uses beginning-of-return, post-trade weights. Fraction at exactly 1×/2×/3× means '
        'total portfolio exposure, with separate sleeve-state fractions. A partly reserved 3× sleeve '
        'generally gives less than 3× total exposure.',
        '- Stress dates use a common underlying trough and its preceding all-history high. All wealth '
        'is measured per original $1, including accumulation before the crash. Deployed-lot marking '
        'removes subsequent proportional harvesting and includes later costs; the cash-only alternative '
        'is gross interest carry. Remaining marked lots are not an exhaustive causal decomposition.',
        '- Rolling minima use exact calendar anniversaries with first trading close on/after anniversary. '
        'The standard rolling CSV inherits policy state, consistent with purchasing units of a running '
        'strategy. Fresh-investor state-reset cohorts are reported separately when generated. Overlapping '
        'cohorts are descriptive, not probabilities.',
        '## Figures',
        '![Wealth](capital_reserve_wealth.png)', '![Reserve balance](capital_reserve_balances.png)',
        '![Drawdowns](capital_reserve_drawdowns.png)', '![Deployment](capital_reserve_deployments.png)',
        '![Full-cycle attribution](capital_reserve_full_cycles.png)',
        '## Reproduce',
        '```bash\nPYTHONPATH=src python -m unittest discover -s tests -v\n'
        'PYTHONPATH=src python -m letf.capital_reserve --offline\n```',
        'Inputs restore from the existing verified bundle; the manifest records hashes, parameters '
        'and runtime versions. Core return data and earlier reports are unchanged. Synthetic pre-inception '
        'LETF returns, early index proxies, idealized closing executions, gross 1× returns and the original '
        'financing assumptions remain limitations. Cash accrual is not a traded bill fund with mark-to-market risk.'])
    (out/'capital_reserve_results.md').write_text('\n\n'.join(lines)+'\n')


def assessment(root):
    """Evidence-based answers, updated from result tables rather than rankings."""
    out=root/'reports'
    m=pd.read_csv(out/'capital_reserve_metrics.csv')
    match=pd.read_csv(out/'capital_reserve_matched_controls.csv')
    s=pd.read_csv(out/'capital_reserve_stress_cycles.csv')
    sub=pd.read_csv(out/'capital_reserve_subperiods.csv')
    grid=pd.read_csv(out/'capital_reserve_sensitivity.csv')
    bull=pd.read_csv(out/'capital_reserve_bull_episodes.csv')
    names=['NO_RESERVE','FIXED_90_10','FIXED_85_15','HWM_RESERVE','ASYMMETRIC_RESERVE_BAND','HWM_DRAWDOWN','BAND_DRAWDOWN']
    def row(name,fund='UPRO',lag='LAG2',cost=25):
        return m[(m.series==name)&(m.fund==fund)&(m.lag==lag)&(m.cost_bps==cost)].iloc[0]
    no,hwm,band=(row(n) for n in (names[0],names[3],names[4]))
    s3=s[(s.fund=='UPRO')&(s.lag=='LAG2')&(s.cost_bps==25)&(s.horizon=='3y')&
         s.available&s.series.isin(names[3:5])]
    c=match[(match.fund=='UPRO')&(match.lag=='LAG2')&(match.cost_bps==25)]
    strict=c[['series','dynamic_cagr','exact_average_fixed_cagr','dynamic_to_exact_average_fixed_terminal_ratio']]
    u=sub[(sub.fund=='UPRO')&(sub.lag=='LAG2')&(sub.cost_bps==25)&sub.series.isin(names[3:5])]
    b=bull[(bull.fund=='UPRO')&(bull.lag=='LAG2')&(bull.cost_bps==25)&bull.series.isin(names)]
    # Positive-return favorable episodes are the closer bull-market comparison.
    if 'no_reserve_total_return' in b:
        b=b[b.no_reserve_total_return>0]
    bt=[]
    for name,f in b.groupby('series',sort=False):
        bt.append(dict(series=name,episodes=len(f),median_no_reserve_cagr=f.no_reserve_cagr.median(),
            median_reserve_cagr=f.reserve_cagr.median(),
            median_cagr_sacrifice_pp=100*(f.no_reserve_cagr-f.reserve_cagr).median(),
            cumulative_log_growth_foregone=f.cumulative_log_growth_foregone.sum()))
    g=grid[(grid.series=='HWM_GRID')&(grid.lag=='LAG2')&(grid.cost_bps==25)]
    gr=g.groupby('fund').cagr.agg(['min','max']).reset_index()
    lines=['## Assessment: no reserve is the best-supported growth architecture',
        '**The complex reserve rules do not establish robust full-cycle value.** '
        'Keep the no-reserve SMA rule as the parsimonious growth baseline. A fixed reserve is an '
        'interpretable choice when deliberately accepting lower exposure and smaller drawdowns, '
        'but that is a different objective from demonstrating superior geometric wealth. '
        'Staging is a deployment mechanism, not a separately funded reserve architecture.',
        f'At **UPRO / LAG2 / 25 bp**, HWM CAGR is **{hwm.cagr:.2%}** and band CAGR **{band.cagr:.2%}**, '
        f'versus **{no.cagr:.2%}** without reserve: improvements of only '
        f'**{100*(hwm.cagr-no.cagr):.2f} / {100*(band.cagr-no.cagr):.2f} percentage points/year**. '
        'The favorable headline does not persist across execution assumptions, the lower-leverage sleeve '
        'and distinct historical cycles.',
        '1. **Does a reserve improve long-run wealth? Only conditionally.** '
        f'UPRO LAG2/25 bp terminal wealth rises {hwm.terminal_multiple/no.terminal_multiple-1:.1%} '
        f'with HWM and {band.terminal_multiple/no.terminal_multiple-1:.1%} with the band. '
        f'For SSO under the same assumptions, CAGR is {row("HWM_RESERVE","SSO").cagr:.2%} / '
        f'{row("ASYMMETRIC_RESERVE_BAND","SSO").cagr:.2%}, versus {row("NO_RESERVE","SSO").cagr:.2%} '
        'without reserve. LAG1/zero-cost dynamic reserve rules also fail to improve on no reserve.',
        '2. **Is it more than fixed cash? Some UPRO timing value appears, but it is not robust.** '
        'The exact-average-target diagnostic below supplements the prespecified 5-point rounding. '
        'Exact matching sets the fixed target to the observed dynamic mean, without fitting returns; '
        'drift means realized average weights remain approximate. Neither comparison is exact risk matching.',
        table(strict),
        '3. **Does HWM retain bull upside? Yes, mostly because it holds very little reserve.** '
        f'It averages only {hwm.average_reserve_weight:.2%} bills, versus '
        f'{row("FIXED_90_10").average_reserve_weight:.2%} for fixed 90/10. '
        'The gain cap is rarely binding, and long underwater periods prevent new harvesting. '
        'HWM therefore preserves upside at the cost of often lacking meaningful dry powder.',
        '4. **Are recoveries shorter? Mainly in 1987.** The own-HWM recovery dates improve sharply '
        'there, but by only days to weeks in most later episodes. Both staged architectures have '
        'zero bills at the 2002 trough, and HWM also has zero at the 2009 trough. A wealth ratio '
        'above one in those episodes can simply carry forward an earlier gain.',
        '5. **Is staging better than drawdown buying? No stable dominance.** '
        f'UPRO LAG2/25 bp staged versus drawdown HWM CAGR is {hwm.cagr:.2%} versus '
        f'{row("HWM_DRAWDOWN").cagr:.2%}; band is {band.cagr:.2%} versus '
        f'{row("BAND_DRAWDOWN").cagr:.2%}. SSO band drawdown deployment beats its staged version, '
        'while both lag no reserve. Hold-only controls confirm that leaving accumulated cash unused '
        'is costly; that does not prove the particular deployment timing is optimal.',
        '6. **Does it reconstruct risk capacity? When cash survives, yes; reliably across crashes, no.** '
        'Dry-powder ratios and marked deployed-lot wealth quantify the capacity, with adverse-state '
        'deployment limited to 1×. Repeated favorable episodes can spend the reserve before the eventual '
        'trough. The large 1987 band effect also partly comes from its initial 10% reserve, whereas '
        'HWM starts with zero.',
        '7. **What bull CAGR is sacrificed?** The table summarizes positive-return favorable episodes '
        'lasting at least 60 sessions. Medians are descriptive, not an estimate of a permanent annual '
        'penalty. Zero median loss for a dynamic rule often means the reserve was empty; occasional '
        'relative gains within favorable episodes reflect daily compounding and lower exposure. '
        'The CSV retains every eligible episode and links it to the next named stress cycle. '
        'Those repeated cycle references must not be summed.',
        table(pd.DataFrame(bt)),
        '8. **Does recovery repay the cost? Generally not in the later staged-reserve cycles.** '
        'The following local-cycle accounting equalizes starting wealth at the previous named crash’s '
        'underlying recovery, then includes all accumulation up to the next peak. The first cycle starts '
        'at sample entry. It removes inherited dollar advantages without resetting reserve policy state. '
        'At three years, HWM repays the 1987 shortfall but not the 2000, 2020 or 2022 shortfalls; '
        '2008 has essentially no local reserve activity. The band also fails to repay 2000, 2020 '
        'and 2022. Near-zero costs and negative costs have undefined payback ratios.',
        table(s3[['series','event','cycle_local_recovery_multiplier','cycle_local_reserve_payback_ratio',
                  'dry_powder_ratio','reserve_recovery_days','no_reserve_recovery_days']]),
        '9. **Are results consistent across crises? No.** 1987 supplies much of the surviving staged '
        'reserve advantage. The 2010-onward comparison loses relative wealth for both staged rules:',
        table(u[['series','period','terminal_ratio_vs_no_reserve']]),
        '10. **Do gains survive execution delay and costs? The small UPRO LAG2 gain does survive '
        '10–25 bp, but it is not invariant to lag or leverage.** The complete grid includes 50 bp '
        'and reports both direct charged-cost drag and endogenous changes in policy decisions.',
        '11. **Does complexity beat simple controls materially? Not with sufficient robustness.** '
        'The prespecified HWM cap/harvest grid has the following LAG2/25-bp CAGR ranges; similar '
        'cap results mostly mean caps are inactive, rather than demonstrating a universal robust optimum.',
        table(gr),
        '12. **Conclusion: the added complexity is not justified by this experiment.** '
        'No reserve is the best-supported baseline for the stated geometric-growth objective. '
        'Fixed reserve is the clearer alternative for an explicit capital-preservation objective. '
        'Do not market either as free dry powder, and do not infer future performance from five '
        'overlapping historical crisis narratives.']
    fresh_path=out/'capital_reserve_fresh_rolling_summary.csv'
    if fresh_path.exists():
        f=pd.read_csv(fresh_path)
        f=f[(f.lag=='LAG2')&(f.cost_bps==25)&f.series.isin(names)]
        lines.extend(['## Fresh-investor rolling cohorts',
            'These monthly cohorts start with fresh $1, each rule’s prescribed initial reserve, '
            'new HWM and confirmation state. Underlying SMA/drawdown history remains observable '
            'before entry. They check whether the inherited-policy results depend on a reserve '
            'built or spent before the investor starts. Both funds/lags and 0/25 bp are included '
            'in the CSV; the important LAG2/25-bp results follow. Cohorts overlap extensively. '
            'Use the median of paired wealth ratios, not the ratio of separately calculated medians. '
            'Drawdown deployment improves some 20-year outcomes, but its UPRO 30-year paired median '
            'is approximately break-even at LAG2/25 bp and lower under the other lag/cost combinations.',
            table(f[['series','fund','horizon_years','cohorts','min_cagr','median_cagr',
                     'min_terminal_multiple','median_terminal_multiple','median_ratio_vs_no_reserve']])])
    return lines


def refresh_report(root):
    import json
    fields=('wealth','reserve_wealth','reserve_weight','effective_equity_exposure','return_net')
    saved={}
    for lag in (1,2):
        frames={field:pd.read_csv(root/f'data/processed/capital_reserve_{field}_lag{lag}.csv',
                                index_col='date',parse_dates=True) for field in fields}
        for name in frames['wealth']:
            saved[(name,lag)]=pd.DataFrame({field:f[name] for field,f in frames.items()})
    write_report(root,saved,json.loads((root/'reports/capital_reserve_manifest.json').read_text()))
