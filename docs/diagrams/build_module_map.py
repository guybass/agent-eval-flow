"""Render the proposed level-2 module map; no agent/library execution.

Uses Pillow for a PNG preview, and stdlib XML for editable draw.io and SVG.
"""
from pathlib import Path
from xml.etree import ElementTree as ET
import html
import math
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1960, 1240
INK, MUTED, BLUE, TEAL = '#18334A', '#536B7E', '#285FA5', '#087D78'
BG, STROKE = '#F6F8FB', '#CFDCE8'
FONT = Path('C:/Windows/Fonts')


def font(size, bold=False):
    return ImageFont.truetype(str(FONT / ('segoeuib.ttf' if bold else 'segoeui.ttf')), size)


def build():
    canvas = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           f'<rect width="{W}" height="{H}" fill="{BG}"/>']
    mxfile = ET.Element('mxfile', host='app.diagrams.net', version='26.0.0')
    diagram = ET.SubElement(mxfile, 'diagram', id='module-map', name='Module communication')
    model = ET.SubElement(diagram, 'mxGraphModel', page='1', pageWidth=str(W), pageHeight=str(H), background=BG)
    root = ET.SubElement(model, 'root')
    ET.SubElement(root, 'mxCell', id='0')
    ET.SubElement(root, 'mxCell', id='1', parent='0')
    counter = 1
    groups = {}

    def cell(x, y, w, h, value, style, parent='1'):
        nonlocal counter
        counter += 1
        c = ET.SubElement(root, 'mxCell', id=str(counter), value=value, style=style, vertex='1', parent=parent)
        ET.SubElement(c, 'mxGeometry', x=str(x), y=str(y), width=str(w), height=str(h), attrib={'as': 'geometry'})
        return str(counter)

    def text(x, y, value, size=19, bold=False, color=INK, parent='1', origin=(0, 0)):
        f = font(size, bold)
        assert x >= 0 and y >= 0 and x + f.getlength(value) < W and y + size + 8 < H, value
        draw.text((x, y), value, font=f, fill=color, anchor='lt')
        svg.append(f'<text x="{x}" y="{y + size}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{html.escape(value)}</text>')
        cell(x-origin[0], y-origin[1], f.getlength(value)+10, size+10, value,
             f'text;html=0;align=left;verticalAlign=top;spacing=0;fontFamily=Segoe UI;fontSize={size};fontStyle={int(bold)};fontColor={color};strokeColor=none;fillColor=none;', parent)

    def group(name, x, y, w, h, files, fill='#EDF4FE'):
        draw.rounded_rectangle((x, y, x+w, y+h), 15, fill=fill, outline=STROKE, width=2)
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="15" fill="{fill}" stroke="{STROKE}" stroke-width="2"/>')
        ident = cell(x, y, w, h, '', f'rounded=1;arcSize=8;html=0;container=1;collapsible=0;fillColor={fill};strokeColor={STROKE};strokeWidth=2;')
        groups[name] = (ident, x, y, w, h)
        text(x+20, y+18, name+'/', 25, True, BLUE, ident, (x,y))
        for i, line in enumerate(files):
            assert font(18).getlength(line) <= w-38, (name, line)
            text(x+20, y+64+i*25, line, 18, parent=ident, origin=(x,y))

    def arrow(points, label, label_xy, *, color=BLUE, dashed=False, source=None, target=None):
        nonlocal counter
        for a,b in zip(points, points[1:]):
            if dashed:
                dx,dy=b[0]-a[0],b[1]-a[1]
                length=math.hypot(dx,dy)
                for at in range(0, int(length), 14):
                    end=min(at+8,length)
                    draw.line([(a[0]+dx*at/length,a[1]+dy*at/length),(a[0]+dx*end/length,a[1]+dy*end/length)],fill=color,width=3)
            else:
                draw.line([a,b], fill=color, width=3)
        x,y=points[-1]; px,py=points[-2]
        angle=math.atan2(y-py,x-px)
        head=[(x,y),(x-13*math.cos(angle-.45),y-13*math.sin(angle-.45)),(x-13*math.cos(angle+.45),y-13*math.sin(angle+.45))]
        draw.polygon(head,fill=color)
        dash=' stroke-dasharray="8 6"' if dashed else ''
        svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="{color}" stroke-width="3"{dash}/>')
        svg.append(f'<polygon points="{" ".join(f"{x},{y}" for x,y in head)}" fill="{color}"/>')
        counter += 1
        attrs=dict(id=str(counter),value='',style=f'edgeStyle=none;rounded=0;html=0;endArrow=block;strokeColor={color};strokeWidth=3;dashed={int(dashed)};',edge='1',parent='1')
        for name, point, prefix in [(source, points[0], 'exit'), (target, points[-1], 'entry')]:
            if name is not None:
                ident,gx,gy,gw,gh=groups[name]
                attrs['source' if prefix=='exit' else 'target']=ident
                attrs['style'] += f'{prefix}X={(point[0]-gx)/gw};{prefix}Y={(point[1]-gy)/gh};{prefix}Dx=0;{prefix}Dy=0;'
        c=ET.SubElement(root,'mxCell',**attrs)
        geo=ET.SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
        ET.SubElement(geo,'mxPoint',x=str(points[0][0]),y=str(points[0][1]),attrib={'as':'sourcePoint'})
        ET.SubElement(geo,'mxPoint',x=str(x),y=str(y),attrib={'as':'targetPoint'})
        arr=ET.SubElement(geo,'Array',attrib={'as':'points'})
        for xx,yy in points[1:-1]:
            ET.SubElement(arr,'mxPoint',x=str(xx),y=str(yy))
        text(*label_xy,label,18,True,color)

    text(40,26,'AGENT EVAL FLOW  /  LEVEL 2  /  MODULE COMMUNICATION',17,True,BLUE)
    text(40,61,'One pipeline, explicit boundaries',38,True)
    text(40,113,'Files stay inside their directory. Arrows show calls or named data handoffs; the Markdown map expands the exact paths.',21,color=MUTED)
    group('objects',40,200,380,350,[
        'study.py  /  dataset.py', 'candidate.py  /  runset.py', 'suite.py  /  result.py',
        'values.py  /  protocols.py','validation.py  /  identity.py','errors.py', '',
        'Shared records and contracts.', 'Private references stay evaluator-side.'], '#F0F2F6')
    group('pipeline',500,200,420,250,[
        'api.py — EvaluationPipeline.eval()', 'bindings.py — implementations',
        'preflight.py — validate and resolve', '', 'Coordinates fresh or saved runs.'])
    group('evaluation',1030,200,410,350,[
        'compiler.py — metric dependencies','engine.py — callback execution',
        'primitives.py — run.* helpers','scoring.py — rubric / acceptance',
        'aggregation.py — candidate summaries','', 'User-defined evaluators and reducers.',
        'Receives runs; never starts an agent.'], '#ECF7F3')
    group('results',1550,200,360,250,[
        'query.py — explain / summary','comparison.py — differences',
        'selection.py — preferences','', 'Reads saved measurements.'], '#ECF7F3')
    group('adapters',40,740,380,295,[
        'codex.py  /  claude_code.py','opensre.py  /  openkritt.py',
        'process.py — native supervision','worker.py — prepared GCP worker','',
        'Native programs own agent behavior.','Adapters preserve their evidence.'], '#FFF5E8')
    group('execution',500,740,420,295,[
        'planning.py — assignments','preflight.py — backend capabilities',
        'runner.py — bounded dispatch','capture.py — recorder reconciliation',
        'importing.py — saved native records','', 'Fresh RunSet/run IDs; stable assignments.'])
    group('storage',1030,740,410,245,[
        'manifests.py — save / load','codec.py — typed JSON',
        'artifacts.py — local evidence cache','', 'Records + references in v0.'], '#F5F0FB')
    group('reporting',1550,740,360,210,[
        'html.py — existing result to HTML','', 'Optional selection + evidence links.',
        'No grading or remote fetch.'], '#F5F0FB')
    arrow([(420,285),(500,285)],'Study',(429,254),color=MUTED,source='objects',target='pipeline')
    arrow([(710,450),(710,740)],'1  execute(PreparedExecution)',(730,619),source='pipeline',target='execution')
    arrow([(920,295),(1030,295)],'2  grade',(938,261),source='pipeline',target='evaluation')
    arrow([(1440,295),(1550,295)],'result*',(1460,261),color=TEAL,source='evaluation',target='results')
    arrow([(500,832),(420,832)],'run',(444,800),source='execution',target='adapters')
    arrow([(420,962),(500,962)],'capture',(426,928),color=TEAL,dashed=True,source='adapters',target='execution')
    arrow([(1730,450),(1730,740)],'report(result)',(1750,626),color=TEAL,source='results',target='reporting')
    arrow([(1630,450),(1630,620),(1235,620),(1235,740)],'save / load result*',(1300,588),color=TEAL,source='results',target='storage')
    arrow([(1440,850),(1550,850)],'loaded*',(1456,816),color=TEAL,source='storage',target='reporting')
    arrow([(230,1035),(230,1110),(1225,1110),(1225,985)],'Materialize native artifacts and remap evidence links',(480,1075),color=MUTED,dashed=True,source='adapters',target='storage')
    text(40,1165,'Blue: runtime calls. Green: returned data / user-invoked result operations. Dashed: evidence transfer.',19,True)
    text(40,1198,'* Handoffs pass through the caller/result methods. Routine type imports and return arrows are omitted. Proposed design, 8 September 2026.',18,color=MUTED)
    svg.append('</svg>')
    (OUT/'module-communication.svg').write_text('\n'.join(svg),encoding='utf-8')
    canvas.save(OUT/'module-communication.png')
    ET.indent(mxfile)
    ET.ElementTree(mxfile).write(OUT/'module-communication.drawio',encoding='utf-8',xml_declaration=True)
    print('Generated module-communication.svg, .png and .drawio')


if __name__=='__main__':
    build()
