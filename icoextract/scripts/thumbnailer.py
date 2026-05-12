#!/usr/bin/env python3
"""
Linux (freedesktop.org) thumbnailer for Windows PE files (.exe/.dll)
"""
import argparse
import logging
import sys
import os
import shlex
import struct

#import mimetypes #not working properly, use subprocess to call xdg-mime
import subprocess


from PIL import Image
from configparser import ConfigParser

from icoextract import IconExtractor, logger, __version__

def get_mime_type(filename):
    result = subprocess.run(["xdg-mime", "query", "filetype", filename], capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("xdg-mime query filetype %s failed: %d", filename, result.returncode)
    return result.stdout.strip() if result.returncode == 0 else None
    

def generate_thumbnail_pe(inputfile, outfile, size=256, force_resize=False):
    """
    Generates a thumbnail for an .exe file.

    inputfile: the input file path (%i)
    outfile: output filename (%o)
    size: determines the thumbnail output size (%s)
    """
    try:
        extractor = IconExtractor(inputfile)
    except RuntimeError:
        logger.debug("Failed to extract icon for %s:", inputfile, exc_info=True)
        sys.exit(1)
        
    #get the best size & color depth
    maxs = 0
    maxd = 0
    maxi = 0
    icons = extractor._get_icon()
    for index, (info,dummy) in enumerate(icons):
        dct = info.dump_dict()
        #print(f"{dct}")
        s_ = max(dct["Width"]["Value"], dct["Height"]["Value"])
        d_ = dct["BitCount"]["Value"]
        if maxs <= s_:
            maxs = s_
            if maxd <= d_:
                maxd = d_
                maxi = index

    data = extractor.get_icon1(icons[maxi])
    im = Image.open(data)  # Open up the .ico from memory
    #maxs = max(im.size[0], im.size[1]) #GRPICONDIRENTRY may be incorrect, use real image dimension

    logger.debug("Use icon subimage %d, size:%d bitdepth:%d", maxi, maxs, maxd)

    # there is a bug in Pillow to handle AND mask (transparency) bits for 4bpp icon
    # dont want to touch Pillow code so here is a dirty workaround
    if maxd == 4:
        try:
            logger.warning("fix 4bpp transparency")

            dataoffset = 24 # only works for get_icon1

            # GRPICONDIRENTRY may be incorrect:
            # (some old ICO files are so broken but they are correctly shown on Windows)
            # use real image dimension from BITMAPINFOHEADER
            # or from PIL.Image object which also comes from BITMAPINFOHEADER

            #_w = icons[maxi][0].dump_dict()["Width"]["Value"]
            #_h = icons[maxi][0].dump_dict()["Height"]["Value"]
            #color_count = icons[maxi][0].dump_dict()["ColorCount"]["Value"]
            _w = im.size[0]
            _h = im.size[1]
            # BITMAPINFOHEADER.biClrUsed
            data.seek(32, os.SEEK_SET)
            color_count = struct.unpack('<I', data.read(4))[0]
            color_count = min(16, color_count) # 16 max for 4bpp

            # https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-bitmapinfoheader
            color_count = 16 if color_count == 0 else color_count
            BITMAPINFOHEADER_bytes = 40
            palette_bytes = color_count*4 # 32bit RGB(A, unused)

            cw = _w + int((0 if ((_w*(32/maxd)) % 32) == 0 else 32-((_w*(32/maxd)) % 32)) * maxd / 32) # pitch/stride alignment for colors

            #data.seek(0, os.SEEK_SET)
            #print(_w, _h, cw, dataoffset, palette_bytes, int(cw * _h * maxd / 8), len(data.getvalue()))

            data.seek(dataoffset + BITMAPINFOHEADER_bytes + palette_bytes + int(cw * _h * maxd / 8), os.SEEK_SET)
            mask_data = data.read()

            aw = _w + (0 if (_w % 32) == 0 else 32-(_w % 32)) # pitch/stride alignment for transparency
            mask = Image.frombuffer("1", im.size, mask_data, "raw", ("1;I", int(aw / 8), -1), )
            im.putalpha(mask)
        except:
            logger.warning("fix 4bpp failed")
            raise

    if force_resize:
        logger.debug("Force resizing icon to %dx%d", size, size)
        im = im.resize((size, size))
    else:
        if size > 256:
            logger.warning('Icon sizes over 256x256 are not supported')
            size = 256
        elif size not in (128, 256):
            logger.warning('Unsupported size %d, falling back to 128x128', size)
            size = 128

        # If large size thumbnail wasn't requested but one is available, pick an 128x128 icon if available;
        # otherwise scale down from 256x256 to 128x128. 128x128 is the largest resolution allowed for
        # "normal" size thumbnails.
        if (size, size) in im.info['sizes']:
            logger.debug(f"Using native {size}x{size} icon")
        else:
            logger.debug(f"resizing icon {im.size} to ({size},{size})")
            im = im.resize((size, size))
        logger.debug("Writing normal size thumbnail for %s to %s", inputfile, outfile)

    im.save(outfile, "PNG")

#generate thumbnail for application/x-desktop
def generate_thumbnail_xdesktop(inputfile, outfile, size=256, force_resize=False):
    
    # Disable interpolation to handle '%' characters in Exec key
    config = ConfigParser(interpolation=None)
    try:
        config.read(inputfile)
    except Exception as e:
        return
    if config is None: #TODO: do we need this?
        return

    if 'Desktop Entry' not in config: #invalid file
        return

    target_exec = config['Desktop Entry']['Exec']
    if target_exec is None:
        return #invalid

    #print(f"{target_exec}")

    external_icon = config['Desktop Entry']['Icon']
    if external_icon is not None and external_icon.strip() != "" and os.path.isfile(external_icon.strip()):
        logger.warning("skip generating thumbnail for desktop shorcut, already have a icon: %s", external_icon)
        return #don't do anything if there is a icon been set
    
    nonfmt_exec = target_exec.replace('%u', '').replace('%f', '').replace('%F', '').strip()
    cmd = shlex.split(nonfmt_exec)

    if cmd[0] == "env":
        cmd.pop(0)
        while("=" in cmd[0]):
            cmd.pop(0)
    #print(f"{cmd[0]}")
    
    return generate_thumbnail_pe(cmd[0], outfile, size, force_resize)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-V", "--version", action='version', version=f'exe-thumbnailer, part of icoextract {__version__}')
    parser.add_argument("-s", "--size", type=int, help="size of desired thumbnail", default=256)
    parser.add_argument("-v", "--verbose", action="store_true", help="enables debug logging")
    parser.add_argument("-f", "--force-resize", action="store_true", help="force resize thumbnail to the specified size")
    parser.add_argument("inputfile", help="input file name (.exe/.dll/.mun)")
    parser.add_argument("outfile", help="output file name (.png)")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    mime_type = get_mime_type(args.inputfile)
    #print(f"{args.inputfile}: {mime_type}")

    if mime_type == "application/x-desktop":
        generate_thumbnail_xdesktop(args.inputfile, args.outfile, size=args.size, force_resize=args.force_resize)
    else:
        generate_thumbnail_pe(args.inputfile, args.outfile, size=args.size, force_resize=args.force_resize)
