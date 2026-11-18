# Workflow Commit Metadata Template

## Required fields
- workflow: <workflow_filename>
- message: <short description of change>
- test_result: approved | rejected | untested
- tester: <who tested, default "cursor-agent">

## Optional fields
- seed: <integer, KSampler seed>
- model_hash: <sha256 of primary checkpoint>
- model: <checkpoint filename>
- output_size: <WxH, e.g. 1024x1536>
- defects_found: <list>
- fix: <what was fixed in this commit>
- pr_url: <GitHub PR URL if applicable>

## Example
- workflow: flux_klein_face_swap_gguf.json
- message: "lower KSampler cfg from 1.5 to 1.0 per FLUX.2 native CFG"
- test_result: approved
- tester: cursor-agent
- seed: 12345
- model: flux-2-klein-9b-Q4_0.gguf
- output_size: 1024x1536
- defects_found: []
- fix: "FLUX models perform best at native CFG=1.0"
