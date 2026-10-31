from __future__ import annotations

from datetime import datetime
from html import escape
import math
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph
from reportlab import rl_config

from .i18n import TEXT


def fonts():
    for name, family, bold in [("News", "Liberation Serif", False), ("NewsBold", "Liberation Serif", True),
                               ("UI", "Liberation Sans", False), ("UIBold", "Liberation Sans", True)]:
        if name in pdfmetrics.getRegisteredFontNames():
            continue
        try:
            pattern = family + (":style=Bold" if bold else ":style=Regular")
            path = subprocess.check_output(["fc-match", "-f", "%{file}", pattern], text=True).strip()
            pdfmetrics.registerFont(TTFont(name, path))
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("Install fontconfig and Liberation fonts to render Turkish correctly") from exc


def block(text, width, font, size, color="#171717", leading=1.22):
    style = ParagraphStyle("text", fontName=font, fontSize=size, leading=size*leading,
                           textColor=HexColor(color), splitLongWords=True)
    p = Paragraph(escape(text), style)
    _, height = p.wrap(width, 1000)
    return p, height


def draw_text(canvas, text, x, top, width, font, size, color="#171717"):
    p, h = block(text, width, font, size, color)
    p.drawOn(canvas, x, top-h)
    return h


def fit_text(text, width, height, font, size, minimum=8.5):
    current = size
    while current >= minimum:
        p, h = block(text, width, font, current)
        if h <= height:
            return p, h, text
        current -= .25
    # Show an ellipsis; preserve the full summary in JSON.
    words = text.split()
    while len(words) > 4:
        words.pop()
        displayed = " ".join(words).rstrip(".,;:") + "..."
        p, h = block(displayed, width, font, minimum)
        if h <= height:
            return p, h, displayed
    raise ValueError("Text cannot fit in A4 layout at a readable size")


def render(path, items, config, theme, now, warnings, extra=None):
    if not items:
        raise ValueError("Cannot render an empty edition")
    fonts()
    # PDFio-based CUPS filters can silently emit blank pages for ReportLab's
    # default ASCII85 + Flate compound streams (libcupsfilters issue #301).
    # Single Flate streams also keep image resources compatible with that path.
    rl_config.useA85 = 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = TEXT[config["language"]]
    extra = extra or {}
    branding = config["branding"]
    from reportlab.lib.pagesizes import LETTER
    width, height = A4 if config["layout"]["paper_size"] == "A4" else LETTER
    margin, gap, accent = theme["margin"], theme["gap"], theme["accent"]
    usable = width - margin*2
    newspaper = theme["layout"] == "newspaper"
    page_count = min(config["layout"]["pages"], len(items))
    per_page = math.ceil(len(items)/page_count)
    pages = [items[i:i+per_page] for i in range(0, len(items), per_page)]
    if per_page > config["layout"]["articles_per_page"]:
        raise ValueError("Selected articles exceed configured page capacity")
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pdf", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        c = Canvas(str(temp_path), pagesize=(width, height))
        c.setTitle(f"Ulak | {now:%Y-%m-%d}")
        c.setAuthor("Ulak")
        c.setSubject(text["eyebrow"])

        def rule(y, weight=.4, color=None, x=margin, right=width-margin):
            c.setStrokeColor(HexColor(color or accent))
            c.setLineWidth(weight)
            c.line(x, y, right, y)

        def article(item, index, x, y, w, h, lead=False):
            update=(' / '+text['update']) if item.get('status')=='update' else ''
            tag, tag_h, _ = fit_text(f'[{index+1:02d}] / {item["topic"]}'+update, w, 12, "UIBold", 7.5, 7)
            tag.drawOn(c, x, y-tag_h)
            title_top = y-18
            title, th, shown_title = fit_text(item["title"], w, min(58, h*.32),
                theme["heading_font"], 22 if lead else 13, 12 if lead else 10)
            title.drawOn(c, x, title_top-th)
            meta_y = y-h+34
            body_top = title_top-th-7
            body_width = w
            illustration_height = min(64, body_top-meta_y-12)
            has_qr=config['references']['qr'] and illustration_height>=60
            if has_qr:
                from reportlab.graphics.barcode.qr import QrCodeWidget
                from reportlab.graphics.shapes import Drawing
                from reportlab.graphics import renderPDF
                qr=QrCodeWidget(item['url']);bounds=qr.getBounds();size=60
                drawing=Drawing(size,size,transform=[size/(bounds[2]-bounds[0]),0,0,size/(bounds[3]-bounds[1]),0,0]);drawing.add(qr)
                renderPDF.draw(drawing,c,x+w-size,body_top-size)
                body_width=w-size-10
                item['qr_printed']=True
            elif (item.get("_image") is not None or item.get("_ascii")) and illustration_height >= 36 and w >= 240:
                illustration_width = 106
                body_width = w-illustration_width-10
                if item.get("_ascii"):
                    c.setFillColor(HexColor("#111111"))
                    line_height = min(4.4, illustration_height/len(item["_ascii"]))
                    c.setFont("Courier", min(4,line_height*.9))
                    for row, line in enumerate(item["_ascii"]):
                        c.drawString(x+w-illustration_width, body_top-line_height-row*line_height, line)
                else:
                    from reportlab.lib.utils import ImageReader
                    c.drawImage(ImageReader(item["_image"]), x+w-illustration_width,
                                body_top-illustration_height, width=illustration_width,
                                height=illustration_height, preserveAspectRatio=True, anchor='n', mask='auto')
            body, bh, shown_summary = fit_text(item["summary"], body_width, body_top-meta_y-12,
                theme["body_font"], theme["body_size"])
            body.drawOn(c, x, body_top-bh)
            stamp = datetime.fromisoformat(item["published"]).astimezone(now.tzinfo)
            label = f'[{index+1:02d}] {item["source"]} / {stamp:%d.%m %H:%M}'
            meta, mh, _ = fit_text(label, w, 12, "UIBold", 7.5, 7)
            meta.drawOn(c, x, meta_y-mh)
            kind = text["model"] if item["summary_kind"] == "model_summary" else text["excerpt"]
            if config['references']['domains']: kind=(urlparse(item['url']).hostname or 'source').replace('www.','')+' / '+kind
            if item.get('corroborating_sources',1)>1: kind+=f' / +{item["corroborating_sources"]-1}'
            source_line,source_h,_=fit_text(kind,w,10,'UI',7,6.5)
            source_line.drawOn(c,x,meta_y-12-source_h)
            c.linkURL(item["url"], (x, title_top-th, x+w, title_top), thickness=0)
            c.linkURL(item["url"], (x, meta_y-mh, x+w, meta_y), thickness=0)
            rule(y-h+10, .4, "#444444" if newspaper else "#CBD1CC", x, x+w)
            item["display_title"], item["display_summary"] = shown_title, shown_summary
            item['layout_box']=dict(x=x,top=y,width=w,height=h)
            item["page"] = page_number

        number = 0
        for page_number, page_items in enumerate(pages, 1):
            date = text['date_format'].format(day=now.day,month=text['months'][now.month],year=now.year)
            meta = text["window"].format(hours=config["hours"])
            if config["name"]:
                meta = config["name"] + " / " + meta
            if newspaper:
                rule(height-margin, 1.2)
                draw_text(c, branding["subtitle"] or text["gazette"], margin, height-margin-8, usable/2, "News", 8)
                masthead, mast_h, _ = fit_text(branding["title"], usable, 58, "NewsBold", 47, 23)
                masthead.style.alignment = 1
                masthead.wrap(usable, 100)
                masthead.drawOn(c, margin, height-margin-11-mast_h)
                right, rh = block(date, usable, "News", 9)
                right.style.alignment = 2
                right.wrap(usable, 30)
                right.drawOn(c, margin, height-margin-8-rh)
                rule(height-margin-72, 1.4)
                rule(height-margin-76, .4)
                draw_text(c, branding["tagline"] or text["motto"], margin, height-margin-84, usable*.65, "News", 8)
                right, rh = block(meta, usable*.35, "News", 8)
                right.style.alignment = 2
                right.wrap(usable*.35, 30)
                right.drawOn(c, margin+usable*.65, height-margin-84-rh)
                top = height-margin-111
            else:
                draw_text(c, text["eyebrow"], margin, height-margin, usable, "UIBold", 7.5, accent)
                brand, brand_h, _ = fit_text(branding["title"], usable*.5, 44, "NewsBold", 35, 18)
                brand.drawOn(c, margin-1, height-margin-12-brand_h)
                draw_text(c, date, width-margin-220, height-margin-5, 220, "UIBold", 10)
                draw_text(c, meta, width-margin-220, height-margin-22, 220, "UI", 8.5, "#64706A")
                rule(height-margin-58, 1.2)
                draw_text(c, text["strap"], margin, height-margin-66, usable, "UI", 8.5, "#64706A")
                top = height-margin-94
            if extra.get("weather") and page_number == 1:
                weather = extra["weather"]
                label = f'{weather["label"]}: {weather["temperature"]:.0f} °C / {weather["condition"]} / {weather["wind"]:.0f} km/h / Open-Meteo {weather["observed"][-5:]}'
                draw_text(c, label, margin, top+3, usable, "News" if newspaper else "UI", 8)
                c.linkURL(weather["url"], (margin, top-8, width-margin, top+3), thickness=0)
                top -= 17
            if extra.get('introduction') and page_number==1:
                intro,intro_h,_=fit_text(extra['introduction'],usable,27,'News',9)
                intro.drawOn(c,margin,top-intro_h)
                top-=intro_h+12
            # Extras yield to a crowded news page instead of shrinking body text.
            puzzle = extra.get("puzzle") if page_number == len(pages) and len(page_items)<=5 else None
            concept = extra.get('concept') if page_number==len(pages) and len(page_items)<=5 else None
            bottom = 178 if puzzle or concept else 83
            remaining = page_items
            if newspaper:
                lead_height = (185 if len(page_items[0]['summary'].split())>65 else 163) if len(page_items)>1 else top-bottom
                article(page_items[0], number, margin, top, usable, lead_height, lead=True)
                number += 1
                remaining = page_items[1:]
                top -= lead_height+9
            if remaining:
                columns = min(config["layout"]["columns"], len(remaining))
                rows = math.ceil(len(remaining)/columns)
                cell_h = (top-bottom)/rows
                col_w = (usable-gap*(columns-1))/columns
                heights=[cell_h]*rows
                if config['layout']['adaptive'] and rows>1:
                    desired=[]
                    for row in range(rows):
                        pair=remaining[row*columns:(row+1)*columns]
                        desired.append(max(block(i['title'],col_w,theme['heading_font'],13)[1]+block(i['summary'],col_w,theme['body_font'],theme['body_size'])[1]+75+min(30,i.get('editorial_score',0)*20) for i in pair))
                    floor=min(105,cell_h)
                    spare=max(0,top-bottom-floor*rows)
                    weights=[max(1,value-floor) for value in desired]
                    heights=[floor+spare*value/sum(weights) for value in weights]
                if newspaper and columns == 2:
                    middle = margin+col_w+gap/2
                    c.setStrokeColor(HexColor(accent))
                    c.setLineWidth(.4)
                    c.line(middle, bottom+11, middle, top)
                for index, item in enumerate(remaining):
                    row, column = divmod(index, columns)
                    article(item, number, margin+column*(col_w+gap), top-sum(heights[:row]), col_w, heights[row])
                    number += 1
            if puzzle or concept:
                rule(164, .6)
            if concept:
                label=text['daily_concept']
                draw_text(c,label+' / '+concept['title'],margin,155,usable*.57,'NewsBold',9)
                concept_p,concept_h,_=fit_text(concept['text'],usable*.57,60,'News',9)
                concept_p.drawOn(c,margin,138-concept_h)
            if puzzle:
                px=margin+usable*.65 if concept else margin
                pw=usable*.35 if concept else usable
                if puzzle['type']=='anagram':
                    draw_text(c,text['anagram'],px,155,pw,'NewsBold',9)
                    draw_text(c,puzzle['letters'],px,135,pw,'News',10)
                    draw_text(c,text['anagram_hint'],px,110,pw,'News',8)
                else:
                    draw_text(c,text['sudoku'],px,155,pw,'NewsBold',9)
                    gx, gy, cell = width-margin-70, 83, 14
                    c.setStrokeColor(HexColor("#111111"))
                    for i in range(5):
                        c.setLineWidth(.9 if i%2==0 else .3)
                        c.line(gx+i*cell, gy, gx+i*cell, gy+4*cell)
                        c.line(gx, gy+i*cell, gx+4*cell, gy+i*cell)
                    c.setFont("News", 10)
                    for row in range(4):
                        for col in range(4):
                            if puzzle["grid"][row][col]:
                                c.drawCentredString(gx+(col+.5)*cell, gy+(3-row)*cell+3, str(puzzle["grid"][row][col]))
            rule(68, .8)
            footer = text["ai_footer"] if config["summary"]["provider"] != "publisher" else text["footer"]
            footer = branding["footer"] or footer
            draw_text(c, footer, margin, 59, usable, "News" if newspaper else "UI", 7.3, "#444444")
            footer_meta = f'{text["count"].format(count=len(items))} / {now:%H:%M} / {config["timezone"]}'
            if extra.get('issue_id'): footer_meta=extra['issue_id']+' / '+footer_meta
            if extra.get('preview'): footer_meta=text['preview']+' / '+footer_meta
            if extra.get('estimated_reading_minutes'): footer_meta+=' / '+text['reading_time'].format(minutes=extra['estimated_reading_minutes'])
            footer_meta += " / " + text["page"].format(page=page_number, pages=len(pages))
            if warnings:
                footer_meta += " / " + text["warnings"].format(count=len(warnings))
            draw_text(c, footer_meta, margin, 44, usable, "News" if newspaper else "UI", 7.3, "#444444")
            c.showPage()
        c.save()
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
