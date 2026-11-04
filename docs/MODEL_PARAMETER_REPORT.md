# Model parameter report

Status: NOT_AUDITED / BLOCKED_BY_GPU_WEIGHTS.

No model weights are present or downloaded by this implementation. Nominal model
names in configs/models.yaml are architecture targets, not measured counts.
The safetensors header auditor is tested on tiny generated multi-shard checkpoints;
it counts stored tensor elements, validates shape/byte offsets and detects duplicate
tensor names or missing index entries. It does not infer original parameter counts
from packed quantization or certify tied/auxiliary/LoRA accounting.

Pin model revision/checkpoint hashes and organizer accounting policy before
reporting <=15B or activating the two-model deployment. Keep fallback single-9B
as a manifest choice only until its own weights have been audited.
