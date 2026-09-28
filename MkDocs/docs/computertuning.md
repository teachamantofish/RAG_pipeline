---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/computertuning.md
type: reference
title: computertuning
name: computertuning
description: '-'
resource: /topomorph/apps/rag-master/MkDocs/docs/computertuning.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 1346
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
## Tune Hardware


### OS Optimization
- Power Plan: High Performance or Ultimate Performance
- BIOS: Enable Precision Boost Overdrive or s (Verify!)
-  Use a PCIe Gen4 NVMe SSD to preload models faster and minimize swap/IO lag.
-  Disable background processes, indexing
-  Write script to stop Windows Defender on model directories, print spooler, MS Defender
-  

### CPU Optimization
- Set CPU affinity: Pin the process to high-performance cores only using taskset (Linux) or CPU affinity tools (Windows) to reduce thread contention.

### RAM
- Verify DDR5 in dual-channel config
- BIOS recognizes and maps full memory to OS
- 96 officially supported; some users report 128GB working
- Notes on 128-256: 
  - BIOS version – early BIOS builds lacked the proper SPD tables. Update to the latest 2025-04-xx or newer firmware (changelog usually says “Improve memory compatibility”).
  - Module layout – pick 1 Rx8 (eight-chip) sticks if possible; very dense 2 Rx16 kits sometimes fall back to 5200 MT/s or won’t train at all.
  - Voltage and timings – stay with JEDEC 5600 MT/s, CL40 or CL46. Overclocked EXPO/XMP profiles are hit-or-miss.
  - Flash the newest BIOS first (Minisforum Support → UM890 → BIOS).
  - Install the 64 GB × 2 kit; boot straight into firmware setup and verify the full 128 GB is detected.
  - Run MemTest86 for a full pass and a couple of hours of your heaviest workload or a stress suite such as OCCT. If you see training failures (three short beeps, endless reboots) drop the speed to 5200 MT/s in BIOS; usually that clears it.
  
#### RAM vs Speed Impact Table

Six strategies (assuming a Qwen 30B Q8 model on CPU with 64 GB RAM as your setup):

Strategy Example RAM Savings Speed Impact

Reduce Context Window --ctx 4096 vs --ctx 8192 ~50% (context memory) Slightly faster
Sliding Window Keep last 4K tokens only Large (old cache freed) None to low
KV Cache Offloading OLLAMA_KV_OFFLOAD=1 30–40% (moves cache to CPU/disk) Slower by 20–40%
Reduce Threads OLLAMA_NUM_THREADS=8 ~10–15% Slower (linear to threads)
Rope Scaling --rope-scale 2 No real savings (fake long context) None
Paged Attention --enable-paged-attention (if supported) 20–50% (efficient cache paging) Minimal impact

Top 3 suggestions: 
- Use ctx = 4096
- Sliding window
- KV offload if needed

### Dedicated M.2 SSD for Swap Tuning
- Format it with no journaling (ext4 or NTFS with tuning)
- Set swap priority high, e.g.:
  ```bash
  sudo swapon /mnt/nvme_swapfile --priority=100
  ```
- Don't mix with system/temp files - keep it exclusive to swap
- Use `vm.swappiness=80` for aggressive swap usage during heavy loads
- Format with no journaling (e.g. `ext4 -O ^has_journal`) or xfs
- Disable trim and indexing on swap-only partitions
- **Bonus:** Consider page-locking with `mlockall()` if supported to avoid swap on hot tokens

### Overclock the iGPU (AMD 780M)
Use AMD Adrenalin to:
- Increase iGPU frequency
- Boost shared memory size to max (4-16 GB)
- Overclock iGPU only if you're using OpenCL or Vulkan inference - otherwise skip
- Increase dedicated VRAM
- Set OS CPU governor to performance

## LLM Runtimes
- GGUF required
- llama.cpp (highly optimized)
- llamafile (one-file deployment, good threading)
- ctransformers for Python-based inference with GGUF

## Tune LLM (Parameters - Not Fine Tuning)

**Note:** CPU inference scales almost linearly up to ~8-12 threads for 7B-13B models.

## Ollama tuning
- Update Ollama: Install latest release from ollama.com/download.
- Enable flash attention: setx OLLAMA_FLASH_ATTENTION 1
- Preload model in RAM: Put models on NVMe (default Ollama path: %USERPROFILE%\.ollama\models).
- Reduce context if not needed: ollama run mymodel --ctx 4096
- Use AVX2 build: Ollama already includes it, just keep updated.
- Lock model in memory: Enable Ollama’s memory-mapped load by default; ensure you have enough RAM.

### Max Threads
- Optimize threads: setx OLLAMA_NUM_THREADS 14
- Use more threads: Ensure your inference framework (like llama.cpp or llamafile) is using all available logical cores. On Ryzen 8845HS, that's likely 16 threads (8 cores + SMT): Use `--threads` or `--nthreads` (in llama.cpp or similar) set to: `n_threads = physical_cores`: 
- ./llama -m qwen-30b.q8.gguf -t 16
- Ryzen 9 8845HS has 8 performance cores (16 threads) - so try:
  - `--threads 8` or
  - `--threads 16` (test both)
- Use `--numa` and `--batch_size` to optimize
- enable AVX512

## Model Choices

- LLM designed for CPU usage
- Use gguf models and avoid CUDA
- Use llamafile or exllama-cpp: Faster gguf engines
- Use large-token context models: Works better with high RAM
- Use low quant (Q4_K_M or Q5_K_M): Better CPU performance at same quality

## 
GAIA tuning and usage: AMD software for NPU usage and RAG
rocm tuning and usage: https://www.youtube.com/watch?v=wCBLMXgk3No
determing model size based on available ram and desired context size: https://www.youtube.com/watch?v=wCBLMXgk3No minute 22:48

