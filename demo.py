#!/usr/bin/env python3

import glob
import json
import os
import shutil
import sys

from pathlib import Path

from PIL import Image
from kaitaistruct import KaitaiStruct
from kanimtool.parser import Build, Symbol, SymbolFrame, AnimGroup, Animation, AnimFrame, FrameElement
from kanimtool.builder import BuildRegistry, AnimationBuilder

from anim import Anim
from bild import Bild

# demo script to test parsing and rendering of Klei's animation zip
USAGE = f"Usage: {__file__} klei-animation.zip"
ZIP_FILE = sys.argv[1] if len(sys.argv) > 1 else None
if not ZIP_FILE or not os.path.isfile(ZIP_FILE):
    print(USAGE)
    exit(1)

# cleanup and prepare output directories, then unpack the zip file
anim_basename = Path(ZIP_FILE).stem
out_dir = "output"
anim_dir = os.path.join(out_dir, anim_basename)
frames_dir = os.path.join(anim_dir, "frames")
shutil.rmtree(anim_dir, ignore_errors=True)
os.makedirs(frames_dir, exist_ok=True)
shutil.unpack_archive(ZIP_FILE, anim_dir)

# convert .tex to .png
for path in glob.glob(os.path.join(anim_dir, "*.tex")):
    # read the .tex file and remove the first 9 bytes (KTEX header) and save it as a DDS file
    with open(path, "rb") as f:
        data = f.read()[9:]
    dds_path = os.path.join(anim_dir, os.path.basename(path).replace(".tex", ".dds"))
    with open(dds_path, "wb") as f:
        f.write(data)

    im = Image.open(dds_path)
    im.load()
    png_path = os.path.join(anim_dir, os.path.basename(path).replace(".tex", ".png"))
    im.save(png_path)

# prepare to parse animation and build files
all_strings = {}
all_images = []
for path in glob.glob(os.path.join(anim_dir, "atlas-*.png")):
    all_images.append(path)


# functions for parsing the build file

def compute_uvbox(vb_start_index, num_verts, build):
    vertices = build.vertices[vb_start_index:vb_start_index+num_verts]
    u1 = min(it.u for it in vertices)
    v1 = min(it.v for it in vertices)
    u2 = max(it.u for it in vertices)
    v2 = max(it.v for it in vertices)
    return (u1, v1, u2, v2)

def parse_symbol_frame(frame, symbol, build):
    seq_idx = frame.frame_num
    duration = frame.duration
    build_image_idx = 0
    # In Klei v6 build format, (frame.x, frame.y, frame.w, frame.h) is the logical
    # trim box, but the actual texture region in the atlas extends ~30 game units
    # beyond the trim box on each side (30-unit bleed margin). The vertex XY positions
    # correctly capture the full game-unit extent of the sprite including this margin.
    vertices = build.vertices[frame.vb_start_index:frame.vb_start_index + frame.num_verts]
    xs = [v.x for v in vertices]
    ys = [v.y for v in vertices]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    bounds = (cx, cy, w, h)
    uvbox = compute_uvbox(frame.vb_start_index, frame.num_verts, build)
    return SymbolFrame(
        seq_idx=seq_idx,
        duration=duration,
        build_image_idx=build_image_idx,
        bounds=bounds,
        uvbox=uvbox,
    )

def parse_symbol(symbol, build):
    hash = symbol.symbol_hash
    path_id = 0 # hack
    flags = 0 # hack
    frames = [parse_symbol_frame(it, symbol, build) for it in symbol.frames]
    return Symbol(
        hash=hash,
        path_id=path_id,
        flags=flags,
        frames=frames,
    )

def parse_build(build):
    strings = {it.hash: it.original_string for it in build.hashed_strings}
    all_strings.update(strings)

    name = build.build_name
    symbols = [parse_symbol(it, build) for it in build.symbols]
    return Build(
        name=name,
        symbols=symbols,
        strings=strings,
    )

# functions for parsing the animation file

def calculate_transform(mat):
    return (mat.sa, mat.sc, mat.stx, mat.sb, mat.sd, mat.sty)

def parse_frame_element(elem):
    symbol = elem.symbol_hash
    frame = elem.symbol_frame
    mult_alpha = (1.0, 1.0, 1.0, 1.0) # hack
    transform = calculate_transform(elem.mat)
    return FrameElement(
        symbol=symbol,
        frame=frame,
        mult_alpha=mult_alpha,
        transform=transform,
    )

def parse_animation_frame(frame):
    bbox = (frame.x, frame.y, frame.w, frame.h)
    elements = [parse_frame_element(it) for it in frame.elements]
    return AnimFrame(
        bbox=bbox,
        elements=elements,
    )

def parse_animation(animation):
    name = animation.name
    frame_rate = animation.frame_rate
    frames = [parse_animation_frame(it) for it in animation.frames]
    return Animation(
        name=name,
        frame_rate=frame_rate,
        frames=frames,
    )

def parse_anim(anim):
    strings = {it.hash: it.original_string for it in anim.hashed_strings}
    all_strings.update(strings)

    animations = [parse_animation(it) for it in anim.anims]
    return AnimGroup(
        animations=animations,
        strings=strings,
    )

def kaitai_struct_to_json(obj):
    def serialize(obj):
        if isinstance(obj, KaitaiStruct):
            return {k: serialize(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
        elif isinstance(obj, list):
            return [serialize(it) for it in obj]
        else:
            return obj
    serialized = serialize(obj)
    del serialized["magic"]
    del serialized["tail"]
    return serialized

# parse the build and animation files, save as JSON files
build_file = os.path.join(anim_dir, "build.bin")
build_data = Bild.from_file(build_file)
build_json = os.path.join(anim_dir, "build.json")
with open(build_json, "w") as f:
    json.dump(kaitai_struct_to_json(build_data), f, indent=2)
build = parse_build(build_data)

anim_file = os.path.join(anim_dir, "anim.bin")
anim_data = Anim.from_file(anim_file)
anim_json = os.path.join(anim_dir, "anim.json")
with open(anim_json, "w") as f:
    json.dump(kaitai_struct_to_json(anim_data), f, indent=2)
anim_group = parse_anim(anim_data)

# create a registry and add the build and animation data, along with the images
registry = BuildRegistry(strings=all_strings)
images = [Image.open(it) for it in all_images]
for im in images:
    im.load()
registry.add_build(build, images)

# configure builder
margins = 0
scale = 1
measure_methods = ("frame",) # or ("element",) / ("frame","element")
resampling = Image.Resampling.BICUBIC
debug_outline = True # turn this off to hide outlines
anim_name = None # or set to a specific animation name to only render that one, e.g. "on"
frame_num = None # or set to a specific frame number to only render that one, e.g. 0

# override configuration here
anim_name = "on"
frame_num = 0

# save frames
for anim in anim_group.animations:
    if anim_name is not None and anim.name != anim_name:
        continue
    frames = anim.frames
    builder = AnimationBuilder(
        registry=registry,
        frames=frames,
        margins=margins,
        scale=scale,
        measure_methods=measure_methods,
        resampling=resampling,
        debug_outline=debug_outline,
    )
    for i in range(len(frames)):
        if frame_num is not None and i != frame_num:
            continue
        im = builder.draw_frame(i)
        im_file = os.path.join(anim_dir, "frames", f"{anim.name}_{i}.png")
        im.save(im_file)
