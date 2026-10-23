"""Figure 6: each native-model contrast retains its own metric unit."""
from common import *


def draw(folder,width=183,height=195):
    rows=read_csv(folder)
    fig=canvas(width,height)
    metrics=[
        ('all_agents_makespan_s','Completion time (s)'),
        ('mean_path_length_m','Path length (m)'),
        ('mean_velocity_total_variation_mps','Velocity variation\n(m/s)'),
        ('mean_acceleration_rms_mps2','Acceleration RMS\n(m/s²)'),
        ('mean_jerk_rms_mps3','Jerk RMS (m/s³)'),
        ('minimum_pair_clearance_m','Clearance (m)'),
    ]
    for i,(key,label) in enumerate(metrics):
        row=i//2;col=i%2
        ax=axis(fig,[.13+col*.49,.79-row*.29,.32,.12],chr(97+i))
        q=next(r for r in rows if r['kind']=='behaviour' and r['key']==key)
        est=number(q,'estimate');low=number(q,'lower_95');high=number(q,'upper_95')
        ax.axvline(0,color=GREY,lw=.8,ls='--')
        interval(ax,0,est,low,high,BLUE,'s')
        span=max(abs(low),abs(high),.01)
        ax.set_xlim(min(0,low)-.15*span,max(0,high)+.15*span)
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3))
        ax.set_ylim(-.6,.6);ax.set_yticks([]);ax.spines['left'].set_visible(False)
        ax.set_xlabel(label)
    # The direction is indispensable; it is a short axis key rather than a title.
    fig.text(.50,.035,'DANCeRS − ORCA',ha='center',fontsize=FONT)
    finish(fig,folder,width,height,'fig06')


if __name__=='__main__':cli(draw,6,195)
