"""Generate editable draw.io pages and matching previews for the design discussion.

Requires Pillow. All fixture values are illustrative, not benchmark measurements.
"""
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import quote
import base64
import html
import json
import math
import zlib
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1680, 1100
SCALE = 1.5
INK, MUTED = '#172C42', '#4E6276'
BG, LINE = '#F5F7FA', '#D7E0E8'
BLUE, TEAL, PURPLE = '#215EAF', '#087D83', '#7752A3'
PALE_BLUE, PALE_TEAL, PALE_PURPLE = '#EDF4FF', '#EAF7F5', '#F4EFFA'
RED, PALE_RED = '#AD3E3B', '#FFF0EE'
FONT_ROOT = Path('C:/Windows/Fonts')


def font(size, bold=False, scale=1):
    return ImageFont.truetype(str(FONT_ROOT / ('segoeuib.ttf' if bold else 'segoeui.ttf')), round(size*scale))


class Page:
    def __init__(self, index, name, title, subtitle):
        self.index, self.name = index, name
        self.items, self.coords, self.count = [], {}, 1
        self.model = ET.Element('mxGraphModel', dx='1680', dy='1100', grid='0', gridSize='10',
                                guides='1', tooltips='1', connect='1', arrows='1', fold='1',
                                page='1', pageScale='1', pageWidth=str(W), pageHeight=str(H),
                                background=BG, math='0', shadow='0')
        self.root = ET.SubElement(self.model, 'root')
        ET.SubElement(self.root, 'mxCell', id='0')
        ET.SubElement(self.root, 'mxCell', id='1', parent='0')
        self.text(44, 24, 1500, f'AGENT EVAL FLOW  /  FUTURE VISION  /  {index:02d}', 15, True, BLUE)
        self.text(44, 57, 1592, title, 35, True)
        self.text(44, 110, 1592, subtitle, 19, False, MUTED)
        self.text(44, 1066, 1592, f'{index} / 4   •   Proposed design, 7 September 2026   •   All example tasks, outputs, costs and scores are invented; no agents were run.', 15, False, MUTED)

    def ident(self):
        self.count += 1
        return f'p{self.index}-{self.count}'

    def cell(self, x, y, w, h, value='', style='', parent='1'):
        ident = self.ident()
        cell = ET.SubElement(self.root, 'mxCell', id=ident, value=value, style=style, vertex='1', parent=parent)
        ET.SubElement(cell, 'mxGeometry', x=str(x), y=str(y), width=str(w), height=str(h), attrib={'as':'geometry'})
        return ident

    def rect(self, x, y, w, h, fill='#FFFFFF', stroke=LINE, radius=14):
        ident = self.cell(x,y,w,h,style=f'rounded={int(radius>0)};arcSize=8;absoluteArcSize=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.3;html=0;container=1;collapsible=0;')
        self.items.append(('rect',x,y,w,h,fill,stroke,radius))
        self.coords[ident] = (x,y,w,h)
        return ident

    def lines(self, value, width, size, bold=False):
        f = font(size, bold)
        result = []
        for para in value.split('\n'):
            if not para:
                result.append('')
                continue
            line = ''
            for word in para.split():
                candidate = f'{line} {word}'.strip()
                if f.getlength(candidate) > width-4 and line:
                    result.append(line)
                    line = word
                else:
                    line = candidate
            result.append(line)
        return result

    def text(self,x,y,w,value,size=20,bold=False,color=INK,parent='1'):
        lines = self.lines(value,w,size,bold)
        lineheight = round(size*1.35,2)
        height = len(lines)*lineheight+4
        assert x+w <= W+1 and y+height <= H+1, (self.name,value,y,height)
        px,py = x,y
        if parent != '1':
            ox,oy,_,_ = self.coords[parent]
            px,py = x-ox,y-oy
        self.cell(px,py,w,height,'\n'.join(lines),
                  f'text;html=0;whiteSpace=wrap;overflow=visible;align=left;verticalAlign=top;spacing=0;fontFamily=Segoe UI;fontSize={size};fontStyle={int(bold)};fontColor={color};strokeColor=none;fillColor=none;',parent)
        self.items.append(('text',x,y,w,lines,size,bold,color,lineheight))
        return height

    def card(self,x,y,w,h,title,body,fill='#FFFFFF',accent=BLUE,size=20):
        ident = self.rect(x,y,w,h,fill)
        th = self.text(x+20,y+17,w-40,title,22,True,accent,ident)
        by = y+17+th+10
        bh = self.text(x+20,by,w-40,body,size,False,INK,ident)
        assert by+bh <= y+h-12, (self.name,title,by+bh,y+h-12)
        return ident

    def arrow(self, points, color=MUTED, source=None,target=None,dashed=False):
        ident = self.ident()
        attrs = {'id':ident,'value':'','style':f'edgeStyle=none;rounded=0;html=0;endArrow=block;endFill=1;strokeColor={color};strokeWidth=2;dashed={int(dashed)};','edge':'1','parent':'1'}
        if source:
            attrs['source']=source
            attrs['style'] += 'exitX=1;exitY=0.5;exitDx=0;exitDy=0;'
        if target:
            attrs['target']=target
            attrs['style'] += 'entryX=0;entryY=0.5;entryDx=0;entryDy=0;'
        edge=ET.SubElement(self.root,'mxCell',attrs)
        g=ET.SubElement(edge,'mxGeometry',relative='1',attrib={'as':'geometry'})
        ET.SubElement(g,'mxPoint',x=str(points[0][0]),y=str(points[0][1]),attrib={'as':'sourcePoint'})
        ET.SubElement(g,'mxPoint',x=str(points[-1][0]),y=str(points[-1][1]),attrib={'as':'targetPoint'})
        if len(points)>2:
            arr=ET.SubElement(g,'Array',attrib={'as':'points'})
            for px,py in points[1:-1]:
                ET.SubElement(arr,'mxPoint',x=str(px),y=str(py))
        self.items.append(('arrow',points,color,dashed))

    def connect(self,a,b,color=MUTED):
        x,y,w,h=self.coords[a]
        xx,yy,ww,hh=self.coords[b]
        self.arrow([(x+w,y+h/2),(xx,yy+hh/2)],color,a,b)

    def table(self,x,y,widths,rows,rowh=47,highlights=None,size=19):
        highlights=highlights or {}
        for rowi,row in enumerate(rows):
            xx=x
            for coli,(cw,val) in enumerate(zip(widths,row)):
                fill = '#E6EDF5' if rowi==0 else highlights.get(rowi,'#FFFFFF')
                self.rect(xx,y+rowi*rowh,cw,rowh,fill,LINE,0)
                lines = self.lines(str(val),cw-24,size,rowi==0)
                assert len(lines)*size*1.35+4 <= rowh-10, (self.name,val,rowh)
                self.text(xx+12,y+rowi*rowh+9,cw-24,str(val),size,rowi==0)
                xx+=cw

    def render(self):
        im=Image.new('RGB',(round(W*SCALE),round(H*SCALE)),BG)
        draw=ImageDraw.Draw(im)
        svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',f'<rect width="{W}" height="{H}" fill="{BG}"/>']
        for item in self.items:
            kind,*v=item
            if kind=='rect':
                x,y,w,h,fill,stroke,r=v
                draw.rounded_rectangle(tuple(round(a*SCALE) for a in (x,y,x+w,y+h)),radius=round(r*SCALE),fill=fill,outline=stroke,width=2)
                svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
            elif kind=='text':
                x,y,w,lines,size,bold,color,lh=v
                for i,line in enumerate(lines):
                    draw.text((round(x*SCALE),round((y+i*lh)*SCALE)),line,font=font(size,bold,SCALE),fill=color,anchor='lt')
                    svg.append(f'<text x="{x}" y="{y+i*lh+size}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{html.escape(line)}</text>')
            else:
                points,color,dashed=v
                coords=[(round(x*SCALE),round(y*SCALE)) for x,y in points]
                draw.line(coords,fill=color,width=round(2*SCALE))
                x,y=points[-1]; xx,yy=points[-2]
                angle=math.atan2(y-yy,x-xx)
                triangle=[(x,y),(x-11*math.cos(angle-.44),y-11*math.sin(angle-.44)),(x-11*math.cos(angle+.44),y-11*math.sin(angle+.44))]
                draw.polygon([(round(a*SCALE),round(b*SCALE)) for a,b in triangle],fill=color)
                points_text=' '.join(f'{a},{b}' for a,b in points)
                triangle_text=' '.join(f'{a},{b}' for a,b in triangle)
                svg.extend([f'<polyline points="{points_text}" fill="none" stroke="{color}" stroke-width="2"/>',f'<polygon points="{triangle_text}" fill="{color}"/>'])
        svg.append('</svg>')
        stem=f'{self.index:02d}-{self.name}'
        im.save(OUT/f'{stem}.png')
        (OUT/f'{stem}.svg').write_text('\n'.join(svg),encoding='utf-8')


pages=[]
p=Page(1,'pipeline','One evaluation home. Separate agent studies.',
       'Follow either row from left to right. Each project owns its tasks, checks and candidate comparisons.')
pages.append(p)
p.card(44,162,1592,106,'1  Declare the experiment',
       'Choose a skill, flow, model or harness change. Freeze the task set, hidden answers, checks and allowed budget before running candidates.',PALE_BLUE,size=21)
xs=[44,324,604,940,1296]; widths=[244,244,300,320,340]
for yy,label,color,fill,task,backend,outputs,checks,report in [
    (320,'OpenSRE study',BLUE,PALE_BLUE,
     'Incident ID + alert\nFrozen telemetry\nRead-only tool replies\nAgent sees inputs only',
     'A: baseline\nB: + runbook guidance\nSame model and tools\nFresh session per run',
     'Diagnosis + report\nCausal claims + citations\nProposed remediation\nTool calls + status/error',
     'Load private answers here\nCheck cause + metric facts\nCheck proposed remedy\nRecord points + evidence',
     'Compare SRE A vs SRE B\nWhich incidents improved?\nWhich regressed, and why?\nCost / time / human effort'),
    (622,'OpenKritt study',TEAL,PALE_TEAL,
     'Repository snapshots\nVulnerable + patched\nFixed scan scope\nAgent sees code only',
     'A: baseline\nB: + review skill\nSame model and flow\nFresh scans per run',
     'Finding reports + locations\nReproduction claims\nRaw steps + lineage*\nScan status + failures',
     'Load private bug tests here\nReproduce claimed bugs\nCheck patched controls\nRecord misses + false alarms',
     'Compare Kritt A vs Kritt B\nWhich bugs were found?\nWhat was missed or false?\nCost / time / reviewer work')]:
    p.text(44,yy-40,1592,label.upper(),18,True,color)
    ids=[]
    for x,w,title,body in zip(xs,widths,['2  Task pack','3  Agent backend','4  Outputs → adapter','5  Shared eval engine','6  Project report'],[task,backend,outputs,checks,report]):
        ids.append(p.card(x,yy,w,240,title,body,fill if title.startswith('3') else '#FFFFFF',color,size=19))
    for a,b in zip(ids,ids[1:]): p.connect(a,b,color)
p.card(44,905,920,142,'One record format for both adapters',
       'task_id + candidate version + attempt_id + artifacts + status/error + elapsed time + tokens + cost + human effort. Missing evidence stays “unknown”.',PALE_PURPLE,PURPLE,size=19)
p.card(1000,905,636,142,'7  Goal → next experiment',
       'Keep the same checks. Prefer quality, lower cost or lower latency. Change one component, then repeat within that project.',PALE_BLUE,BLUE,size=19)
p.text(44,871,1550,'* OpenKritt step data needs a separate collection seam; the findings ZIP alone is insufficient. Adapters and the shared engine are proposed.',15,False,MUTED)

p=Page(2,'opensre','OpenSRE: did diagnostic guidance improve the answer?',
       'One invented incident, two complete agent configurations. Latency and cost remain separate from diagnosis quality.')
pages.append(p)
a=p.card(44,162,344,270,'1  The same incident',
       'SRE-006: explain stale reads.\nResource: db-replica-1.\nLag = 120 s; CPU = 92%.\nTool: apply_worker = blocked.\nFrozen inputs; 120 s; read-only.',PALE_BLUE,BLUE,size=21)
b=p.card(430,162,344,270,'2  OpenSRE backend',
       'A: baseline prompt.\nB: + guidance to check replication before blaming CPU.\nSame model, tools and limits.\nFresh session for A and B.',PALE_BLUE,BLUE,size=21)
c=p.card(816,162,394,270,'3  Captured agent outputs',
       'A: CPU saturation; scale CPU.\nB: stalled replication; resume the blocked apply worker.\nBoth: correct resource + metric facts.\nA: $0.18 / 70 s. B: $0.22 / 85 s.', '#FFFFFF',BLUE,size=20)
d=p.card(1252,162,384,270,'4  Adapter → eval record',
       'Report + structured claims*\nTool and environment audit\nStatus/error + duration\nUsage → declared cost rates\nLink every item to this attempt.', '#FFFFFF',BLUE,size=20)
for aa,bb in [(a,b),(b,c),(c,d)]:p.connect(aa,bb,BLUE)
p.card(44,476,416,299,'5  Independent answers',
       'Hidden cause: blocked replica apply worker. CPU = separate workload.\nResource: db-replica-1.\nFacts: lag 120 s; CPU 92%.\nApproved next action: resume the worker.\nAnswers stay outside agent inputs.',PALE_PURPLE,PURPLE,size=20)
p.text(504,471,1100,'6  Apply the predeclared diagnosis rubric (v1)',24,True,BLUE)
p.table(504,519,[512,120,120,380],[
    ['Check','A','B','Why A lost points'],
    ['Correct cause / 50','0','50','Blamed unrelated CPU'],
    ['Correct resource / 15','15','15','Resource matches'],
    ['Both metric facts / 20','20','20','120 s and 92% match'],
    ['Appropriate remedy / 15','0','15','CPU scaling misses fault'],
    ['Quality score / 100','35','100','35 = 0 + 15 + 20 + 0'],
],rowh=46,size=19,highlights={5:PALE_BLUE})
p.arrow([(460,638),(504,638)],PURPLE)
p.arrow([(1444,432),(1444,458),(1598,458),(1598,519)],BLUE)
p.card(44,817,496,207,'7  Score + acceptance + resources',
       'A: 35/100; FAIL; $0.18; 70 s.\nB: 100/100; PASS; $0.22; 85 s.\nPass: ≥80 + correct cause + completed ≤120 s + no writes.',PALE_BLUE,BLUE,size=21)
p.card(572,817,508,207,'Open the reason behind the score',
       'Lost 50 cause points → predicted CPU → hidden replication answer → telemetry evidence.\nLost 15 remedy points → CPU scaling does not address the fixture fault.',PALE_RED,RED,size=20)
p.card(1112,817,524,207,'Then test across incidents',
       'One incident is one task, even with 20 tool calls. Compare fresh attempts on misleading alerts and missing telemetry.\nThis one example does not prove B is better overall.', '#FFFFFF',BLUE,size=20)
p.text(44,1036,1592,'* CLI JSON provides status/response/denied_tools/error. Structured diagnosis and audit capture need a pipeline adapter; this is not a promised CLI export.',15,False,MUTED)

p=Page(3,'openkritt','OpenKritt: did a review skill find more real bugs?',
       'An independent security study. Its scores are meaningful within this study; they do not rank OpenKritt against OpenSRE.')
pages.append(p)
a=p.card(44,162,344,284,'1  A tiny repository task',
       'Task K-001 has two snapshots:\nV: missing ownership check (B1); unsafe path handling (B2).\nP: both bugs fixed.\nFresh scans; agent sees code.\nBug IDs and tests stay private.',PALE_TEAL,TEAL,size=20)
b=p.card(430,162,344,284,'2  OpenKritt backend',
       'A: baseline workflow W1.\nB: W1 + review skill for ownership and path handling.\nSame model, flow and budgets.\nSearch → findings → native validation / dedup / ranking.',PALE_TEAL,TEAL,size=20)
c=p.card(816,162,394,284,'3  Native outputs',
       'Finding text + file_path + line\nSummary + trigger_flow\nReproduction claim\nFindings ZIP + scan status\nSeparate step records: raw output, lineage, timings and usage*', '#FFFFFF',TEAL,size=20)
d=p.card(1252,162,384,284,'4  Invented final findings',
       'A on V: reports B1; misses B2.\nA on P: falsely reports B1.\nB on V: reports B1 and B2.\nB on P: reports neither.\nAdapter attaches finding IDs and snapshot / candidate versions.', '#FFFFFF',TEAL,size=20)
for aa,bb in [(a,b),(b,c),(c,d)]:p.connect(aa,bb,TEAL)
p.card(44,494,416,288,'5  Independent checks',
       'B1: read another user’s order.\nV returns 200; P rejects with 403.\nB2: read outside the allowed folder.\nV reads it; P rejects the request.\nPrivate tests verify these facts.\nNative verdicts are claims to check.',PALE_PURPLE,PURPLE,size=20)
p.text(504,484,1100,'6  Apply this fixture’s rubric (v1)',24,True,TEAL)
p.table(504,532,[512,120,120,380],[
    ['Check','A','B','Why A lost points'],
    ['Verified B1 on V / 40','40','40','B1 independently confirms'],
    ['Verified B2 on V / 40','0','40','No B2 finding to verify'],
    ['No false B1/B2 claims / 20','0','20','B1 is fixed on P'],
    ['Fixture score / 100','40','100','40 = 40 + 0 + 0'],
],rowh=48,size=19,highlights={4:PALE_TEAL})
p.arrow([(460,638),(504,638)],PURPLE)
p.arrow([(1444,446),(1444,474),(1598,474),(1598,532)],TEAL)
p.text(504,791,1100,'Count each known bug once. Duplicate reports earn no extra points. Other bug claims require separate adjudication.',16,False,MUTED)
p.card(44,837,496,187,'7  Read the actual outcome',
       'A: 1 true finding, 1 miss, 1 false alarm.\nB: 2 true findings, 0 misses, 0 false alarms.\nTask accepted only if both bugs are verified and the controls pass.',PALE_TEAL,TEAL,size=20)
p.card(572,837,508,187,'Inspect → form a hypothesis',
       'No B2 finding → inspect search steps: was that path skipped or checked badly?\nThe trace suggests a cause. Fresh A/B comparisons test the skill’s effect.', '#FFFFFF',TEAL,size=20)
p.card(1112,837,524,187,'Keep resource and review costs',
       'Sum V + P scan costs, including failures and retries. Record wall time and reviewer minutes.\nConfirm improvements on new repository families.', '#FFFFFF',TEAL,size=20)
p.text(44,1036,1592,'* Findings ZIP is not a full trace export. Fresh scans provide independent attempts; OpenKritt repeat_runs expands a search using earlier outputs.',15,False,MUTED)

p=Page(4,'objectives','The same evaluation results. Three different best choices.',
       'Illustrative OpenSRE study only. Keep the task set and quality checks fixed; change what you prefer among acceptable candidates.')
pages.append(p)
p.card(44,162,1030,165,'1  Run the fixed suite once for each candidate',
       '20 incidents × 3 fresh attempts = 60 attempts per candidate; 240 total.\nAn accepted attempt satisfies page 2’s rule. Accuracy here = accepted attempts / 60.\nStore all checks, costs, durations, failures and evidence.',PALE_BLUE,BLUE,size=21)
p.card(1110,162,526,165,'2  Keep the same requirements',
       'At least 90% accepted attempts.\nNo infrastructure writes.\nComplete cost and timing coverage.',PALE_PURPLE,PURPLE,size=21)
p.text(44,347,1592,'3  One saved result table — every option below reads this same table',25,True,BLUE)
p.table(44,394,[444,240,232,232,220,224],[
    ['SRE candidate','Accepted / accuracy','Mean cost / run','p95 duration','Infra. writes','Meets minimum?'],
    ['A  Baseline','45/60 = 75%','$0.16','95 s','0','No'],
    ['B  A + diagnostic guidance','54/60 = 90%','$0.24','110 s','0','Yes'],
    ['C  B + different model','59/60 = 98.3%','$0.65','100 s','0','Yes'],
    ['D  B + parallel evidence flow','57/60 = 95%','$0.90','65 s','0','Yes'],
],rowh=53,size=19,highlights={1:PALE_RED})
p.text(44,672,1592,'Cost = total declared run spend / all 60 attempts, including retries and failures. Duration = end-to-end wall time; p95 uses all attempts.',17,False,MUTED)
p.text(44,728,1592,'4  Change only the selection objective',25,True,BLUE)
p.card(44,777,508,166,'Spend less → choose B',
       'Lowest mean cost that meets requirements.\n$0.24 per attempt; 90% accepted.\nA is cheaper but fails the quality floor.',PALE_TEAL,TEAL,size=21)
p.card(586,777,508,166,'Respond faster → choose D',
       'Lowest p95 duration that meets requirements.\n65 s; 95% accepted.\nMore parallel work costs $0.90 per attempt.',PALE_BLUE,BLUE,size=21)
p.card(1128,777,508,166,'Maximize accuracy → choose C',
       'Highest acceptance rate.\n59/60 = 98.3%; $0.65 per attempt.\nAllow p95 duration up to 120 s.',PALE_PURPLE,PURPLE,size=21)
p.text(44,974,1592,'No rerun is needed to change this preference. To improve the agent itself, create candidate E and run the fixed suite again.',23,True,INK)
p.text(44,1019,1592,'These are sample choices, not proven population winners. Use per-incident uncertainty and held-out confirmation; three repeats are not three new incidents.',17,False,MUTED)

mxfile=ET.Element('mxfile',host='app.diagrams.net',agent='Agent Eval Flow design generator',version='26.0.0',type='device')
for p in pages:
    d=ET.SubElement(mxfile,'diagram',id=f'vision-{p.index}',name=f'{p.index:02d} {p.name.title()}')
    d.append(p.model)
    p.render()
xml=ET.tostring(mxfile,encoding='unicode')
path=OUT/'AGENT_EVAL_FLOW_VISION.drawio'
path.write_text(xml,encoding='utf-8')
for d in ET.parse(path).getroot().findall('diagram'):
    cells=d.findall('.//mxCell')
    ids=[c.get('id') for c in cells]
    assert len(ids)==len(set(ids)), 'Duplicate cell ID'
    for c in cells:
        for attr in ('source','target','parent'):
            assert c.get(attr) is None or c.get(attr) in ids, (c.get('id'),attr)
assert 0+15+20+0==35 and 50+15+20+15==100
assert 40+0+0==40 and 40+40+20==100
encoded=quote(xml,safe="~()*!.'-").encode()
comp=zlib.compressobj(level=9,wbits=-15)
payload=base64.b64encode(comp.compress(encoded)+comp.flush()).decode()
url='https://app.diagrams.net/?grid=0&pv=0&title=Agent%20Eval%20Flow%20-%20Future%20Vision.drawio#create='+quote(json.dumps({'type':'xml','compressed':True,'data':payload},separators=(',',':')),safe='')
(OUT/'Open-in-drawio.url').write_text('[InternetShortcut]\nURL='+url+'\n',encoding='utf-8')
body=''.join(f'<section><h2>{p.index}. {html.escape(p.name.title())}</h2><img src="{p.index:02d}-{p.name}.svg" alt="{html.escape(p.name)}"></section>' for p in pages)
(OUT/'vision.html').write_text(f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Agent Eval Flow — future vision</title><style>body{{font-family:Segoe UI,Arial,sans-serif;background:{BG};color:{INK};margin:24px auto;max-width:1680px;padding:0 24px}}a{{color:{BLUE}}}.edit{{display:inline-block;background:{BLUE};color:white;padding:14px 22px;border-radius:8px;text-decoration:none;font-weight:600}}section{{margin-top:36px}}img{{width:100%;height:auto;border:1px solid {LINE};border-radius:12px}}p{{font-size:19px;line-height:1.5}}</style><h1>Agent Eval Flow: future pipeline</h1><p>Separate OpenSRE and OpenKritt studies, shared evaluation tools. All numbers below are illustrative.</p><a class="edit" href="{html.escape(url)}" target="_blank">Open all 4 editable pages in draw.io</a> <a href="AGENT_EVAL_FLOW_VISION.drawio" download>Download .drawio</a>{body}</html>',encoding='utf-8')
print(json.dumps({'pages':len(pages),'drawio':str(path),'cells':sum(len(p.root) for p in pages),'url_length':len(url),'validation':'IDs, references, score sums and text/card bounds passed'}))
