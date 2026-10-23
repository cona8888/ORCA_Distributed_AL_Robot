"""Figure 2: original schematic of two local factor-graph stacks."""
from common import *


def draw(folder,width=183,height=150):
    fig=canvas(width,height)
    ax=axis(fig,[.06,.28,.90,.64],'a',diagram=True)
    # Graph fragments depict variable/factor semantics, not measured connectivity.
    for xc,label in [(.23,'Robot i'),(.76,'Robot j')]:
        robot(ax,xc,.92,.035,np.pi/3)
        ax.text(xc,.82,label,ha='center')
        for y in [.62,.24]:
            for idx,x in enumerate(np.linspace(xc-.15,xc+.15,4)):
                node(ax,x,y,radius=.017)
                if idx<3:
                    ax.plot([x,x+.10],[y,y],color=INK,lw=.9)
                    node(ax,x+.05,y,factor=True,radius=.010,color=PURPLE)
            for x in [xc-.10,xc+.10]:
                node(ax,x,y+.10,factor=True,radius=.010,color=PURPLE)
                ax.plot([x, x-.05],[y+.10,y],color=PURPLE,lw=.9)
                ax.plot([x, x+.05],[y+.10,y],color=PURPLE,lw=.9)
        arrow(ax,(xc,.53),(xc,.34),color=PURPLE,dashed=True)
    ax.text(.495,.68,'Planning',ha='center')
    ax.text(.495,.31,'Consensus',ha='center')
    arrow(ax,(.39,.24),(.60,.24),color=PURPLE,dashed=True)
    arrow(ax,(.60,.18),(.39,.18),color=PURPLE,dashed=True)
    ax.text(.49,.10,'GBP',ha='center')
    # Numbered surfaces correspond to data.csv and the caption.
    for row in read_csv(folder):
        x=number(row,'x');y=number(row,'y')
        if row.get('target_x'):
            ax.plot([x,number(row,'target_x')],[y,number(row,'target_y')],color=GREY,lw=.7,zorder=.5)
        node(ax,x,y,color=BLUE,radius=.025)
        ax.text(x,y,row['optimization'],ha='center',va='center')
    b=axis(fig,[.07,.07,.87,.10],'b',diagram=True)
    for x,lab in zip([.12,.45,.81],['Sense','GBP','Move']):
        b.text(x,.6,lab,ha='center',va='center')
    arrow(b,(.23,.6),(.37,.6));arrow(b,(.56,.6),(.72,.6),color=BLUE)
    b.plot([.81,.81,.12,.12],[.3,0,0,.3],color=INK,lw=.8)
    arrow(b,(.12,.3),(.12,.45),width=.8)
    finish(fig,folder,width,height,'fig02')


if __name__=='__main__':cli(draw,2,150)
