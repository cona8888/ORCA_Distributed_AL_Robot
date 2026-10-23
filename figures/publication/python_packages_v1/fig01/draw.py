"""Figure 1: paired trajectory and unchanged execution cycle."""
from common import *


def draw(folder,width=183,height=170):
    rows=read_csv(folder)
    fig=canvas(width,height)
    a=axis(fig,[.17,.38,.67,.58],'a')
    scene=next(r['scene_id'] for r in rows if r['kind']=='track')
    paths(a,rows,scene,folder,'map.png')
    a.set_xticks([-50,0,50]);a.set_yticks([-50,0,50])
    a.set_xlabel('x (m)');a.set_ylabel('y (m)')
    b=axis(fig,[.07,.13,.88,.15],'b',diagram=True)
    # Both routes have the same order; the five markers locate implementation work.
    xs=[.24,.53,.81]
    for y,col,label in [(.70,GREY,'Reference'),(.25,BLUE,'Optimized')]:
        b.text(0,y,label,va='center')
        for x in xs:node(b,x,y,color=col,radius=.025,filled=(col==BLUE))
        for i in range(2):arrow(b,(xs[i]+.03,y),(xs[i+1]-.03,y),color=col)
    for x,lab in zip(xs,['Sense','GBP','Move']):b.text(x,-.18,lab,ha='center')
    b.text(.94,.48,'=',ha='center',va='center',fontsize=22)
    trajectory_legend(fig,.015)
    finish(fig,folder,width,height,'fig01')


if __name__=='__main__':cli(draw,1,170)
