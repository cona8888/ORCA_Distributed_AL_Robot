"""Figure 4: prospective map/count cell resource intervals."""
from common import *


def draw(folder,width=183,height=200):
    rows=read_csv(folder)
    fig=canvas(width,height)
    codes={'open':'O','perfect_maze':'M','fractal_low':'L','fractal_medium':'D','fractal_high':'H'}
    labels=[f"{codes[r['family']]} · {r['robot_count']}" for r in rows]
    for bounds,letter,metric,xlabel in [
        ([.19,.56,.32,.36],'a','wall','Wall speedup (×)'),
        ([.65,.56,.32,.36],'b','cpu','CPU speedup (×)'),
        ([.19,.11,.78,.34],'c','rss','RSS ratio (×)')]:
        ax=axis(fig,bounds,letter)
        for i,r in enumerate(rows):
            interval(ax,9-i,number(r,metric+'_estimate'),number(r,metric+'_lower_95'),number(r,metric+'_upper_95'),BLUE)
        ax.set_ylim(-.6,9.6);ax.set_yticks(range(10),labels=list(reversed(labels)))
        ax.set_xlabel(xlabel)
        ax.axvline(1,color=GREY,ls='--',lw=.8)
        if metric!='rss':ax.set_xlim(.99,1.39);ax.set_xticks([1,1.2,1.4]);
        else:
            ax.set_xlim(.994,1.054);ax.set_xticks([1,1.025,1.05],labels=['1.000','1.025','1.050'])
            ax.axvline(1.05,color=RED,lw=.8,ls=':')
        if metric=='cpu':ax.set_yticklabels([])
    finish(fig,folder,width,height,'fig04')


if __name__=='__main__':cli(draw,4,200)
