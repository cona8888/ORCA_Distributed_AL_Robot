"""Shared drawing tools. Every delivered figure contains a local copy."""
from pathlib import Path
import csv
import hashlib
import html
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'dancers-figure-mpl'))
os.environ.setdefault('XDG_CACHE_HOME', str(Path(tempfile.gettempdir()) / 'dancers-figure-cache'))
Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Ellipse, Rectangle, FancyArrowPatch, Polygon
import numpy as np
from PIL import Image

BLUE = '#2F6F9F'
GREY = '#777D82'
INK = '#202124'
LIGHT = '#DDE2E5'
PURPLE = '#7A6FA8'
RED = '#C94C4C'
FONT = 14
PANEL = 16


def setup():
    for folder in [Path('/System/Library/Fonts/Supplemental'), Path('/Library/Fonts'),
                   Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts']:
        if folder.exists():
            for font in folder.glob('*'):
                if font.suffix.lower() in ('.ttf', '.otf') and 'arial' in font.name.lower():
                    font_manager.fontManager.addfont(str(font))
    font_manager.findfont('Arial', fallback_to_default=False)
    plt.rcParams.update({
        'font.family': 'Arial', 'font.size': FONT, 'axes.labelsize': FONT,
        'xtick.labelsize': FONT, 'ytick.labelsize': FONT, 'legend.fontsize': FONT,
        'axes.titlesize': FONT, 'axes.linewidth': 0.8, 'axes.labelcolor': INK,
        'axes.edgecolor': INK,
        'text.color': INK, 'xtick.color': INK, 'ytick.color': INK,
        'svg.fonttype': 'none', 'pdf.fonttype': 42, 'savefig.facecolor': 'white',
        'figure.facecolor': 'white', 'axes.grid': False, 'axes.unicode_minus': True,
        'svg.hashsalt': 'dancers-python-package-v1',
    })


def canvas(width, height):
    setup()
    return plt.figure(figsize=(width / 25.4, height / 25.4))


def axis(fig, bounds, letter=None, diagram=False):
    ax = fig.add_axes(bounds)
    if letter:
        ax.set_gid('panel-' + letter)
        ax.text(-0.10 if not diagram else 0, 1.015 if not diagram else 1.0,
                letter, transform=ax.transAxes, fontsize=PANEL, fontweight='bold',
                va='bottom', ha='left', clip_on=False).set_gid('panel-label-' + letter)
    if diagram:
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect('auto')
        ax.set_axis_off()
    else:
        ax.spines[['top', 'right']].set_visible(False)
        ax.tick_params(direction='out', width=0.8, length=3)
    return ax


def read_csv(folder, name='data.csv'):
    with (folder / name).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def number(row, key):
    return float(row[key])


def arrow(ax, start, end, color=INK, dashed=False, width=1.0):
    obj = FancyArrowPatch(start, end, arrowstyle='-|>', mutation_scale=12,
                          linewidth=width, color=color,
                          linestyle='--' if dashed else '-', shrinkA=3, shrinkB=3)
    ax.add_patch(obj)
    return obj


def node(ax, x, y, factor=False, color=INK, radius=0.018, filled=False):
    # Compensate for non-square diagram axes so variables stay circular and
    # factors square at final physical size.
    pos=ax.get_position()
    ratio=(ax.figure.get_figwidth()*pos.width)/(ax.figure.get_figheight()*pos.height)
    yr=radius*ratio
    shape = (Rectangle((x-radius, y-yr), 2*radius, 2*yr,
                       facecolor=color, edgecolor=color, lw=1)
             if factor else Ellipse((x, y), 2*radius, 2*yr,
                                   facecolor=color if filled else 'white',
                                   edgecolor=color, lw=1.2))
    ax.add_patch(shape)
    return shape


def graph(ax, x, y, scale=1, color=PURPLE):
    """A schematic fragment, not a sampled robot communication graph."""
    for i in range(3):
        node(ax, x+i*0.09*scale, y, radius=0.014*scale, color=color)
        if i < 2:
            ax.plot([x+i*.09*scale, x+(i+1)*.09*scale], [y,y], color=color, lw=1)
            node(ax, x+(i+.5)*.09*scale, y, factor=True, radius=.009*scale, color=color)


def robot(ax, x, y, radius, bearing, color=BLUE, fill='white'):
    if not ax.axison:
        pos=ax.get_position()
        yr=radius*(ax.figure.get_figwidth()*pos.width)/(ax.figure.get_figheight()*pos.height)
        ax.add_patch(Ellipse((x,y),2*radius,2*yr,fc=fill,ec=color,lw=1.1,zorder=6))
    else:
        yr=radius
        ax.add_patch(Circle((x, y), radius, fc=fill, ec=color, lw=1.1, zorder=6))
    ax.plot([x, x + 1.6*radius*np.cos(bearing)],
            [y, y + 1.6*yr*np.sin(bearing)], color=INK, lw=.9, zorder=7)


def paths(ax, rows, scene, folder, map_file, bounds=(-50,50,-50,50), endpoints=True):
    x0,x1,y0,y1 = bounds
    ax.imshow(Image.open(folder / map_file).convert('L'), cmap='gray',
              vmin=0, vmax=255, alpha=.20, origin='upper',
              extent=(x0,x1,y1,y0), interpolation='nearest', zorder=0)
    subset = [r for r in rows if r['scene_id'] == scene and r['kind'] == 'track']
    for rid in sorted(set(r['robot_id'] for r in subset), key=int):
        r = sorted([q for q in subset if q['robot_id'] == rid], key=lambda q:float(q['time_s']))
        x = [number(q,'x_m') for q in r]
        y = [number(q,'y_m') for q in r]
        ax.plot(x,y,color=BLUE,lw=2,zorder=3)
        ax.plot(x,y,color=INK,lw=.85,ls=(0,(4,3)),zorder=4)
        if endpoints:
            ax.plot(x[0],y[0],marker='o',mfc='white',mec=BLUE,color=BLUE,
                    ls='none',ms=5,zorder=5)
            robot(ax,x[-1],y[-1],number(r[-1],'radius_m'),number(r[-1],'bearing_rad'),fill=BLUE)
    ax.set_xlim(x0,x1)
    ax.set_ylim(y1,y0)  # Native image/world frame: increasing y is downward.
    ax.set_aspect('equal')
    return subset


def trajectory_legend(fig, y):
    handles=[Line2D([],[],color=BLUE,lw=2,label='Optimized'),
             Line2D([],[],color=INK,lw=.9,ls='--',label='Reference')]
    fig.legend(handles=handles, loc='lower center',bbox_to_anchor=(.52,y),
               ncol=2,frameon=False,handlelength=1.5,columnspacing=1.2)


def interval(ax, y, estimate, low, high, color=BLUE, marker='s'):
    ax.errorbar(estimate,y,xerr=[[estimate-low],[high-estimate]],
                fmt=marker,color=color,ms=5,lw=1.1,capsize=2,mec=color,
                mfc='white' if marker=='o' else color)


def finish(fig, folder, width, height, stem):
    """Export without tight cropping so the specified physical size is preserved."""
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    outside=[]
    # Ignore tick objects beyond the axis limits: Matplotlib retains these
    # internally but does not draw them on the canvas.
    shown=set(fig.texts)
    for ax in fig.axes:
        shown.update(ax.texts)
        shown.update([ax.xaxis.label,ax.yaxis.label,ax.title])
        for axis_obj in [ax.xaxis,ax.yaxis]:
            shown.add(axis_obj.offsetText)
            for tick in axis_obj._update_ticks():
                shown.update([tick.label1,tick.label2])
        if ax.get_legend():shown.update(ax.get_legend().get_texts())
    for legend in fig.legends:shown.update(legend.get_texts())
    for text in shown:
        if text.get_visible() and text.get_text():
            if text.get_fontsize() < FONT:
                raise RuntimeError('Text smaller than 14 pt: '+text.get_text())
            box=text.get_window_extent(renderer)
            if box.x0 < -1 or box.y0 < -1 or box.x1 > fig.bbox.width+1 or box.y1 > fig.bbox.height+1:
                outside.append(text.get_text())
    if outside:
        raise RuntimeError('Text outside canvas: '+repr(outside))
    svg=folder/(stem+'.svg')
    fig.savefig(svg,metadata={'Date':None,'Creator':'Matplotlib; Arial; live SVG text'})
    # Matplotlib's viewBox uses points. Its live text sizes therefore remain pt
    # at the physical mm dimensions below.
    text=svg.read_text(encoding='utf-8')
    text=re.sub(r'width="[^"]+pt" height="[^"]+pt"',
                f'width="{width}mm" height="{height}mm"',text,count=1)
    # Explicitly record SVG's default unit stroke instead of relying on viewer
    # defaults. All existing nondefault Matplotlib widths remain unchanged.
    def explicit_stroke(match):
        style=match.group(1)
        if re.search(r'(?:^|;)\s*stroke\s*:',style) and 'stroke-width' not in style:
            style+='; stroke-width: 1'
        return 'style="'+style+'"'
    text=re.sub(r'style="([^"]*)"',explicit_stroke,text)
    description=(folder/'caption.txt').read_text(encoding='utf-8').strip()
    text=re.sub(r'(<svg\b[^>]*)(>)',
                lambda m:m.group(1)+' role="img" aria-labelledby="svg-title svg-desc"'+m.group(2)
                +'<title id="svg-title">'+stem+'</title><desc id="svg-desc">'
                +html.escape(description)+'</desc>',text,count=1)
    svg.write_text(text,encoding='utf-8')
    fig.savefig(folder/(stem+'.png'),dpi=300)
    fig.savefig(folder/(stem+'_600dpi.png'),dpi=600)
    fig.savefig(folder/(stem+'.pdf'),metadata={'Title':None,'CreationDate':None})
    ET.parse(svg)
    report={'figure':stem,'width_mm':width,'height_mm':height,
            'font':'Arial','minimum_text_pt':FONT,'visible_title':False,
            'text_outside_canvas':outside,'svg_live_text':True,
            'data_sha256':hashlib.sha256((folder/'data.csv').read_bytes()).hexdigest(),
            'status':'technical checks passed; visual review recorded separately'}
    (folder/'technical_review.json').write_text(json.dumps(report,indent=2)+'\n')
    plt.close(fig)


def cli(draw, number_id, default_height):
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--width-mm',type=float,default=183)
    parser.add_argument('--height-mm',type=float,default=default_height)
    args=parser.parse_args()
    folder=Path(__import__('sys').argv[0]).resolve().parent
    draw(folder,args.width_mm,args.height_mm)
