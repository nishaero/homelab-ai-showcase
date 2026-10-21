# Local LLM serving with vLLM

A compose file that serves a quantized 32 billion parameter model through an OpenAI compatible
API on an AMD Strix Halo machine (integrated GPU, shared memory), using the ROCm build of vLLM.

## What is configured

- **Image:** `rocm/vllm-dev` with ROCm 7.2, PyTorch 2.9 and vLLM 0.14.0rc0.
- **Model:** `Qwen/Qwen2.5-Coder-32B-Instruct-AWQ`, 4 bit AWQ quantization (`--quantization awq`,
  `VLLM_USE_TRITON_AWQ=1`).
- **GPU access:** `/dev/kfd` and `/dev/dri` passed into the container, and
  `HSA_OVERRIDE_GFX_VERSION=11.5.1` so ROCm accepts the integrated GPU.
- **Memory:** `--gpu-memory-utilization 0.45` caps vLLM at 45 percent of the GPU memory, a 16k
  context (`--max-model-len`), 16 GB of CPU swap space and `--enforce-eager`.
- **Model cache:** a named volume for the Hugging Face cache, so a restart does not download the
  model again.

## Status

It was deployed and served the model on the workstation. It is stopped now: the agents in this
repository use Ollama, which is simpler to run next to the other services. The file is kept as a
working reference for the vLLM and ROCm setup.

## Notes

- `privileged: true` and `seccomp:unconfined` are used because the ROCm container needs device
  access. On a shared machine, narrow them down if your setup allows it.
- No benchmark numbers are published here, because none were recorded in a repeatable way.
