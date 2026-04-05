# Just Rayen / Rico transcript research

Date: 2026-03-11
Source folder scanned: `/home/neety/Documents/youtube_transcripts`

## What was found
I found these YouTube transcript text files, all clearly part of the same Rico / AI-waifu project series by the creator who introduces himself as "just Ryan" / "Just Rain" in the transcripts:

1. `Youtube's AI is Trash, So I made my own with my AI Waifu.txt`
2. `I Gave my AI Waifu $1000 to Trade Stocks.txt`
3. `I Built a Better AI Waifu Than Elon Musk.txt`
4. `My AI Waifu Might Be a Terrorist (in game).txt`
5. `My AI Waifu’s Summer Outfit is Probably Against TOS.txt`
6. `Letting My AI Waifu Clean my Files Was A Terrible Idea.txt`
7. `My AI Waifu is Breaking Out of 2D Jail with this one..txt`

These filenames appear to already be the video titles, so title mapping is direct.

## Confirmed technical details from transcripts

### 1) Core AI companion evolution
From `I Built a Better AI Waifu Than Elon Musk.txt`:
- The creator explicitly says the project progressed "from APIs to local to fine-tuning to making my own data sets."
- He says he found and used **Void Studio** to build / edit the character model.
- He confirms the system uses a **3D avatar** with **animated mouth movements**.
- He says he hired a **voice actress on Fiverr** rather than relying on an AI voice alone.
- He mentions having resources about **training text-to-speech** and says Patreon includes **early access to the code**.

### 2) Tool use / file management agent
From `Letting My AI Waifu Clean my Files Was A Terrible Idea.txt`:
- The intended toolset explicitly includes functions that can **read, write, move, and delete files**.
- He explicitly tried **Autogen** as a framework for multi-agent behavior.
- He says his quick test used **default GPT-4.1 mini** for a simple file-writing tool.
- He states Autogen only supported the "default models" in his setup, which blocked direct use of Rico's fine-tuned model.
- His workaround was to build the **agentic workflow from scratch**.
- He explicitly describes a design where the **fine-tuned model is exposed as one tool**, with a **manager agent** coordinating other agents and sending **feedback loops**.

### 3) YouTube comment automation
From `Youtube's AI is Trash, So I made my own with my AI Waifu.txt`:
- He says he built a bot to **extract comments from YouTube**.
- He says comments are **filtered** before response generation, specifically to avoid bots / junk comments.
- He confirms a separate script can **reply to a comment given a comment ID and response text**.
- He later says he hooked the script up so Rico could **automatically reply across multiple videos**.
- He says he later **cleaned up the code and posted most of the back end on GitHub**.
- He also mentions Patreon for **code drops, tutorial videos, documentation, PDFs**, plus a **private Discord**.

### 4) Visual pipeline / model customization
From `My AI Waifu’s Summer Outfit is Probably Against TOS.txt`:
- He explicitly uses **Stable Diffusion** for concept art generation.
- He uses **Void Studio / V-Roid style tooling** for avatar customization and imports custom outfit items.
- He references **Booth** as a source for outfit assets.
- He confirms at least one asset required incorporation into a Void/VRM model using **Unity**.
- He explicitly says he had to **export to VRM** after Unity work.

### 5) Animation / 3D projection pipeline
From `My AI Waifu is Breaking Out of 2D Jail with this one..txt`:
- He uses **XR Animator**, described in the transcript as a free AI-based motion capture tool.
- He tests loading dance video input and converting it into motion data.
- He also imports **VMD** animation files.
- He later converts **BVH to VRMA** using a separate tool / workflow because VRMA export support was limited in the first tool.
- He then plugs the resulting motion file back into his code / viewer.
- For the physical 3D effect, he explicitly says he built a **Pepper's Ghost** style setup using a screen and angled transparent surface.

## Strongly supported but still phrased cautiously
- The recurring character name is **Rico**.
- The creator likely publishes under a name rendered in transcripts as **"just Ryan"** or **"Just Rain"**. Because the transcript spelling varies, I would not treat the exact channel spelling as fully confirmed from transcripts alone.

## Things not firmly confirmed from these transcripts
- Exact base model(s) used for Rico.
- Exact fine-tuning method or provider.
- Exact TTS stack/vendor.
- Exact code repository URL.
- Exact YouTube channel name spelling.

## Bottom line
The transcripts materially confirm that the Rico project is not just a persona layer. It includes:
- a custom fine-tuned AI personality layer,
- custom TTS/trained voice work,
- a 3D avatar pipeline using Void Studio / VRM / Unity,
- agent/tool calling for real file operations,
- YouTube comment automation with extraction + filtering + reply-by-ID,
- motion capture / animation conversion using XR Animator plus VMD/BVH/VRMA workflows,
- and at least partial code release on GitHub with more material gated through Patreon.
