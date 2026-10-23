"""Figure 7: an algebraic budget screen and measured worked example."""
from common import *


def draw(folder,width=183,height=180):
    rows=read_csv(folder)
    q=next(r for r in rows if r['kind']=='worked_case')
    r=number(q,'reference_time_s');o=number(q,'optimized_time_s');budget=number(q,'budget_s')
    fig=canvas(width,height)
    a=axis(fig,[.17,.54,.78,.39],'a')
    x=np.linspace(1,1.6,100)
    a.fill_between(x,x,1.6,color='#EAF2F7',zorder=0)
    a.plot(x,x,color=GREY,lw=1.0)
    a.plot(r/budget,r/o,marker='s',color=BLUE,ms=7)
    a.set_xlim(1,1.6);a.set_ylim(1,1.6)
    a.set_xticks([1,1.2,1.4,1.6]);a.set_yticks([1,1.2,1.4,1.6])
    a.set_xlabel('Required speedup (×)');a.set_ylabel('Measured speedup (×)')
    b=axis(fig,[.17,.15,.78,.25],'b')
    b.barh([1,0],[r,o],height=.38,color=[GREY,BLUE])
    b.axvline(budget,color=RED,lw=1.2,ls='--')
    b.set_yticks([1,0],labels=['Reference','Optimized'])
    b.set_xlim(0,.52);b.set_xticks([0,.2,.4]);b.set_xlabel('Wall time (s)')
    b.set_ylim(-.55,1.4)
    b.spines['left'].set_visible(False);b.tick_params(axis='y',length=0)
    arrow(b,(o,-.40),(budget,-.40),color=BLUE,width=1)
    finish(fig,folder,width,height,'fig07')


if __name__=='__main__':cli(draw,7,180)
