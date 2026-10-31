"""CUPS printing is opt-in; manual duplex uses separately generated pass files."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
import os
import shutil
import tempfile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject
from pypdf.errors import PyPdfError


def preflight(path,config):
    """Exercise CUPS's real PDF path and estimate raster coverage without a job."""
    from PIL import Image
    import io
    check_pdf(path)
    original=PdfReader(path)
    report=dict(pages=len(original.pages),sheets=math.ceil(len(original.pages)/(1 if config['print']['duplex']=='off' else 2))*config['print']['copies'],filter_tested=False,dark_pixel_fraction=None,page_coverage=[],heaviest_region=None)
    filter_path='/usr/lib/cups/filter/pdftopdf'
    if not Path(filter_path).exists(): filter_path=shutil.which('pdftopdf')
    with tempfile.TemporaryDirectory(prefix='ulak-preflight-') as directory:
        filtered=Path(path)
        if filter_path:
            filtered=Path(directory)/'filtered.pdf'
            env=dict(os.environ);env.pop('PPD',None)
            with filtered.open('wb') as output:
                result=subprocess.run([filter_path,'999','ulak','Ulak preflight','1','media='+config['layout']['paper_size'],str(Path(path).resolve())],stdout=output,stderr=subprocess.PIPE,env=env,timeout=60,check=True)
            if b'Unsupported compound' in result.stderr or b'ERROR:' in result.stderr:
                raise ValueError('CUPS preflight reported a PDF processing error; job not submitted')
            processed=PdfReader(filtered)
            if len(processed.pages)!=len(original.pages): raise ValueError('CUPS preflight changed page count')
            for before,after in zip(original.pages,processed.pages):
                if len((before.extract_text() or '').strip())>20 and len((after.extract_text() or '').strip())<20:
                    raise ValueError('CUPS preflight lost page text; job not submitted')
            report['filter_tested']=True
        if shutil.which('pdftoppm'):
            dark=total=0
            for page in range(1,len(original.pages)+1):
                result=subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-scale-to','500','-gray','-singlefile','-png',str(filtered)],capture_output=True,timeout=30,check=True)
                with Image.open(io.BytesIO(result.stdout)) as image:
                    image=image.convert('L');histogram=image.histogram()
                    w,h=image.size
                    for label,box in [('top-left',(0,0,w//2,h//2)),('top-right',(w//2,0,w,h//2)),('bottom-left',(0,h//2,w//2,h)),('bottom-right',(w//2,h//2,w,h))]:
                        region=image.crop(box).histogram()
                        amount=sum(region[:220])/max(1,sum(region))
                        if report['heaviest_region'] is None or amount>report['heaviest_region']['coverage']:
                            report['heaviest_region']=dict(page=page,region=label,coverage=round(amount,4))
                pixels=sum(histogram);coverage=sum(histogram[:220])/max(1,pixels)
                if coverage<.0002: raise ValueError(f'Preflight detected blank page {page}; job not submitted')
                report['page_coverage'].append(round(coverage,4))
                dark+=sum(histogram[:220]);total+=pixels
            report['dark_pixel_fraction']=round(dark/max(1,total),4)
    return report


def validate_capabilities(command):
    sides=next((part for part in command if part.startswith('sides=')),'sides=one-sided')
    printer=command[command.index('-d')+1] if '-d' in command else None
    args=['lpoptions']+(['-p',printer] if printer else [])+['-l']
    result=subprocess.run(args,capture_output=True,text=True,timeout=8,check=True)
    supported=result.stdout.casefold()
    if sides!='sides=one-sided':
        desired='duplexnotumble' if sides.endswith('long-edge') else 'duplextumble'
        if sides.split('=',1)[1] not in supported and desired not in supported:
            raise ValueError('Printer does not advertise the requested automatic duplex mode; select manual duplex or one-sided printing')
    media=next((part.split('=',1)[1] for part in command if part.startswith('media=')),None)
    page_sizes=next((line for line in result.stdout.splitlines() if line.startswith('PageSize/')),None)
    if media and page_sizes and media not in page_sizes.split(':',1)[-1].replace('*','').split():
        raise ValueError('Printer does not advertise the requested paper size')
    color=next((line for line in result.stdout.splitlines() if line.startswith('ColorModel/')),None)
    if 'print-color-mode=color' in command and color:
        choices=color.split(':',1)[-1].casefold().replace('*','')
        if not any(token in choices for token in ('rgb','cmyk')) and 'color' not in choices.split():
            raise ValueError('Printer does not advertise color printing')


def check_pdf(path):
    """Reject compound streams before affected PDFio CUPS filters silently blank them."""
    try:
        reader = PdfReader(path)
        if not reader.pages: raise ValueError('PDF has no pages; job not submitted')
    except PyPdfError as exc:
        raise ValueError('Cannot read PDF; job not submitted') from exc
    seen = set()

    def stream(value):
        value = value.get_object()
        if isinstance(value, ArrayObject):
            for child in value:
                stream(child)
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        filters = value.get("/Filter")
        if isinstance(filters, ArrayObject) and len(filters) > 1:
            raise ValueError("PDF uses compound stream filters unsupported by some CUPS versions; regenerate the edition with current Ulak before printing")
        resources(value.get("/Resources", {}))

    def resources(value):
        value = value.get_object() if hasattr(value, 'get_object') else value
        objects = value.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, 'get_object') else objects
        for obj in objects.values():
            stream(obj)

    for page in reader.pages:
        if page.get("/Contents") is not None:
            stream(page["/Contents"])
        resources(page.get("/Resources", {}))


def duplex_files(path, reverse_backs=False):
    """Keep one back per sheet, including a blank back for an odd final page."""
    path = Path(path)
    reader = PdfReader(path)
    fronts, backs = PdfWriter(), PdfWriter()
    sheets = math.ceil(len(reader.pages)/2)
    for sheet in range(sheets):
        fronts.add_page(reader.pages[2*sheet])
    for sheet in (reversed(range(sheets)) if reverse_backs else range(sheets)):
        index = 2*sheet+1
        if index < len(reader.pages):
            backs.add_page(reader.pages[index])
        else:
            page = reader.pages[-1]
            backs.add_blank_page(width=page.mediabox.width, height=page.mediabox.height)
    front_path = path.with_name(path.stem+".fronts.pdf")
    back_path = path.with_name(path.stem+".backs.pdf")
    for writer, target in ((fronts, front_path), (backs, back_path)):
        with target.open("wb") as output:
            writer.write(output)
    return front_path, back_path


def select_printer(settings, dry_run=False):
    candidates = [settings["printer"]] if settings["printer"] else settings["printers"]
    if not candidates:
        return ""  # Let CUPS use its configured default.
    if dry_run:
        return candidates[0]
    result = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, check=True, timeout=8)
    available = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    for printer in candidates:
        if printer in available:
            return printer
    raise ValueError("None of the preferred printers is available in CUPS")


def print_command(path, config, printing_pass=None, dry_run=False):
    settings = config["print"]
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"PDF does not exist: {path}")
    check_pdf(path)
    if settings["duplex"] == "manual":
        if printing_pass not in ("front", "back"):
            raise ValueError("Manual duplex requires --print-pass front or back; reload paper between passes")
        # Splitting an existing immutable edition avoids regenerating different news between passes.
        if settings["copies"] != 1:
            raise ValueError("Manual duplex supports one copy per pass; repeat both passes for additional copies")
        fronts, backs = duplex_files(path, settings["reverse_backs"])
        path = fronts if printing_pass == "front" else backs
        sides = "one-sided"
    else:
        if printing_pass:
            raise ValueError("--print-pass is only valid for print.duplex=manual")
        sides = {"off": "one-sided", "long-edge": "two-sided-long-edge", "short-edge": "two-sided-short-edge"}[settings["duplex"]]
    command = ["lp"]
    printer = select_printer(settings, dry_run)
    if printer:
        command += ["-d", printer]
    quality = settings["quality"]
    color = "monochrome" if quality == "efficient" else settings["color"]
    command += ["-n", str(settings["copies"]), "-o", "media="+config["layout"]["paper_size"],
                "-o", "sides="+sides, "-o", "print-color-mode="+color,
                "-o", "print-quality="+str({"draft":3,"efficient":3,"normal":4,"high":5}[quality]),
                "-o", "collate=true",
                "-o", "print-scaling=none", "--", str(path)]
    return command


def submit(command):
    # No shell interpolation; the user explicitly invokes --print.
    result=subprocess.run(command, check=True, capture_output=True,text=True)
    if result.stdout: print(result.stdout.strip())
    return result.stdout
