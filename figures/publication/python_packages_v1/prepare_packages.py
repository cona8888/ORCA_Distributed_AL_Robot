"""Extract registered sources to portable, independently runnable packages.

Run from any directory. This program does not execute or alter experiments.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(rel):
    return json.loads((ROOT/rel).read_text())


def write_csv(folder,rows):
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with (folder/'data.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fields)
        writer.writeheader();writer.writerows(rows)


def trajectory_rows(scene):
    return [dict(kind='track',scene_id=scene['id'],family=scene['family'],
                 robot_count=scene['robot_count'],robot_id=t['rid'],**p)
            for t in scene['tracks'] for p in t['points']]


def finalize(n,caption,sources,notes):
    folder=HERE/f'fig{n:02}'
    shutil.copy2(HERE/'common.py',folder/'common.py')
    (folder/'caption.txt').write_text(caption.strip()+'\n',encoding='utf-8')
    provenance={'figure':n,'sources':[{'path':s,'sha256':sha(ROOT/s)} for s in sources],
                'notes':notes,'data_sha256':sha(folder/'data.csv')}
    (folder/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (folder/'requirements.txt').write_text('matplotlib>=3.10\nnumpy>=2.0\nPillow>=11.0\n')
    (folder/'README.txt').write_text(
        f'Figure {n}\n\nRebuild: python draw.py\n'
        'Dependencies: pip install -r requirements.txt; Arial must be installed.\n'
        'The default width is 183 mm. Axis labels and units are 14 pt; '
        'all other visible text is at least 14 pt.\n'
        'Optional: python draw.py --width-mm 260\n'
        'SVG text is live and editable. PNG files are previews (300 and 600 dpi).\n'
        'Maps are unchanged raster inputs embedded in the SVG. No figure title '
        'is drawn. The caption is stored separately.\n'
        'data.csv is the plotted-data extract; provenance.json identifies the '
        'registered source files.\n')


def main():
    traj_rel='figures/publication/fig04_trajectories/fig04_trajectory_data.json'
    traj=load(traj_rel)
    # Authenticate the underlying held-out source, not just its compact extract.
    assert sha(ROOT/traj['source_analysis'])==traj['source_analysis_sha256']
    for scene in traj['safe_cases']:
        assert sha(ROOT/scene['map_path'])==scene['map_sha256']
        assert sha(ROOT/scene['replay_path'])==scene['replay_sha256']
        assert len(scene['tracks'])==scene['robot_count']
        replay_html=(ROOT/scene['replay_path']).read_text()
        match=re.search(r'<script id="replayPayload" type="application/json">([\s\S]*?)</script>',replay_html)
        assert match,scene['id']
        replay=json.loads(match.group(1))
        assert replay['comparison']['exact_semantic_match'] is True
        assert len(replay['reference']['frames'])==len(replay['optimized']['frames'])==200
        for ref,opt in zip(replay['reference']['frames'],replay['optimized']['frames']):
            assert ref['tick']==opt['tick'] and ref['phase']==opt['phase']
            assert ref['simulated_time_seconds']==opt['simulated_time_seconds']
            assert len(ref['robots'])==len(opt['robots'])
            for rr,oo in zip(ref['robots'],opt['robots']):
                for key in ['rid','bearing','radius_m']:
                    assert rr[key]==oo[key]
                assert rr.get('state_hex',rr.get('xy'))==oo.get('state_hex',oo.get('xy'))
    for n in range(1,8):(HERE/f'fig{n:02}').mkdir(exist_ok=True)
    scene=next(s for s in traj['safe_cases'] if s['family']=='perfect_maze' and s['robot_count']==6)
    folder=HERE/'fig01'
    write_csv(folder,trajectory_rows(scene))
    shutil.copy2(ROOT/scene['map_path'],folder/'map.png')
    finalize(1,
        '''Figure 1 | The same robot behaviour with less computation.
(a) Coincident measured reference and optimized robot paths in the authenticated
perfect-maze, six-robot, seed-11 held-out scene. Solid blue and dashed dark lines
use the same coordinates, with no visual offset. Open markers show initial
positions; filled robot discs and heading ticks show final poses. The 100 m ×
100 m arena uses the simulator coordinate frame, with y increasing downward.
(b) Both builds retain the sensing, GBP, and motion sequence. The schematic
introduces the unchanged execution order; implementation changes are located
in Figure 2. Trajectory CSV rows contain a compact initial/post-update sample
of 101 poses per robot from a 200-frame exact paired replay.''',
        [traj_rel,traj['source_analysis'],scene['map_path'],scene['replay_path']],
        ['Simulated measured trajectories; execution-cycle diagram is conceptual.',
         'Blue: optimized; dark dashed: reference. No computed path is shifted.'])
    semantic_rel='figures/publication/fig01_overview/fig01_semantic_spec.json'
    semantic=load(semantic_rel)
    positions=[(.07,.84),(.36,.84),(.07,.47),(.36,.47),(.62,.11)]
    targets=[(.18,.82),(.34,.72),(.13,.34),(.34,.24),(.54,.18)]
    write_csv(HERE/'fig02',[dict(optimization=item['n'],stage=item['stage'],
                              meaning=item['full'],x=x,y=y,target_x=tx,target_y=ty,
                              kind='schematic_layout')
                           for item,(x,y),(tx,ty) in zip(semantic['optimizations'],positions,targets)])
    finalize(2,
        '''Figure 2 | Where computational work is reduced in the DANCeRS architecture.
(a) A conceptual two-robot diagram shows planning and global-consensus graph
fragments, each with open circular variables and square factors. Graph edges
are schematic and do not represent sampled communication topology. Dashed
purple arrows denote information exchange. Numbered markers identify (1)
contiguous KD-tree positions and identifiers, (2) reuse of exactly unchanged
neighbour topology, (3) GBP snapshot/copy/solve reuse, (4) scalar R1 information
operations with identity-transport bypass, and (5) single-owner movement of
externally staged messages. (b) The retained sensing, GBP, and motion cycle.
The implementation preserves the GBP schedule, internal sweep count, update
order, convergence settings, and reported planner metrics. CSV coordinates are
diagram-layout data, not experimental measurements.''',
        [semantic_rel,'docs/publication/DANCERS_FIGURE1_VISUAL_THESIS_CONTRACT_V1.md'],
        ['Original conceptual schematic. Node topology illustrates notation only.'])
    ablation_rel='figures/publication/fig02_ablation/fig02_plot_data.csv'
    shutil.copy2(ROOT/ablation_rel,HERE/'fig03'/'data.csv')
    finalize(3,
        '''Figure 3 | How the five changes reduce execution time.
(a,b) Conceptual fragments illustrate unchanged topology reuse and reduced
copying/repeated operations; their geometry is illustrative. (c) Complete-
process wall time at cumulative stages S0–S5 for 500 and 1,000 robots. Stage
values are geometric means of seed medians from three timing repetitions per
stage and ten complete seeds; the vertical axis is logarithmic. Blue squares
and solid lines denote 500 robots; purple circles and dashed lines denote
1,000 robots. S0 is reference; S1–S5 add the five changes in Figure 2 order.
(d) Paired cumulative S0/S5 wall and CPU speedups with registered deterministic
95% cluster-bootstrap intervals. Blue squares denote wall time; purple open
circles denote CPU time. Wall speedups are 8.4454× (8.2915–8.6021) and 14.7229×
(14.3003–15.1753). (e) Geometric-mean peak RSS across cumulative stages,
approximately 44 and 55 MiB. This historical-order experiment measures
cumulative contributions, not independent factorial effects.''',
        [ablation_rel,'results/publication/dancers-ablation-v1-repair-2/analysis.json'],
        ['Stage geometry in panels a,b is illustrative; panels c–e use registered data.',
         'Sparse workload ratios must not be pooled with multi-map results.'])
    multimap_rel='figures/publication/fig03_multimap/fig03_plot_data.csv'
    shutil.copy2(ROOT/multimap_rel,HERE/'fig04'/'data.csv')
    finalize(4,
        '''Figure 4 | Performance improvement across different environments.
(a,b) Reference/optimized complete-process wall-time and total-CPU ratios for
ten registered family-by-robot-count cells. Squares show point estimates and
horizontal whiskers show registered 95% bootstrap intervals. (c) Optimized/
reference peak-RSS ratios. The dashed line is unity; the red dotted line is the
registered 1.05 RSS-ratio ceiling. Row codes are O: one fixed open map; M:
perfect maze; L, D, H: fractal low, medium, high. The number following each
code is robot count. Each cell contains 20 scenes. Stochastic-map intervals
resample map blocks and missions; open-map intervals resample missions within
one fixed map. Across 200 scenes, all semantic and position pairs match exactly,
199 scenes pass task-validity checks, and one inherited contact is retained.
All 2,082 processes completed, including 82 warm-ups and 2,000 measured runs.
Wall speedups span 1.2128–1.3398× and CPU speedups 1.2397–1.3560× on host 1.
Memory remains approximately unchanged; independent host replication is pending.''',
        [multimap_rel,'results/publication/dancers-multimap-v1-host-1/analysis.json'],
        ['Point/interval definitions and row codes are explained only in the caption.'])
    rows=[]
    for i,s in enumerate(traj['safe_cases']):
        rows+=trajectory_rows(s)
        shutil.copy2(ROOT/s['map_path'],HERE/'fig05'/f'map_{i}.png')
    contact=traj['contact_case']
    assert sha(ROOT/contact['reference_trace_path'])==contact['reference_trace_sha256']
    assert sha(ROOT/contact['optimized_trace_path'])==contact['optimized_trace_sha256']
    crop_rel='figures/publication/fig04_trajectories/fig04_contact_map_crop.png'
    assert sha(ROOT/crop_rel)==contact['crop_png_sha256']
    contact_scene=dict(id=contact['scene_id'],family=contact['family'],
                       robot_count=contact['robot_count'],tracks=contact['tracks'])
    rows+=trajectory_rows(contact_scene)
    z=contact['zoom_bounds_m']
    rows.append(dict(kind='contact_bounds',scene_id=contact['scene_id'],
                     x_min=z['x_min_m'],x_max=z['x_max_m'],y_min=z['y_min_m'],y_max=z['y_max_m']))
    for rid,center,bearing in zip(contact['minimum_clearance']['pair'],
                                   contact['minimum_clearance']['centers_m'],
                                   contact['minimum_clearance']['bearings_rad']):
        rows.append(dict(kind='contact_pose',scene_id=contact['scene_id'],robot_id=rid,
                         x_m=center[0],y_m=center[1],bearing_rad=bearing,
                         radius_m=contact['minimum_clearance']['radius_m'],
                         time_s=contact['minimum_clearance']['time_s'],
                         minimum_clearance_m=contact['minimum_clearance']['clearance_m'],
                         first_contact_time_s=contact['first_contact']['entry_time_s']))
    write_csv(HERE/'fig05',rows)
    shutil.copy2(ROOT/crop_rel,HERE/'fig05'/'contact_crop.png')
    finalize(5,
        '''Figure 5 | What output preservation means for robot motion.
(a–e) Measured coincident trajectories for open (N=8), perfect-maze (N=6),
fractal-low (N=2), fractal-medium (N=2), and fractal-high (N=4) examples, all
at seed 11. Each complete arena is 100 m × 100 m. Solid blue paths denote
optimized DANCeRS and dashed dark paths reference DANCeRS; coordinates are
identical and never offset. Open markers show initial positions; filled discs
and heading ticks show final poses. The native coordinate frame has y increasing
downward. (f) A 12.5 m × 12.5 m crop of the inherited fractal-low, four-robot,
seed-17 contact case. Red discs show robots 0 and 3 at the continuous swept-
geometry minimum: clearance −0.277 m at 1.466 s; first contact occurs at
1.164 s. Both builds reproduce the event exactly. Across the separate held-out
analysis, 600/600 paired semantic digests match and 198/200 position scenes
pass registered task-validity checks. The retained contact example comes from
the task-calibration replay included in the authenticated trajectory extract;
the population counts come from the held-out analysis.''',
        [traj_rel,traj['source_analysis'],crop_rel,contact['reference_trace_path'],contact['optimized_trace_path']],
        ['CSV holds 101 compact poses per plotted robot and exact continuous-contact poses.',
         'Native raster maps are copied without resampling or scientific alteration.'])
    orca_rel='figures/publication/fig05_orca/fig05_plot_data.json'
    orca=load(orca_rel)
    for src in orca['authenticated_sources'].values():
        assert sha(ROOT/src['path'])==src['sha256'],src['path']
    rows=[dict(kind='behaviour',key=q['key'],label=q['label'],unit=q['unit'],
               estimate=q['estimate'],lower_95=q['lower95'],upper_95=q['upper95'],
               direction='DANCeRS minus ORCA') for q in orca['open_fixed_world']['metrics']]
    rows += [dict(kind='implementation_resource',key=q['key'],direction=q['direction'],
                  estimate=q['estimate'],lower_95=q['lower95'],upper_95=q['upper95'],unit='ratio')
             for q in orca['resources']]
    write_csv(HERE/'fig06',rows)
    finalize(6,
        '''Figure 6 | Behavioural trade-offs between DANCeRS and ORCA.
Paired native-model DANCeRS-minus-ORCA contrasts across 20 open missions for
(a) all-agent completion time, (b) mean path length, (c) mean velocity total
variation, (d) mean acceleration RMS, (e) mean jerk RMS, and (f) minimum pair
clearance. Squares show registered estimates; whiskers show 95% intervals;
the dashed line indicates zero difference. Every panel retains its own metric
unit and horizontal scale. Both methods complete all agents in these open
missions. DANCeRS has longer paths and later completion, lower acceleration
and jerk RMS, and higher velocity variation. The clearance interval crosses
zero. All 12 registered behavioural metrics and the three paired DANCeRS
implementation-resource ratios are retained in data.csv; six nonredundant
behavioural metrics are plotted. Results depend on the fixed inputs, registered
parameter mapping, native motion models, and host. Circle tasks and cadence
sensitivity belong to Extended Data.''',
        [orca_rel]+[s['path'] for s in orca['authenticated_sources'].values()],
        ['Each behavioural contrast retains a native unit; scales are independent.',
         'All registered metrics are supplied in CSV, including those not drawn.'])
    analysis_rel='results/publication/dancers-multimap-v1-host-1/analysis.json'
    analysis=load(analysis_rel)
    q=next(q for q in analysis['resources']['scene_rows'] if q['scene_id']=='perfect_maze-map-05-m39-n6')
    assert next(s for s in analysis['fidelity']['task_rows'] if s['scene_id']==q['scene_id'])['task_valid']
    rt=q['reference_medians']['wall_seconds']
    ot=q['optimized_medians']['wall_seconds']
    write_csv(HERE/'fig07',[dict(kind='worked_case',scene_id=q['scene_id'],
        reference_time_s=rt,optimized_time_s=ot,budget_s=.4,
        measured_speedup=rt/ot,required_speedup=rt/.4,
        time_saved_s=rt-ot,time_saved_fraction=1-ot/rt,
        headroom_s=.4-ot,headroom_fraction=1-ot/.4,
        reference_rss_mib=q['reference_medians']['peak_rss_mib'],
        optimized_rss_mib=q['optimized_medians']['peak_rss_mib'],
        budget_basis='illustrative requirement, not measured',
        resource_statistic='median of three timing runs per build')])
    finalize(7,
        '''Figure 7 | An engineering budget guide with a worked example.
(a) An algebraic median-time budget screen plots measured speedup S =
T_ref/T_opt against required speedup S_req = T_ref/T_budget. The diagonal is
S = S_req; the pale region satisfies S ≥ S_req. The square locates the
registered perfect-maze, six-robot, map-05, mission-39 example. (b) Complete-
process median wall times, 0.449964 s for reference and 0.342231 s for optimized
DANCeRS, with an illustrative 0.400 s budget shown by the red dashed line.
The blue arrow spans the optimized median headroom of 0.057769 s (14.44% of
the budget). Execution time falls by 23.94%, and speedup is 1.3148×. Exact
registered outputs match over 200 frames and task-validity checks pass.
Median peak RSS is 40.484 versus 40.563 MiB. Each resource summary uses three
timing runs per build on host 1. The chart supports preliminary computation-
budget screening; median values and cell confidence intervals do not establish
an individual-run deadline probability or worst-case bound. A memory budget
and task-specific validation are additional deployment requirements.''',
        [analysis_rel,'results/publication/dancers-multimap-v1-host-1/schedule.json'],
        ['The 0.400 s requirement and feasibility region are analytical illustrations.',
         'Measured times, memory, and validity come from one existing registered scene.'])
    (HERE/'README.txt').write_text(
        'DANCeRS: seven main figure packages\n\n'
        'Each fig01–fig07 folder contains draw.py, common.py, data.csv, caption.txt, '
        'provenance.json, requirements.txt, and SVG/PNG/PDF exports.\n'
        'Run python draw.py inside any figure folder; no repository files are needed.\n'
        'Arial, no visible figure title, axes/units >=14 pt, live SVG text. '
        'Default width: 183 mm. Captions are separate.\n'
        'prepare_packages.py re-extracts data from the repository and is only needed '
        'for maintaining the full collection.\n'
        'Science: registered simulator data and conceptual diagrams, with the exact '
        'source hashes in each provenance.json. No new experiments were run.\n'
        'The authors approved the seven-figure direction; layouts follow the latest '
        'request for minimal text and large Arial axis labels.\n')
    print('Prepared seven portable CSV/source packages.')


if __name__=='__main__':main()
