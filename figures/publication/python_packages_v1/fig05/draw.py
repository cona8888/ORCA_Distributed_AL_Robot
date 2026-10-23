"""Figure 5: measured paired trajectories and an inherited contact."""
from common import *


def draw(folder,width=183,height=275):
    rows=read_csv(folder)
    fig=canvas(width,height)
    scenes=list(dict.fromkeys(r['scene_id'] for r in rows if r['kind']=='track'))
    for i,scene in enumerate(scenes):
        col=i%2;row=i//2
        ax=axis(fig,[.16+col*.45,.71-row*.31,.31,.205],chr(97+i))
        if i<5:
            paths(ax,rows,scene,folder,f'map_{i}.png')
            ax.set_xticks([-50,0,50]);ax.set_yticks([-50,0,50])
        else:
            meta=next(r for r in rows if r['kind']=='contact_bounds')
            bounds=tuple(number(meta,k) for k in ['x_min','x_max','y_min','y_max'])
            paths(ax,rows,scene,folder,'contact_crop.png',bounds,endpoints=False)
            for r in rows:
                if r['kind']=='contact_pose':
                    robot(ax,number(r,'x_m'),number(r,'y_m'),number(r,'radius_m'),number(r,'bearing_rad'),color=RED,fill='#FAE7E3')
                    ax.text(number(r,'x_m'),number(r,'y_m'),r['robot_id'],ha='center',va='center',zorder=9)
            ax.set_xticks([-24,-18,-12]);ax.set_yticks([-6,0,4])
            for spine in ax.spines.values():spine.set_edgecolor(RED)
        ax.set_xlabel('x (m)')
        if col==0:ax.set_ylabel('y (m)')
    trajectory_legend(fig,.01)
    finish(fig,folder,width,height,'fig05')


if __name__=='__main__':cli(draw,5,275)
