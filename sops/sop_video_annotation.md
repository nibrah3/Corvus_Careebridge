# SOP: Video Annotation Assessment

**Purpose:** Complete a video annotation task using Gemini Files API to analyze the actual source video — not a screen recording.

---

## Steps

### 1. Extract task requirements

From the DOM (via `mcp__dom__*` or `mcp__cdp__cdp_get_axtree()`), read:
- What type of annotation is requested (object detection, activity recognition, scene classification, sentiment, etc.)
- The output format required (bounding boxes, timeline segments, labels, JSON schema if shown)
- Any specific instructions (frame intervals, confidence threshold, label taxonomy)

Write these requirements down before touching the video.

### 2. Identify the video source

Look in the DOM for:
- A direct download link (`<a href="...mp4">`, `<a href="...webm">`)
- A `<video>` tag with `src` attribute
- An iframe with a known video platform (Vimeo, custom player)
- A "Download" or "Source" button

**Priority:** direct download URL > video src > iframe src

If no downloadable source exists: use `mcp__capture__start_video_capture(fps=5)`, play the video via humanizer_mcp, then `mcp__capture__stop_video_capture(output_path)`. Note: this is the screen recording fallback — prefer the source file.

### 3. Obtain the video file

If download URL found:
- Download to `C:\tmp\annotation_video_{timestamp}.mp4` using a Bash tool call.
- Verify file size > 0.

If using screen recording:
- Call `mcp__gemini__start_recording(fps=5)`.
- Click Play on the video player via `mcp__humanizer__*`.
- Wait for video to finish.
- Call `mcp__gemini__stop_recording(output_path="C:/tmp/annotation_video_{timestamp}.mp4")`.

### 4. Upload to Gemini Files API

Call `mcp__gemini__upload_video(video_path)`.

Wait for `state == "ACTIVE"` in the response (the tool polls automatically).

If upload fails: report error, stop. Do NOT fall back to screenshot annotation — screenshots cannot represent a video for annotation.

Save the returned `uri` and `name` — you need both.

### 5. Request annotation from Gemini

Call `mcp__gemini__analyse_video(file_uri, prompt)`.

Construct the prompt from the task requirements gathered in Step 1. Be specific:
- Include the label taxonomy if provided.
- Specify the output format (JSON, timestamps, bounding box coordinates).
- Ask for confidence scores.

Example prompt structure:
```
Analyze this video and perform [annotation_type].
Labels to use: [taxonomy].
For each [segment/frame/object], provide:
- timestamp_start, timestamp_end (seconds)
- label from the taxonomy
- confidence (0.0-1.0)
- [any other required fields]
Return as JSON array.
```

### 6. Map output to platform format

Take Gemini's structured response and reformat it to match what the platform expects.

Common formats:
- **COCO**: `{ image_id, annotations: [{ bbox: [x,y,w,h], category_id, score }] }`
- **YOLO**: `class_id cx cy w h` per line (normalized 0-1)
- **Custom JSON**: follow the schema shown on the task page
- **Timeline segments**: `[{ start: N, end: N, label: "X" }]`

If confidence < 0.80 for any segment: flag it and route the entire answer to supervised gate.

### 7. Submit the annotation

Enter the formatted annotation into the submission field via `mcp__humanizer__*`.
Click Submit via `mcp__humanizer__*`.

### 8. Cleanup

Call `mcp__gemini__delete_file(file_name)` to free Gemini quota.
Delete the local temp video file.

### 9. Gate rule

If ANY segment has confidence < 0.80, or if the platform format was unclear: go to supervised gate before submitting. Do not auto-submit uncertain annotations.

## On Error

For tunnel/MCP/Gemini failures: see sops/sop_on_error_shared.md.

**Video annotation specific:**
- Video download fails → try alternate URL from DOM (iframe src, video tag src)
- Gemini upload timeout → retry once with 60s timeout
- Gemini returns empty annotation → gate the answer; do not auto-submit
- Screen recording fallback used → fallback mode active; all answers gated
