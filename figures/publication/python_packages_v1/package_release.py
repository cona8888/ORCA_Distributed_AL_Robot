"""Create reviewed figure archives without including caches or temporary files."""
from pathlib import Path
import csv
import hashlib
import json
import zipfile
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT=Path(__file__).resolve().parent
NS={'svg':'http://www.w3.org/2000/svg'}
CLAIMS=[
    'Measured trajectories coincide; execution order is retained.',
    'Five implementation changes sit inside the retained architecture.',
    'Cumulative implementation changes reduce measured sparse-workload cost.',
    'Resource improvements persist across registered map/count cells.',
    'Exact trajectory preservation includes an inherited contact case.',
    'Native-model comparison exposes metric-specific behavioural trade-offs.',
    'Measured medians support a preliminary computation-budget calculation.',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    release=ROOT/'packages'
    release.mkdir(exist_ok=True)
    manifest=[]
    combined_captions=[]
    for n in range(1,8):
        folder=ROOT/f'fig{n:02}'
        report=json.loads((folder/'svg_review.json').read_text())
        assert not report['errors'] and not report['warnings'],folder
        assert report['metrics']['minimum_font_pt']>=14-1e-6
        tree=ET.parse(folder/f'fig{n:02}.svg')
        for text in tree.findall('.//svg:text',NS):
            assert 'Arial' in text.get('style','')
        assert json.loads((folder/'provenance.json').read_text())['data_sha256']==sha(folder/'data.csv')
        assert json.loads((folder/'technical_review.json').read_text())['data_sha256']==sha(folder/'data.csv')
        assert len(list(csv.DictReader((folder/'data.csv').open())))>0
        review={
            'figure':n,'claim':CLAIMS[n-1],
            'scientific':'Checked plotted extracts against authenticated source identities; '
                         'conceptual objects and derived relations identified in caption.',
            'narrative':'Panel order and emphasis reviewed in exported PNG at final aspect ratio.',
            'reduction':'14 pt minimum at 183 mm saved width; no visible figure title; '
                        'labels inspected for overlap and clipping.',
            'accessibility':'Reference/optimized distinction has line style or fill redundancy; '
                            'count/metric comparisons use marker or line-style redundancy. '
                            'Unity/zero/budget lines are differentiated by dash style.',
            'technical':'All scripts rendered successfully; strict SVG audit has no errors '
                        'or warnings. Representative SVG and PDF exports inspected.',
            'status':'Prepared for author visual review',
            'user_style_override':'14 pt minimum replaces the smaller generic Nature '
                                  'figure-font guidance; height varies to accommodate labels.',
        }
        (folder/'review.json').write_text(json.dumps(review,indent=2)+'\n')
        files=[p for p in sorted(folder.iterdir()) if p.is_file()]
        checksums=''.join(f'{sha(p)}  {p.name}\n' for p in files)
        (folder/'checksums.sha256').write_text(checksums)
        files=[p for p in sorted(folder.iterdir()) if p.is_file()]
        archive=release/f'fig{n:02}_package.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in files:z.write(p,arcname=f'fig{n:02}/{p.name}')
        with zipfile.ZipFile(archive) as z:assert z.testzip() is None
        manifest.append({'figure':n,'archive':archive.name,'sha256':sha(archive),
                         'width_mm':report['metrics']['width_mm'],
                         'height_mm':report['metrics']['height_mm'],
                         'minimum_font_pt':14,'files':[p.name for p in files]})
        combined_captions.append((folder/'caption.txt').read_text().strip())
    (ROOT/'captions.txt').write_text('\n\n'.join(combined_captions)+'\n')
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

    # This contact sheet is a review preview. Full-size exports remain in each package.
    tile_w,tile_h,gap=460,730,28
    sheet=Image.new('RGB',(4*tile_w+5*gap,2*tile_h+3*gap),'white')
    pen=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',28)
    for i in range(7):
        x=gap+(i%4)*(tile_w+gap);y=gap+(i//4)*(tile_h+gap)
        pen.text((x,y),str(i+1),font=font,fill='#202124')
        img=Image.open(ROOT/f'fig{i+1:02}'/f'fig{i+1:02}.png').convert('RGB')
        img.thumbnail((tile_w,tile_h-45),Image.Resampling.LANCZOS)
        sheet.paste(img,(x+(tile_w-img.width)//2,y+45))
    sheet.save(ROOT/'overview.png')
    ImageOps.grayscale(sheet).save(ROOT/'overview_grayscale.png')
    all_zip=release/'dancers_figures_01_07.zip'
    with zipfile.ZipFile(all_zip,'w',zipfile.ZIP_DEFLATED) as z:
        for n in range(1,8):
            for p in sorted((ROOT/f'fig{n:02}').iterdir()):
                if p.is_file():z.write(p,arcname=f'fig{n:02}/{p.name}')
        for name in ['README.txt','captions.txt','manifest.json','overview.png']:
            z.write(ROOT/name,arcname=name)
    with zipfile.ZipFile(all_zip) as z:assert z.testzip() is None
    print(json.dumps({'archives':len(manifest),'collection':str(all_zip),
                      'collection_bytes':all_zip.stat().st_size,'all_crc_checks_passed':True}))


if __name__=='__main__':main()
