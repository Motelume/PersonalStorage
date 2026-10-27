"""Reproducible Windows executable build; never collects library data or secrets."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def collect_ocr_notices():
    from importlib.metadata import distribution
    bundle=ROOT/'dist/PersonalStorage';target=bundle/'THIRD_PARTY_NOTICES'
    if not bundle.is_dir() or not target.resolve().is_relative_to(bundle.resolve()):raise ValueError('Invalid distribution directory')
    target.mkdir(exist_ok=True);lines=['# OCR dependency notices','', 'System fonts are used locally and are not redistributed.','']
    packages=('rapidocr','onnxruntime','pypdfium2','opencv-python','numpy','shapely','pyclipper','Pillow','pypdf','PyYAML','omegaconf','requests','tqdm','protobuf','flatbuffers','olefile','xlrd','striprtf')
    apache=None
    for name in packages:
        package=distribution(name);folder=target/name;folder.mkdir(exist_ok=True)
        lines.append(f'## {name} {package.version}')
        for index,relative in enumerate(package.files or []):
            if not any(word in str(relative).lower() for word in ('license','notice','copying')):continue
            source=package.locate_file(relative)
            if not source.is_file():continue
            destination=folder/(str(index)+'-'+Path(relative).name);shutil.copy2(source,destination)
            lines.append(f'- {relative}: {name}/{destination.name}')
            if str(relative).replace('\\','/').endswith('LICENSES/Apache-2.0.txt'):apache=source
        lines.append('')
    if apache is None:raise ValueError('Apache license text unavailable')
    shutil.copy2(apache,target/'RapidOCR-and-PaddleOCR-Apache-2.0.txt')
    lines+=['## OCR model attribution','RapidOCR source: https://github.com/RapidAI/RapidOCR (Apache-2.0).',
            'PP-OCRv4 detection/recognition and text-angle classifier weights originate from PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR .',
            'The unchanged ONNX conversions are taken from the pinned rapidocr 3.4.2 wheel. Model sources/hashes are in rapidocr/default_models.yaml; application pins are in personalstorage/ocr.py.',
            'Upstream model rights remain with Baidu/PaddleOCR contributors; conversion/engineering rights remain with RapidOCR contributors. No endorsement is implied. See the included Apache-2.0 text.']
    (target/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    from release_gate import validate
    validate()
    if os.name!='nt':raise SystemExit('Build the desktop app on Windows')
    sys.path.insert(0,str(ROOT))
    from personalstorage import ocr
    if not ocr.availability()['ready']:raise SystemExit('Install requirements-ocr.txt and verify bundled models before building')
    from PIL import Image, ImageDraw, ImageFont
    asset=ROOT/'.local/build-assets';asset.mkdir(parents=True,exist_ok=True)
    icon=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(icon)
    draw.rounded_rectangle((5,5,251,251),radius=55,fill='#3569dc')
    try:font=ImageFont.truetype('segoeuib.ttf',172)
    except OSError:font=ImageFont.load_default(size=172)
    draw.text((128,118),'P',font=font,fill='white',anchor='mm')
    icon_path=asset/'app.ico';icon.save(icon_path,sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
        '--name','PersonalStorage','--icon',str(icon_path),'--paths',str(ROOT),
        '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'), '--specpath',str(ROOT/'build'),
        '--add-data',str(ROOT/'personalstorage/web_static')+';personalstorage/web_static',
        '--add-data',str(ROOT/'personalstorage/templates')+';personalstorage/templates',
        '--collect-data','webview','--collect-binaries','webview',
        '--collect-data','rapidocr','--collect-binaries','onnxruntime','--collect-data','pypdfium2','--collect-data','pypdfium2_raw','--collect-binaries','pypdfium2_raw',
        '--copy-metadata','rapidocr','--copy-metadata','onnxruntime','--copy-metadata','pypdfium2','--copy-metadata','opencv-python',
        '--hidden-import','rapidocr.inference_engine.onnxruntime','--hidden-import','onnxruntime','--hidden-import','pypdfium2',
        '--exclude-module','torch','--exclude-module','paddle','--exclude-module','openvino','--exclude-module','matplotlib',
        '--exclude-module','PyQt5','--exclude-module','PyQt6','--exclude-module','PySide2','--exclude-module','PySide6',
        str(ROOT/'tools/desktop_entry.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    collect_ocr_notices()
    print(ROOT/'dist/PersonalStorage/PersonalStorage.exe')


if __name__=='__main__':main()
