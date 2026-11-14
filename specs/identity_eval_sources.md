# Identity test set v2: sources

Version 1 (`src/gamma/evals/identity_cases.json`) was written by a model from
Shana's persona files. Version 2 swaps in real public chat wherever a public set
fits. It keeps Gamma-specific prompts where none does: tool and format turns,
plus false facts and break-character attempts about Shana herself.

Build and run:

```bash
~/Documents/gamma-datasets/.venv-prep/bin/python -I src/gamma/evals/build_identity_v2.py \
    ~/Documents/gamma-datasets/raw data/evals/identity_cases_v2.json
python -m gamma.evals.identity --run --cases data/evals/identity_cases_v2.json
```

The built file holds other people's messages, so it stays in git-ignored
`data/evals/` and is never committed. The repo is public. Every case records
its dataset, licence and row reference.

| Category | Source | Licence | What was taken |
|---|---|---|---|
| casual, AI probes, emotional | [WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M), Allen Institute for AI | ODC-BY 1.0 | First user turns, filtered as below; 5 of 14 shards scanned |
| false facts | [SycophancyEval](https://huggingface.co/datasets/meg-tong/sycophancy-eval), Sharma et al. 2023, "Towards Understanding Sycophancy in Language Models" | MIT | `answer.jsonl` prompts where the user asserts a wrong answer |
| breaking character | [deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections) | Apache-2.0 | Injection-labelled English rows |
| stream chat | TwitchChat, Ringer, Nicolaou & Walker 2020, AIIDE ([copy](https://huggingface.co/datasets/Michielo/twitchchat)) | CC BY 4.0 | Short messages aimed at the streamer; 1 of 3 shards |
| stream chat | [Differentiable/processed_chats](https://huggingface.co/datasets/Differentiable/processed_chats): VTuber chat with usernames masked | BigScience OpenRAIL-M | Lines naming the streamer, with her name changed to Shana |

WildChat filters:
- **Kept:** English, not toxic, not redacted, 2–30 words, one line.
- **Dropped:** requests for tasks (code, essays, translations and similar), role-play scripts, lines about ChatGPT, medical-symptom questions, links, and anything about self-harm or sex.

Considered and not used:
- [oshichats-v2](https://huggingface.co/datasets/pykeio/oshichats-v2): needs a Hugging Face login.
- [JailbreakBench](https://huggingface.co/datasets/JailbreakBench/JBB-Behaviors): it tests harmful requests, not character.
- EmpatheticDialogues: non-commercial licence.

Known limits:
- Real data is noisy, so a few "casual" lines are still small tasks.
- WildChat has few sincere "are you an AI" openers. About a third of the AI probes and a few emotional prompts are still v1.
- Tool and format scores are not comparable with live use, because evaluation mode turns off tool inference.
