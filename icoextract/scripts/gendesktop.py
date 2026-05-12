#!/usr/bin/env python3

import argparse
import logging
import sys
import os
import glob
import stat
import hashlib

import subprocess

from PIL import Image
from icoextract import IconExtractor, logger, __version__

def get_mime_type(filename):
    result = subprocess.run(["xdg-mime", "query", "filetype", filename], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def get_mime_type_magic(filename):
    result = subprocess.run(["file", "--mime-type", "--brief", filename], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def main():
    """
    Generates a desktop shortcut (.desktop) for an .exe file to the user desktop,
    for DOS exes with no icons, using a proper icon detected from the file's directory.
    The icon will be scaled and generated as a png to the file's directory.
    If no icons detected for the DOS exe, a builtin MSDOS icon will be used.

    inputexe: the input file path (%i)
    size: determines the thumbnail output size (%s)
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-V", "--version", action='version', version=f'exe-shorcut, part of icoextract {__version__}')
    parser.add_argument("-s", "--size", type=int, help="size of icon, if generated", default=256)
    parser.add_argument("-d", "--desktop", action="store_true", help="create shortcut in desktop instead of current directory")
    parser.add_argument("-v", "--verbose", action="store_true", help="enables debug logging")
    parser.add_argument("inputexe", help="input file name (.exe)")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    mime_type = get_mime_type(args.inputexe)

    exe_mimes = [
        "application/vnd.microsoft.portable-executable",
        "application/x-msdownload",
        "application/x-dosexec",
        "application/x-ms-dos-executable",
        "application/x-wine-extension-exe",
        "application/x-ms-ne-executable", # not supported, but may have an external icon. TODO: https://github.com/npjg/nefile
    ]

    pene_mimes = [
        "application/vnd.microsoft.portable-executable",
        "application/x-ms-ne-executable",
    ]

    icon_mimes = ["image/vnd.microsoft.icon", "image/x-icon", "image/ico", "image/icon", "application/ico"]   

    if mime_type is None or mime_type not in exe_mimes:
        logger.debug("invalid input file.")
        sys.exit(1)

    iconfile = None
    inputfile = os.path.abspath(args.inputexe)
    base = os.path.splitext(os.path.basename(inputfile))[0]
    dir = os.path.dirname(inputfile)
    parent1st = os.path.basename(dir).replace(" ", "_")
    user = os.environ["USER"]

    # a exe may have an ico with the same base name in the same directory
    # if it exist and is a valid ICO, then use it in the .desktop
    # some icos cannot scale properly, to get better effect, scale and save it as PNG to ~/.icons
    # I don't think there are files name as *.iCO or *.IcO or something like that.
    icon_files = glob.glob(os.path.join(dir,"*.ico")) + glob.glob(os.path.join(dir, "*.ICO")) + glob.glob(os.path.join(dir, "*.Ico"))

    # pass 1: base name match, case insensitive
    for ico in icon_files:
        # check magic since some DOS program data file has ICO extension, but not an icon
        mime_icon = get_mime_type_magic(ico)
        if mime_icon is not None and mime_icon in icon_mimes:
            if os.path.splitext(os.path.basename(ico))[0].lower() == base.lower():
                iconfile = ico
                break

    # pass 2: use the first valid one
    for ico in icon_files:
        mime_icon = get_mime_type_magic(ico)
        if mime_icon is not None and mime_icon in icon_mimes:
            iconfile = ico
            break

    logger.debug("find valid icon file: %s", iconfile)

    if iconfile is not None:
        im = Image.open(iconfile)
        im = im.resize((args.size, args.size))
        outicon = os.path.join(dir, base + "_icoextract.png") # add sufix to avoid overwritten if that file already existed in program data
        logger.debug("generating scaled ico file: %s", outicon)
        im.save(outicon, "PNG")
        iconfile = outicon
    elif get_mime_type_magic(args.inputexe) not in pene_mimes:
        # use a general icon for it, "MSDOS"
        iconfile = "/usr/share/icons/icoextract/application-x-ms-dos-executable.svg"

    outfile = ""
    if args.desktop:
        # keep path in case some exe with the same name, i.e. multiple GAME.EXE from different games,
        # the icon names will conflict as GAME.desktop.
        # use 1 level parent is usually enough
        try:
            outfile = os.path.join(os.environ["XDG_DESKTOP_DIR"], parent1st + "_" + base + ".desktop")
        except KeyError:
            outfile = "/home/" + user +"/Desktop/" + parent1st + "_" + base + ".desktop"
    else:
        outfile = os.path.join(dir, parent1st + "_" + base + ".desktop")

    #print(outfile)

    content = f'[Desktop Entry]\n\
Version=1.0\n\
Type=Application\n\
Name={base}\n\
Comment=\n\
Exec="{inputfile}"\n\
Icon={iconfile}\n\
Path={dir}\n\
Terminal=false\n\
StartupNotify=false\n'

    with open(outfile, "w") as f:

        f.write(content)
        f.close()

        #chmod +x
        st = os.stat(outfile)
        os.chmod(outfile, st.st_mode | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

        #mark as secure and trusted
        desktop_env = os.environ.get('XDG_CURRENT_DESKTOP').lower()
        if "xfce" in desktop_env:
            sha256 = hashlib.sha256()
            sha256.update(content.encode('utf-8'))

            logger.debug("set %s metadata::xfce-exe-checksum %s", outfile, sha256.hexdigest())

            # gio set -t string <file> metadata::xfce-exe-checksum <sha256>
            ret = subprocess.run(
                ['gio', 'set', "-t", "string", outfile, 'metadata::xfce-exe-checksum', sha256.hexdigest()],
                check=True,
                capture_output=True,
                text=True
            )
            logger.debug("done" if ret.returncode ==0 else "failed")
        else:
            logger.debug("set %s metadata::trusted yes", outfile)
            # gio set <file> metadata::trusted yes
            ret = subprocess.run(
                ['gio', 'set', outfile, 'metadata::trusted', 'yes'],
                check=True,
                capture_output=True,
                text=True
            )
            logger.debug("done" if ret.returncode ==0 else "failed")