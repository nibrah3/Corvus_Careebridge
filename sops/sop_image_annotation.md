# SOP: Image Annotation Assessment

**Purpose:** Complete an image annotation task using Gemini Files API to analyze the source image directly.

---

## Steps

### 1. Extract task requirements

From the DOM, read:
- Annotation type: bounding box, polygon, keypoint, classification, segmentation, caption
- Label taxonomy (the exact labels to use — use them verbatim)
- Output format required
- Whether multiple labels per image are allowed
- Any instructions on handling ambiguous or partially visible objects

### 2. Identify the image source

Look in the DOM for:
- `<img>` tag with full `src` URL
- A download link
- An image loaded via background CSS (check computed styles via `cdp_eval` if needed)

Extract the full image URL.

### 3. Download the image

Download to `C:\tmp\annotation_image_{timestamp}.jpg` using a Bash tool call.
Verify file size > 0.

If the image requires authentication (loads only in the browser session):
- Use `mcp__capture__screenshot(purpose="annotation")` to capture the image area with a tight crop region.
- Save the screenshot to disk.
- Use this file for upload.
- Note: this constitutes fallback mode — gate the answer before submitting.

### 4. Upload to Gemini

Call `mcp__gemini__upload_video(video_path)` — this tool works for images too (Gemini Files API accepts images).

Wait for `state == "ACTIVE"`.

If upload fails: report error and stop. Do not screenshot-substitute for annotation.

### 5. Request annotation from Gemini

Call `mcp__gemini__analyse_video(file_uri, prompt)` with the annotation-specific prompt.

Example prompt:
```
Annotate this image with [annotation_type].
Labels to use (use exactly): [taxonomy].
For each detected object/region:
- label (from taxonomy only)
- bounding box: [x_min, y_min, x_max, y_max] in pixel coordinates
- confidence (0.0-1.0)
If no objects match the taxonomy, return an empty array.
Return as JSON.
```

### 6. Map to platform format

Reformat the response to match what the platform's annotation tool expects.
If the platform uses a web annotation tool (e.g. makesense.ai, CVAT, Label Studio):
- Input the bounding box data using the tool's import function if available.
- Otherwise, draw boxes manually via `mcp__humanizer__*` using the coordinates as guidance.

### 7. Submit

Submit via `mcp__humanizer__*`. Click confirm/save/submit.

### 8. Gate rule

If confidence < 0.80 on any annotation, or if the image has poor quality/ambiguous content: go to supervised gate before submitting.

### 9. Cleanup

Delete local temp image file.

## On Error

For tunnel/MCP/Gemini failures: see sops/sop_on_error_shared.md.

**Image annotation specific:**
- Image download blocked (auth required) → screenshot with purpose=annotation; fallback mode active
- Gemini upload fails → stop; do not screenshot-substitute for annotation
- Annotation format unclear → gate answer with format question for operator
