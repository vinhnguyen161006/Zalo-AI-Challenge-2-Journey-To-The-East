# Model parameter report

Status: TWO_TARGET_CHECKPOINTS_INSTALLED / LOCAL_GGUF_AUDIT / TARGET_GPU_UNVERIFIED.

## Current two-checkpoint configuration (2026-11-11)

`configs/qwen35.yaml` uses Qwen3.5 4B for Planner/PandasCoder and Qwen3.5 9B
for SQLCoder/repair. Both are Q4_K_M and run through local Ollama. The 3B
checkpoint described below is historical and is not loaded by this config.

| Tensor component | Stored tensor elements |
|---|---:|
| 4B text | 4,205,751,296 |
| 4B projector | 333,514,240 |
| 9B text | 8,953,803,264 |
| 9B projector | 456,010,480 |
| Total | **13,949,079,280** |

`scripts/audit_ollama.py` verified every selected blob's size/SHA-256 and counted
logical tensor shapes from GGUF headers, without torch/CUDA. Report:
`outputs/qwen35-tensor-audit.json`. Shared content hashes are counted once.
This is below 15 billion stored elements, not an organizer accounting certification
or a reconstruction of omitted/tied/pre-quantization tensors.

Publisher license is Apache-2.0 for [4B](https://huggingface.co/Qwen/Qwen3.5-4B)
and [9B](https://huggingface.co/Qwen/Qwen3.5-9B).
The [official release news](https://github.com/QwenLM/Qwen3.8#news) records
2026-03-02, before the supplied 2026-10-26 cutoff.

Windows API revisions:
- 4B: `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`
- 9B: `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90`

Exported manifest revisions for Linux:
- 4B: `e0b8c85c75aad2573ff49bec177ebb1013772fba665a53d64c04266439a2222d`
- 9B: `85879493124988ce76ff36a6ac583504cc8d893727db6a0c4f9992a079e6ca49`

Only these two checkpoints were exported to Git-ignored `models/qwen35-export/`.
No fine-tuning or auxiliary model was used. Calibration code uses empirical
counts, not another neural model. The two-model image has not been built.

## Historical 3B baseline

The Windows baseline shares one `qwen2.5-coder:3b-instruct` Q4_K_M checkpoint
across Planner, SQLCoder and PandasCoder; there are no embedding/reranker models.
Ollama reports digest
`f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225`.
The [publisher model card](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct)
states 3.09B parameters and **qwen-research** license, not Apache-2.0.
This is published model metadata, not an organizer eligibility certification.
Competition use must satisfy that license before freezing a final solution.

`scripts/export_ollama.py` exported this model and its six referenced blobs to
`models/ollama-export/`, verifying source sizes and SHA-256. The exported manifest
SHA-256 is `ce40a49b0aa2b3b607d26a603e0f2db06a256cecbc9cca7adf893ae11a80275a`;
this storage-manifest digest differs from the revision reported by Windows Ollama.
Linux Ollama 0.40.16 reports the exported manifest digest above. The Docker config
`configs/ollama-docker.yaml` pins that exact revision; Windows retains its own pin.
The digest check remains enabled in both environments. Exported blobs are unchanged.
Weights and exports remain Git-ignored. No training or fine-tuning was performed.

At the earlier 3B baseline stage, no target 4B/9B weights were present. An existing
Qwen2.5-Coder 3B Q4_K_M checkpoint in the user's Ollama store was used for local
development; this implementation did not download it or certify its parameter count.
Nominal model names in configs/models.yaml are architecture targets, not measured counts.
The target roles are Planner + PandasCoder on the shared 4B checkpoint and
SQLCoder + repair/critic on the shared 9B checkpoint. Role names do not add models.
The safetensors header auditor is tested on tiny generated multi-shard checkpoints;
it counts stored tensor elements, validates shape/byte offsets and detects duplicate
tensor names or missing index entries. It does not infer original parameter counts
from packed quantization or certify tied/auxiliary/LoRA accounting.

Pin model revision/checkpoint hashes and organizer accounting policy before
reporting <=15B or activating the two-model deployment. Keep fallback single-9B
as a manifest choice only until its own weights have been audited.
