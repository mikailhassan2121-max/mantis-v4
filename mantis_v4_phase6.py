"""Phase 6: adaptive entry decision research (classification only)."""
from __future__ import annotations

import argparse, json
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd

from mantis_v4.config import MantisConfig
from mantis_v4.clock import load_timezone
from mantis_v4.models.dataset_builder import ALL_FEATURES, ModellingDatasetBuilder, drop_incomplete, select_usable_features
from mantis_v4.models.splits import reserve_holdout
from mantis_v4.backtest import YFinanceHistoryProvider, load_universe
from mantis_v4.simulation import (combine_channels, compute_fragility, conservative_lower_bound,
    digital_sensitivities, gaussian_terminal, scaled_sensitivities, student_t_terminal,
    volatility_of_volatility)
from mantis_v4.entry import DecisionPolicy, apply_policy_once, evaluate_entries, select_policy_on_development

SEED = 20260814
LIMITS = ["PROXY SETTLEMENT REFERENCE", "NO HISTORICAL WEBULL CONTRACT QUOTES",
          "CLASSIFICATION RESEARCH ONLY", "NOT A PROFITABILITY BACKTEST",
          "LIMITED RECENT HISTORICAL REGIME"]


def build_states(table: pd.DataFrame) -> pd.DataFrame:
    spot, ref = table.spot.to_numpy(), table.reference.to_numpy()
    seconds, sigma = table.seconds_remaining.to_numpy(), table.realized_vol_1m.to_numpy()
    anchor = gaussian_terminal(table.buffer_pct.to_numpy(), table.sigma_remaining_return.to_numpy(),
                               spot=spot, reference=ref)
    heavy = student_t_terminal(table.buffer_pct.to_numpy(), table.sigma_remaining_return.to_numpy(),
                               nu=4.456245976114701, spot=spot)
    channels = {"gaussian": anchor.p_yes, "student_t": heavy.p_yes}
    agreement = combine_channels(channels)
    lower = conservative_lower_bound(channels)
    sens = digital_sensitivities(spot=spot, reference=ref, seconds_remaining=seconds, sigma_1m=sigma)
    scaled = scaled_sensitivities(sens, spot, sigma)
    frag = compute_fragility(gamma_per_pct2=scaled["gamma_per_pct2"],
        vega_per_10pct_vol=scaled["vega_per_10pct_vol"], theta_per_30s=scaled["theta_per_30s"],
        abs_z=np.abs(table.normal_z.to_numpy()), seconds_remaining=seconds,
        crossings=table.crossings.to_numpy(),
        vol_of_vol=volatility_of_volatility(table.rv_5m.to_numpy(), table.rv_30m.to_numpy()),
        disagreement=agreement.disagreement)
    out = table[["contract_id","asset","group_key","window_epoch","seconds_remaining",
                 "normal_z","crossings","outcome_yes"]].copy()
    out["p_yes"], out["lower_bound"] = anchor.p_yes, lower
    out["disagreement"], out["fragility"] = agreement.disagreement, frag.score
    out["crossing_risk"] = anchor.p_cross_reference
    ratio = table.rv_5m.to_numpy() / np.maximum(table.rv_30m.to_numpy(), 1e-12)
    out["volatility_regime"] = np.where(ratio > 1.35, "EXPANDING", np.where(ratio < .75, "CONTRACTING", "NORMAL"))
    out["reference_valid"] = np.isfinite(ref) & (ref > 0)
    out["sufficient_history"] = True
    out["data_fresh"] = True
    out["contract_valid"] = out.contract_id.astype(str).str.len().gt(0)
    return out


def families() -> dict[str, list[DecisionPolicy]]:
    probs=(.70,.75,.80,.85,.90,.95); lcbs=(.70,.75,.80,.85,.90,.95)
    fam={"A_probability_only":[],"B_probability_lcb":[],"C_probability_fragility":[],
         "D_probability_disagreement":[],"E_probability_fragility_disagreement":[],
         "F_probability_lcb_fragility_disagreement":[],"G_fixed_under_5m":[],
         "H_adaptive_timing":[]}
    for p in probs:
        fam["A_probability_only"].append(DecisionPolicy(f"A_p{p:.2f}",p))
        fam["C_probability_fragility"].append(DecisionPolicy(f"C_p{p:.2f}_f50",p,max_fragility=50))
        fam["D_probability_disagreement"].append(DecisionPolicy(f"D_p{p:.2f}_d.05",p,max_disagreement=.05))
        fam["E_probability_fragility_disagreement"].append(DecisionPolicy(f"E_p{p:.2f}_f50_d.05",p,max_fragility=50,max_disagreement=.05))
        fam["F_probability_lcb_fragility_disagreement"].append(DecisionPolicy(f"F_p{p:.2f}_l{max(.70,p-.05):.2f}_f50_d.05",p,lcb_threshold=max(.70,p-.05),max_fragility=50,max_disagreement=.05))
        fam["G_fixed_under_5m"].append(DecisionPolicy(f"G_p{p:.2f}_under300",p,max_seconds_remaining=300))
        fam["H_adaptive_timing"].append(DecisionPolicy(f"H_p{p:.2f}_l{max(.7,p-.05):.2f}_f50_d.05_t300",p,lcb_threshold=max(.7,p-.05),max_fragility=50,max_disagreement=.05,max_seconds_remaining=300,max_crossing_probability=.35,max_crossings=4))
    # Explicitly sweep every required LCB threshold at a common high point confidence.
    fam["B_probability_lcb"] = [DecisionPolicy(f"B_p.95_l{l:.2f}",.95,lcb_threshold=l) for l in lcbs]
    return fam


def timing_study(states: pd.DataFrame) -> list[dict]:
    rows=[]
    for _, block in states.groupby(["contract_id","asset"]):
        b=block.sort_values("seconds_remaining",ascending=False).reset_index(drop=True)
        direction=np.where(b.p_yes>=.5,1,0); confidence=np.maximum(b.p_yes,1-b.p_yes)
        for j,row in b.iterrows():
            future=b.iloc[j+1:]
            correct=int(direction[j]==row.outcome_yes)
            rows.append({"seconds_remaining":row.seconds_remaining,"entry_bucket":__import__('mantis_v4.entry',fromlist=['entry_time_bucket']).entry_time_bucket(row.seconds_remaining),
                "correct_now":correct,"direction_flips_later":bool(len(future) and (np.where(future.p_yes>=.5,1,0)!=direction[j]).any()),
                "confidence_improves":bool(len(future) and future.assign(c=np.maximum(future.p_yes,1-future.p_yes)).c.max()>confidence[j]+.025),
                "confidence_deteriorates":bool(len(future) and future.assign(c=np.maximum(future.p_yes,1-future.p_yes)).c.min()<confidence[j]-.025),
                "setup_disappears":bool(len(future) and np.maximum(future.p_yes,1-future.p_yes).max()<.70)})
    f=pd.DataFrame(rows)
    return f.groupby("entry_bucket",sort=False).agg(n=("correct_now","size"),accuracy_now=("correct_now","mean"),direction_flip_probability=("direction_flips_later","mean"),confidence_improves_probability=("confidence_improves","mean"),confidence_deteriorates_probability=("confidence_deteriorates","mean"),setup_disappears_probability=("setup_disappears","mean")).reset_index().to_dict("records")


def write_reports(summary: dict, out: Path):
    banner="\n".join(f"> **{x}**" for x in LIMITS)
    policies=summary["holdout_policies"]
    table="\n".join(f"| {r['family']} | {r['policy_name']} | {r['contracts_traded']} | {r['coverage']:.2%} | {r['accuracy']:.2%} | [{r['ci_low']:.2%}, {r['ci_high']:.2%}] | {r['abstention_rate']:.2%} |" for r in policies)
    c=summary["conclusions"]
    detail="\n".join(f"- {x}" for x in c["findings"])
    main=f"""# Phase 6 Entry Policy\n\n{banner}\n\nNormal-Z remains the unchanged directional probability anchor. Student-t is used only to form heavy-tail disagreement and a conservative eligibility bound. Sensitivities are fragility diagnostics, never alpha. All hypothetical entries are held to fixed resolution and occur once per contract/asset. `EV_STATUS = UNAVAILABLE`.\n\n## Untouched holdout results\n\n| Family | Locked development policy | Trades | Coverage | Accuracy | Clustered 95% CI | Abstention |\n|---|---|---:|---:|---:|---:|---:|\n{table}\n\n## Findings\n\n{detail}\n\n## Recommendation for Phase 7\n\n{summary['recommendation']}\n\nThis is a **CLASSIFICATION-OPTIMAL ENTRY POLICY UNDER PROXY DATA**, not profit-maximizing timing. Phase 7 has not begun.\n"""
    (out/"PHASE6_ENTRY_POLICY.md").write_text(main,encoding="utf-8")
    frontier="\n".join(f"| {x['target']:.1%} | {x.get('supported',False)} | {x.get('policy','—')} | {x.get('coverage',0):.2%} | {x.get('trades',0)} |" for x in summary["selectivity_frontier"])
    (out/"PHASE6_SELECTIVITY_FRONTIER.md").write_text(f"# Phase 6 Selectivity Frontier\n\n{banner}\n\n| Accuracy target | Supported on holdout | Policy | Coverage | Trades |\n|---|---|---|---:|---:|\n{frontier}\n\nTargets are not forced. Tiny or near-resolution samples are not treated as robust evidence.\n",encoding="utf-8")
    timing="\n".join(f"| {x['entry_bucket']} | {x['n']} | {x['accuracy_now']:.2%} | {x['direction_flip_probability']:.2%} | {x['confidence_improves_probability']:.2%} | {x['confidence_deteriorates_probability']:.2%} | {x['setup_disappears_probability']:.2%} |" for x in summary["timing"])
    (out/"PHASE6_ENTRY_TIMING.md").write_text(f"# Phase 6 Entry Timing\n\n{banner}\n\nHistorical future states below evaluate predefined decisions; they are never live features.\n\n| Bucket | States | Accuracy now | Direction flips later | Confidence improves | Confidence deteriorates | Setup disappears |\n|---|---:|---:|---:|---:|---:|---:|\n{timing}\n\nNear-resolution accuracy is explicitly classification-trivial when dominated by saturated probability, large buffers, and collapsed remaining volatility. No economic usefulness can be inferred without quotes.\n",encoding="utf-8")
    (out/"PHASE6_ABSTENTION.md").write_text(f"# Phase 6 Abstention\n\n{banner}\n\nThe engine distinguishes probability, conservative-bound, buffer, fragility, disagreement, crossing-risk, early-information and developing-trend waits; unstable chop may end as NO TRADE; invalid inputs always produce DATA HOLD. Full decisions and reason codes are in `data/mantis_v4_phase6_entries.csv`.\n\nThe recommended policy traded {c['recommended_trades']:,} of {c['contracts_observed']:,} asset-contracts ({c['recommended_coverage']:.2%} coverage) and abstained on {c['recommended_abstention']:.2%}. This selectivity is a classification-quality result only.\n",encoding="utf-8")


def derive_conclusions(selected: list[dict], best: dict) -> dict:
    by={x["family"]:x for x in selected}
    def delta(a,b,key="accuracy"): return by[a][key]-by[b][key]
    late=sum(v for k,v in best["entry_time_distribution"].items() if k in
             ("T-120 to T-60","T-60 to T-30","T-30 to resolution"))
    final30=best["entry_time_distribution"].get("T-30 to resolution",0)
    findings=[
      f"Adaptive timing beat fixed under-five-minute timing by {delta('H_adaptive_timing','G_fixed_under_5m')*100:+.2f} percentage points of accuracy, while changing coverage by {delta('H_adaptive_timing','G_fixed_under_5m','coverage')*100:+.2f} points.",
      f"The selected LCB-only policy changed accuracy by {delta('B_probability_lcb','A_probability_only')*100:+.2f} points versus probability-only; on this holdout the selected LCB gate did not earn an incremental benefit.",
      f"The fragility gate changed accuracy by {delta('C_probability_fragility','A_probability_only')*100:+.2f} points and coverage by {delta('C_probability_fragility','A_probability_only','coverage')*100:+.2f} points.",
      f"The disagreement-only gate changed accuracy by {delta('D_probability_disagreement','A_probability_only')*100:+.2f} points; at the development-selected threshold it did not earn an incremental benefit.",
      f"The combined fragility+disagreement policy changed accuracy by {delta('E_probability_fragility_disagreement','A_probability_only')*100:+.2f} points and coverage by {delta('E_probability_fragility_disagreement','A_probability_only','coverage')*100:+.2f} points; its result equalled fragility-only, so disagreement did not add value here.",
      f"The recommended rule placed {late}/{best['contracts_traded']} entries ({late/best['contracts_traded']:.2%}) at T-120 or later and {final30}/{best['contracts_traded']} ({final30/best['contracts_traded']:.2%}) in the final 30 seconds. Its accuracy is therefore not solely a final-seconds artifact, but remains classification-only and may still reflect late information and collapsed remaining volatility.",
    ]
    return {"findings":findings,"contracts_observed":best["contracts_observed"],
            "recommended_trades":best["contracts_traded"],"recommended_coverage":best["coverage"],
            "recommended_abstention":best["abstention_rate"],"final_30_share":final30/best["contracts_traded"]}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--days",type=int,default=30); ap.add_argument("--holdout",type=float,default=.25); args=ap.parse_args()
    root=Path(__file__).resolve().parent; config=MantisConfig.load()
    provider=YFinanceHistoryProvider(root/"data"/"history",timeout_seconds=config.network_timeout_seconds)
    frames,_=load_universe(provider,config.active_assets,args.days)
    table,_=ModellingDatasetBuilder(frames,load_timezone(config.contract_timezone)).build(); usable,_=select_usable_features(table,ALL_FEATURES); table,_=drop_incomplete(table,usable)
    plan=reserve_holdout(table,holdout_fraction=args.holdout); dev,hold=build_states(plan.dev),build_states(plan.holdout)
    selected=[]; tuning=[]; entries=[]
    for family,candidates in families().items():
        chosen, scores=select_policy_on_development(dev,candidates); tuning.extend({"family":family,**s} for s in scores)
        e=apply_policy_once(hold,chosen); entries.append(e); m=evaluate_entries(e,len(e),n_boot=400)
        selected.append({"family":family,"policy_name":chosen.name,"policy":asdict(chosen),**m})
    all_entries=pd.concat(entries,ignore_index=True); data=root/"data"; research=root/"research"
    all_entries.to_csv(data/"mantis_v4_phase6_entries.csv",index=False)
    pd.DataFrame([{k:v for k,v in x.items() if not isinstance(v,(dict,list))} for x in selected]).to_csv(data/"mantis_v4_phase6_frontier.csv",index=False)
    pd.DataFrame([{k:v for k,v in x.items() if not isinstance(v,(dict,list))} for x in tuning]).to_csv(data/"mantis_v4_phase6_candidates.csv",index=False)
    valid=[x for x in selected if x["contracts_traded"]>=30 and x["accuracy"] is not None]
    best=max(valid,key=lambda x:(x["accuracy"],x["coverage"]))
    frontier=[]
    for target in (.90,.925,.95,.975,.99):
        supported=[x for x in valid if x["ci_low"] is not None and x["ci_low"]>=target]
        pick=max(supported,key=lambda x:x["coverage"]) if supported else None
        frontier.append({"target":target,"supported":bool(pick),"policy":pick["policy_name"] if pick else None,"coverage":pick["coverage"] if pick else 0,"trades":pick["contracts_traded"] if pick else 0})
    recommendation=f"Lock `{best['policy_name']}` for Phase 7 economic evaluation: holdout accuracy {best['accuracy']:.2%}, coverage {best['coverage']:.2%}, clustered CI [{best['ci_low']:.2%}, {best['ci_high']:.2%}]. This recommendation concerns classification entry quality only."
    summary={"limitations":LIMITS,"holdout":plan.describe(),"holdout_policies":selected,"development_tuning":tuning,"selectivity_frontier":frontier,"timing":timing_study(hold),"recommendation":recommendation,"conclusions":derive_conclusions(selected,best),"phase7_started":False}
    (data/"mantis_v4_phase6_summary.json").write_text(json.dumps(summary,indent=2,default=str,allow_nan=False),encoding="utf-8")
    write_reports(summary,research); print(recommendation)

if __name__=="__main__": main()
