"""Figure 3: cumulative ablation with compact mechanism fragments."""
from common import *


def draw(folder,width=183,height=225):
    rows=read_csv(folder)
    fig=canvas(width,height)
    a=axis(fig,[.07,.83,.41,.12],'a',diagram=True)
    for y in [.76,.48,.20]:graph(a,.05,y,color=GREY)
    arrow(a,(.36,.48),(.55,.48))
    graph(a,.67,.48,color=BLUE)
    a.text(.20,-.05,'Reference',ha='center');a.text(.78,-.05,'Reuse',ha='center')
    b=axis(fig,[.57,.83,.38,.12],'b',diagram=True)
    for y in [.70,.26]:
        for x in [.06,.17,.28]:b.add_patch(Rectangle((x,y),.08,.18,fc='none',ec=GREY,lw=1))
    arrow(b,(.43,.55),(.63,.55))
    b.add_patch(Rectangle((.74,.40),.16,.24,fc='none',ec=BLUE,lw=1.2))
    b.text(.21,-.05,'Copies',ha='center');b.text(.82,-.05,'Reuse',ha='center')
    c=axis(fig,[.17,.50,.78,.23],'c')
    for n,col,mark,ls in [(500,BLUE,'s','-'),(1000,PURPLE,'o','--')]:
        r=[q for q in rows if q['record_type']=='stage' and int(q['robots'])==n]
        c.plot(range(6),[number(q,'wall_estimate') for q in r],color=col,marker=mark,ls=ls,lw=1.3,label=f'N = {n:,}')
    c.set_yscale('log');c.set_ylim(.5,30)
    c.set_yticks([1,5,20],labels=['1','5','20'])
    c.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    c.set_xticks(range(6),labels=[f'S{i}' for i in range(6)])
    c.set_ylabel('Wall time (s)');c.set_xlabel('Cumulative stage')
    c.legend(frameon=False,loc='upper right',handlelength=1.3)
    d=axis(fig,[.17,.21,.78,.19],'d')
    for n,y in [(500,1),(1000,0)]:
        q=next(q for q in rows if q['record_type']=='comparison' and int(q['robots'])==n and q['label_or_kind']=='cumulative')
        for metric,offset,col,mark in [('wall',.1,BLUE,'s'),('cpu',-.1,PURPLE,'o')]:
            interval(d,y+offset,number(q,metric+'_estimate'),number(q,metric+'_lower_95'),number(q,metric+'_upper_95'),col,mark)
    d.set_yticks([1,0],labels=['500','1,000']);d.set_ylabel('Robots')
    d.set_xlabel('S0 / S5 speedup (×)');d.set_xlim(0,17);d.set_xticks([1,5,10,15])
    d.axvline(1,color=GREY,lw=.8,ls='--')
    handles=[Line2D([],[],color=BLUE,marker='s',label='Wall'),Line2D([],[],color=PURPLE,marker='o',mfc='white',label='CPU')]
    d.legend(handles=handles,frameon=False,loc='upper left',ncol=2,handlelength=1,columnspacing=1)
    e=axis(fig,[.17,.045,.78,.09],'e')
    e.texts[0].set_position((.01,1.05))
    for n,col,mark,ls in [(500,BLUE,'s','-'),(1000,PURPLE,'o','--')]:
        r=[q for q in rows if q['record_type']=='stage' and int(q['robots'])==n]
        e.plot(range(6),[number(q,'rss_mib') for q in r],color=col,marker=mark,ls=ls,lw=1)
    e.set_ylim(0,65);e.set_yticks([0,30,60]);e.set_xticks(range(6),labels=[f'S{i}' for i in range(6)])
    e.set_ylabel('RSS (MiB)')
    finish(fig,folder,width,height,'fig03')


if __name__=='__main__':cli(draw,3,225)
