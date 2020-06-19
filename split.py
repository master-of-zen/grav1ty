import os
from util import get_frames, ffmpeg
from mkv_keyframes import get_mkv_keyframes
from aom_keyframes import get_aom_keyframes

# returns splits, total frames, segments
# splits are contained like so:
# {
#   "00000": {                # aom segment
#     "segment": "00000.mkv", # split segment
#     "start": 0,             # starting frame within the split segment
#     "frames": 5             # number of frames for the aom segment
#   }
# }
# segments are contained like so:
# {
#   "00000.mkv": {
#     "start": 0,
#     "length": 10
#   }
# }
def split(video, path_split, min_frames=-1, max_frames=-1, cb=None):
  mkv_keyframes, total_frames = get_mkv_keyframes(video)
  mkv_keyframes.append(total_frames)
  
  skip_keyframes = 0

  aom_keyframes = get_aom_keyframes(video)

  if min_frames != -1 and max_frames != -1:
    final_scenes = []
    last_scene = aom_keyframes[skip_keyframes]
    previous_scene = aom_keyframes[skip_keyframes]
    for scene in aom_keyframes[skip_keyframes + 1:]:
      if scene - last_scene >= max_frames and previous_scene - last_scene > min_frames:
        final_scenes.append(previous_scene)
        last_scene = previous_scene
      previous_scene = scene
    aom_keyframes = aom_keyframes[:skip_keyframes + 1] + final_scenes
  
  aom_keyframes.append(total_frames)
  
  cmd = [
    "ffmpeg", "-y",
    "-i", video,
    "-map", "0:v:0",
    "-avoid_negative_ts", "1"
  ]

  frames, splits, segments = partition_with_mkv(aom_keyframes, mkv_keyframes, total_frames)
  reencode = False
  if len(frames) < len(aom_keyframes) / 2:
    splits = {}
    frames = []
    segments = {}

    print("keyframes unreliable, re-encoding")

    for i in range(len(aom_keyframes) - 1):
      frame = aom_keyframes[i]
      next_frame = aom_keyframes[i+1]
      segment_n = len(frames)
      length = next_frame - frame
      frames.append(frame)
      splits[f"{len(splits):05d}"] = ({
        "segment": f"{segment_n:05d}.mkv",
        "start": 0,
        "frames": length,
        "filesize": 0
      })
      segments[f"{segment_n:05d}.mkv"] = {
        "start": frame,
        "length": length
      }

    reencode = True

  frames = [str(f) for f in frames][1:]
  print(frames)

  if reencode:
    cmd.extend([
      "-c:v", "libx264",
      "-crf", "0",
      "-x264-params", "scenecut=0",
      "-force_key_frames", "expr:" + "+".join([f"eq(n,{int(f)})" for f in frames])
    ])
  else:
    cmd.extend(["-c", "copy"])

  cmd.extend([
    "-f", "segment",
    "-segment_frames", ",".join(frames)
  ])

  cmd.append(os.path.join(path_split, "%05d.mkv"))

  os.makedirs(path_split, exist_ok=True)
  ffmpeg(cmd, lambda x: cb(x, total_frames))

  return splits, total_frames, segments

def partition_with_mkv(aom_keyframes, mkv_keyframes, total_frames):
  splits = {}
  last_end = 0
  frames = []
  segments = {}

  for i in range(len(aom_keyframes) - 1):
    frame = aom_keyframes[i]
    next_frame = aom_keyframes[i+1]
    segment_n = len(frames)
    start = 0
    length = next_frame - frame
    if frame in mkv_keyframes:
      frames.append(frame)
    else:
      largest = 0
      for j in mkv_keyframes:
        if j < frame:
          largest = j
        else:
          break
      start = frame - largest
      if largest in frames or largest < last_end:
        segment_n -= 1
        start = frame - frames[len(frames)-1]
      else:
        frames.append(largest)
    
    splits[f"{len(splits):05d}"] = ({"segment": f"{segment_n:05d}.mkv", "start": start, "frames": length, "filesize": 0})
    last_end = frame + length
  
  for segment_n in range(len(frames)):
    segments[f"{segment_n:05d}.mkv"] = {
      "start": frames[segment_n],
      "length": (total_frames if segment_n == len(frames) - 1 else frames[segment_n + 1]) - frames[segment_n]
    }

  return frames, splits, segments

def correct_split(path_in, path_out, start, length):
  cmd = [
    "ffmpeg", "-hide_banner",
    "-i", path_in,
    "-map", "0:v:0",
    "-c:v", "libx264",
    "-crf", "0",
    "-force_key_frames", f"expr:eq(n,{start})",
    "-x264-params", "scenecut=0",
    "-vf", f"select=gte(n\\,{start})",
    "-frames:v", str(length),
    "-y", path_out
  ]
  ffmpeg(cmd, lambda x: print(f"{x}/{length}", end="\r"))

# input the source and segments produced by split()
def verify_split(path_in, path_split, segments, cb=None):
  for i, segment in enumerate(segments, start=1):
    print(segment)
    path_segment = os.path.join(path_split, segment)
    segment_n = str(os.path.splitext(segment)[0])
    num_frames = get_frames(path_segment)

    if num_frames != segments[segment]["length"]:
      print("bad framecount", segment, "expected:", segments[segment]["length"], "got:", num_frames)
      correct_split(path_in, path_segment, segments[segment]["start"], segments[segment]["length"])
    else:
      num_frames_slow = get_frames(path_segment, False)
      if num_frames != num_frames_slow:
        print("bad framecount", segment, "expected:", num_frames, "got:", num_frames_slow)
        correct_split(path_in, path_segment, segments[segment]["start"], segments[segment]["length"])
    
    if cb: cb(i)

# this is an example program
if __name__ == "__main__":
  import argparse, json

  parser = argparse.ArgumentParser()
  parser.add_argument("-i", dest="input", required=True)
  parser.add_argument("-o", dest="split_path", required=True)
  parser.add_argument("-s", "--splits", dest="splits", required=True)
  parser.add_argument("--min_frames", default=-1)
  parser.add_argument("--max_frames", default=-1)
  
  args = parser.parse_args()

  splits, total_frames, segments = split(
    args.input,
    args.split_path,
    min_frames=args.min_frames,
    max_frames=args.max_frames,
    cb=lambda x: print(f"{x}/{total_frames}", end="\r")
  )

  print(total_frames, "frames")
  print("verifying split")

  verify_split(
    args.input,
    args.split_path,
    segments,
    cb=lambda x: print(f"{x}/{len(segments)}", end="\r")
  )

  json.dump(splits, open(args.splits, "w+"))