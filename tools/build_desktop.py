"""Reproducible Windows executable build; never collects library data or secrets."""
from pathlib import Path
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.name!='nt':raise SystemExit('Build the desktop app on Windows')
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
        '--exclude-module','PyQt5','--exclude-module','PyQt6','--exclude-module','PySide2','--exclude-module','PySide6',
        str(ROOT/'tools/desktop_entry.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    print(ROOT/'dist/PersonalStorage/PersonalStorage.exe')


if __name__=='__main__':main()
